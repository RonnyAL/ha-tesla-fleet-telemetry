"""HA services exposed by tesla_telemetry.

Three services, all keyed by an optional `entry_id` (the integration
auto-resolves when there's exactly one entry configured):

  * ``bootstrap``                — one-time onboarding. Calls
    ``register_partner_domain`` (skippable via flag for accounts already
    registered through another integration) and pushes an initial
    ``set_fleet_telemetry_config`` so the vehicle starts streaming.
  * ``resync_telemetry_config``  — re-pushes the same config. Tesla's
    ``exp`` field is ~30 days. The integration also auto-checks daily
    and re-pushes when the last sync is more than 7 days old, so manual
    invocation is rarely needed.
  * ``dump_public_key``          — emits the EC P-256 public key derived
    from the configured private key, ready to host at the partner
    domain's ``.well-known`` path. Returns the PEM as a service response.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    AUTO_RESYNC_CHECK_INTERVAL_SECONDS,
    AUTO_RESYNC_MAX_AGE_SECONDS,
    CONF_CA_PEM,
    CONF_HOSTNAME,
    CONF_INTERVAL_PRESET,
    CONF_LAST_SYNC_AT,
    CONF_LAST_SYNC_FIELDS_HASH,
    CONF_PARTNER_DOMAIN,
    CONF_PORT,
    CONF_PRIVATE_KEY_PEM,
    CONF_VIN,
    DOMAIN,
    INTERVAL_PRESET_OVERRIDES,
)
from .firmware import evidence_from_entry
from .signals import FieldPolicy, resolve_effective_intervals, resolve_field_policies
from .tesla_api import (
    TelemetryConfig,
    TelemetryFieldConfig,
    TeslaApi,
    TeslaApiError,
)
from .tls_ca import ca_bundle_pem

_LOGGER = logging.getLogger(__name__)

SERVICE_BOOTSTRAP = "bootstrap"
SERVICE_RESYNC = "resync_telemetry_config"
SERVICE_DUMP_PUBLIC_KEY = "dump_public_key"
SERVICE_GET_CONFIG = "get_telemetry_config"
SERVICE_SET_INTERVAL_PRESET = "set_interval_preset"
SERVICE_GET_TELEMETRY_ERRORS = "get_telemetry_errors"

ATTR_ENTRY_ID = "entry_id"
ATTR_CA_PEM = "ca_pem"
ATTR_REGISTER_PARTNER = "register_partner_domain"
ATTR_PRESET = "preset"
ATTR_THIS_VEHICLE_ONLY = "this_vehicle_only"

_BOOTSTRAP_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY_ID): str,
        vol.Optional(ATTR_CA_PEM): str,
        vol.Optional(ATTR_REGISTER_PARTNER, default=True): bool,
    }
)

_RESYNC_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY_ID): str,
        vol.Optional(ATTR_CA_PEM): str,
    }
)

_DUMP_KEY_SCHEMA = vol.Schema({vol.Optional(ATTR_ENTRY_ID): str})

_GET_CONFIG_SCHEMA = vol.Schema({vol.Optional(ATTR_ENTRY_ID): str})

_GET_ERRORS_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY_ID): str,
        # The endpoint is partner-scoped, so it reports every vehicle on the
        # domain. Default to just this entry's car; set false to see them all.
        vol.Optional(ATTR_THIS_VEHICLE_ONLY, default=True): bool,
    }
)

_SET_PRESET_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY_ID): str,
        vol.Required(ATTR_PRESET): vol.In(list(INTERVAL_PRESET_OVERRIDES)),
        vol.Optional(ATTR_CA_PEM): str,
    }
)


def _resolve_entry(hass: HomeAssistant, entry_id: str | None) -> ConfigEntry:
    """Return the entry the service call is targeting."""
    if entry_id:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            raise ServiceValidationError(
                f"unknown tesla_telemetry config entry: {entry_id}"
            )
        return entry
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        raise ServiceValidationError("no tesla_telemetry config entries")
    if len(entries) > 1:
        raise ServiceValidationError(
            "multiple tesla_telemetry entries — pass `entry_id`"
        )
    return entries[0]


def _entry_api(hass: HomeAssistant, entry: ConfigEntry) -> TeslaApi:
    record = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not record or record.get("api") is None:
        raise HomeAssistantError(
            f"entry {entry.entry_id} has no API client — was setup_entry called?"
        )
    return record["api"]


def _resolve_policies(entry: ConfigEntry) -> dict[str, FieldPolicy]:
    """The resolved per-field policy for this entry, firmware gate included."""
    return resolve_field_policies(entry, evidence_from_entry(entry))


def _config_fields(entry: ConfigEntry) -> dict[str, dict[str, Any]]:
    """The serialised ``fields`` object, and the thing that is fingerprinted.

    Fingerprinting the serialised body rather than the intervals is what makes
    an edit to a minimum delta detectable: the comparison has to cover
    everything that is actually sent, or the options listener decides nothing
    changed and never re-pushes.
    """
    return {
        name: TelemetryFieldConfig(
            interval_seconds=policy.interval_seconds,
            minimum_delta=policy.minimum_delta,
            resend_interval_seconds=policy.resend_interval_seconds,
            include_fields=list(policy.include_fields),
        ).to_dict()
        for name, policy in _resolve_policies(entry).items()
    }


def _resolve_ca_pem(
    hass: HomeAssistant, entry: ConfigEntry, call_data: Mapping[str, Any]
) -> str:
    """Resolve the CA bundle for a push, persisting an explicit override.

    A `ca_pem:` on the service call is stored on the entry, because the pushes
    that happen *without* service data — the daily auto-resync and the
    options-change re-push — would otherwise fall back to
    DEFAULT_CA_BUNDLE_PEM and silently swap the vehicle's trust anchor back
    days later.

    Passing an empty `ca_pem:` clears a stored override and returns to the
    default bundle; omitting the key entirely leaves whatever is stored.
    """
    if ATTR_CA_PEM in call_data:
        override = (call_data.get(ATTR_CA_PEM) or "").strip()
        if override != (entry.data.get(CONF_CA_PEM) or ""):
            data = {**entry.data}
            if override:
                data[CONF_CA_PEM] = override
            else:
                data.pop(CONF_CA_PEM, None)
            hass.config_entries.async_update_entry(entry, data=data)
            _LOGGER.info(
                "tesla_telemetry: %s CA bundle override for vin=%s",
                "stored" if override else "cleared",
                entry.data.get(CONF_VIN),
            )
        return ca_bundle_pem(override)
    return entry_ca_pem(entry)


def entry_ca_pem(entry: ConfigEntry) -> str:
    """The CA bundle this entry should push, honouring a stored override."""
    return ca_bundle_pem(entry.data.get(CONF_CA_PEM))


def _build_telemetry_config(entry: ConfigEntry, ca_pem: str) -> TelemetryConfig:
    return TelemetryConfig(
        hostname=entry.data[CONF_HOSTNAME],
        port=int(entry.data[CONF_PORT]),
        ca=ca_pem,
        fields={
            name: TelemetryFieldConfig(
                interval_seconds=policy.interval_seconds,
                minimum_delta=policy.minimum_delta,
                resend_interval_seconds=policy.resend_interval_seconds,
                include_fields=list(policy.include_fields),
            )
            for name, policy in _resolve_policies(entry).items()
        },
    )


def _fields_fingerprint(
    fields: Mapping[str, int | Mapping[str, Any]],
) -> str:
    """Stable digest of a resolved field config.

    Stored rather than the whole mapping: all anyone needs is an equality test
    against what the car was last told, and a digest keeps the config entry
    small.

    Accepts either a plain ``{signal: interval}`` mapping or the serialised
    per-field bodies. A serialised body containing only ``interval_seconds``
    is collapsed back to a bare int, so it digests identically to the plain
    shape — which is what keeps an entry stamped before Phase 4 from looking
    changed the first time it is compared, and what makes a default
    configuration's digest match regardless of which shape produced it.

    Serialised as JSON rather than joined with separators, so that no field
    name can be confused with the delimiters and produce a collision. Tesla's
    Field enum never contains one today, but a digest that silently treats two
    different configs as equal would present as "the car was never updated".
    """
    normalised: dict[str, Any] = {}
    for name, value in fields.items():
        if isinstance(value, int):
            normalised[name] = value
            continue
        body = dict(value)
        normalised[name] = (
            body["interval_seconds"]
            if set(body) == {"interval_seconds"}
            else body
        )
    payload = json.dumps(
        sorted(normalised.items()), separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _stamp_last_sync(
    hass: HomeAssistant,
    entry: ConfigEntry,
    fields: Mapping[str, int | Mapping[str, Any]] | None = None,
) -> None:
    """Record a successful telemetry config push: when, and what.

    The timestamp drives the >7-day auto-resync. The fingerprint is what lets
    a later run tell whether the car is still holding the config we think it
    is — an integration update that adds default signals changes it, so the
    next resync tick pushes instead of waiting for the age to expire.
    """
    new_data = {**entry.data, CONF_LAST_SYNC_AT: int(time.time())}
    if fields is not None:
        new_data[CONF_LAST_SYNC_FIELDS_HASH] = _fields_fingerprint(fields)
    hass.config_entries.async_update_entry(entry, data=new_data)


async def _bootstrap_handler(call: ServiceCall) -> ServiceResponse:
    hass = call.hass
    entry = _resolve_entry(hass, call.data.get(ATTR_ENTRY_ID))
    api = _entry_api(hass, entry)

    response: dict[str, Any] = {"vin": entry.data[CONF_VIN]}

    if call.data.get(ATTR_REGISTER_PARTNER, True):
        domain = entry.data[CONF_PARTNER_DOMAIN]
        try:
            response["partner_register"] = await api.register_partner_domain(
                domain
            )
        except Exception as err:  # noqa: BLE001 — partner reg is best-effort
            _LOGGER.warning(
                "tesla_telemetry: partner_accounts/register failed for %s: %s "
                "(continuing — re-run with register_partner_domain: false if "
                "the partner is already registered through another integration)",
                domain,
                err,
            )
            response["partner_register_error"] = str(err)

    ca_pem = _resolve_ca_pem(hass, entry, call.data)
    cfg = _build_telemetry_config(entry, ca_pem)
    response["telemetry_config"] = await api.set_fleet_telemetry_config(
        entry.data[CONF_VIN], cfg
    )
    _stamp_last_sync(hass, entry, _config_fields(entry))
    _LOGGER.info(
        "tesla_telemetry: bootstrap completed for vin=%s — %s",
        entry.data[CONF_VIN],
        response["telemetry_config"],
    )
    return response


async def _resync_handler(call: ServiceCall) -> ServiceResponse:
    hass = call.hass
    entry = _resolve_entry(hass, call.data.get(ATTR_ENTRY_ID))
    api = _entry_api(hass, entry)
    ca_pem = _resolve_ca_pem(hass, entry, call.data)
    cfg = _build_telemetry_config(entry, ca_pem)
    response = await api.set_fleet_telemetry_config(entry.data[CONF_VIN], cfg)
    _stamp_last_sync(hass, entry, _config_fields(entry))
    _LOGGER.info(
        "tesla_telemetry: resync completed for vin=%s — %s",
        entry.data[CONF_VIN],
        response,
    )
    return {"vin": entry.data[CONF_VIN], "telemetry_config": response}


async def _get_config_handler(call: ServiceCall) -> ServiceResponse:
    """Query Tesla's GET /api/1/vehicles/{vin}/fleet_telemetry_config so the
    user can verify whether the car has synced our pushed config without
    having to fish a bearer token out of `.storage`."""
    hass = call.hass
    entry = _resolve_entry(hass, call.data.get(ATTR_ENTRY_ID))
    api = _entry_api(hass, entry)
    vin = entry.data[CONF_VIN]
    response = await api.get_fleet_telemetry_config(vin)
    _LOGGER.info(
        "tesla_telemetry: get_fleet_telemetry_config vin=%s synced=%s",
        vin,
        response.get("synced"),
    )
    return {"vin": vin, "telemetry_config": response}


async def _get_telemetry_errors_handler(call: ServiceCall) -> ServiceResponse:
    """Query Tesla for errors vehicles reported after receiving the config.

    This is the endpoint that explains a car which accepted the config and
    then never connected — bad hostname, an untrusted certificate chain, a
    TLS or mTLS failure. None of that is visible from Home Assistant
    otherwise: the vehicle simply never opens the WebSocket.

    Partner-scoped, so it needs the partner domain rather than a VIN and
    returns every vehicle on the domain; filtered to this entry's VIN by
    default.
    """
    hass = call.hass
    entry = _resolve_entry(hass, call.data.get(ATTR_ENTRY_ID))
    api = _entry_api(hass, entry)
    vin = entry.data[CONF_VIN]
    domain = entry.data.get(CONF_PARTNER_DOMAIN)
    if not domain:
        raise ServiceValidationError(
            "this entry has no partner domain configured, which the "
            "fleet_telemetry_errors endpoint requires"
        )

    try:
        errors = await api.get_fleet_telemetry_errors(domain)
    except TeslaApiError as err:
        raise HomeAssistantError(
            f"could not fetch telemetry errors for domain {domain}: {err}"
        ) from err

    total = len(errors)
    if call.data.get(ATTR_THIS_VEHICLE_ONLY, True):
        errors = [e for e in errors if e.get("vin") == vin]

    _LOGGER.info(
        "tesla_telemetry: get_telemetry_errors domain=%s vin=%s matched=%d of %d",
        domain,
        vin,
        len(errors),
        total,
    )
    return {
        "vin": vin,
        "partner_domain": domain,
        "error_count": len(errors),
        "total_for_domain": total,
        "errors": errors,
    }


async def _set_interval_preset_handler(call: ServiceCall) -> ServiceResponse:
    """Switch the telemetry interval preset and re-push the config.

    The preset name is persisted in entry.data so a HA restart preserves the
    user's choice. Auto-resync also honours it.
    """
    hass = call.hass
    entry = _resolve_entry(hass, call.data.get(ATTR_ENTRY_ID))
    api = _entry_api(hass, entry)
    preset: str = call.data[ATTR_PRESET]

    new_data = {**entry.data, CONF_INTERVAL_PRESET: preset}
    hass.config_entries.async_update_entry(entry, data=new_data)
    # _build_telemetry_config now reads the just-saved preset.
    entry = hass.config_entries.async_get_entry(entry.entry_id)  # type: ignore[assignment]
    assert entry is not None

    ca_pem = _resolve_ca_pem(hass, entry, call.data)
    cfg = _build_telemetry_config(entry, ca_pem)
    response = await api.set_fleet_telemetry_config(entry.data[CONF_VIN], cfg)
    _stamp_last_sync(hass, entry, _config_fields(entry))
    intervals = resolve_effective_intervals(entry)
    _LOGGER.info(
        "tesla_telemetry: interval preset=%s applied for vin=%s — %s",
        preset,
        entry.data[CONF_VIN],
        response,
    )
    return {
        "vin": entry.data[CONF_VIN],
        "preset": preset,
        "intervals": intervals,
        "telemetry_config": response,
    }


async def _dump_public_key_handler(call: ServiceCall) -> ServiceResponse:
    hass = call.hass
    entry = _resolve_entry(hass, call.data.get(ATTR_ENTRY_ID))
    pem = entry.data.get(CONF_PRIVATE_KEY_PEM, "")
    if not pem.strip():
        raise HomeAssistantError("entry has no private key configured")

    from cryptography.hazmat.primitives import serialization

    try:
        key = serialization.load_pem_private_key(pem.encode(), password=None)
    except Exception as err:
        raise HomeAssistantError(f"could not parse private key: {err}") from err

    pub_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    domain = entry.data.get(CONF_PARTNER_DOMAIN, "<partner-domain>")
    _LOGGER.info(
        "tesla_telemetry: host the following public key at "
        "https://%s/.well-known/appspecific/com.tesla.3p.public-key.pem\n%s",
        domain,
        pub_pem,
    )
    return {"partner_domain": domain, "public_key_pem": pub_pem}


def async_register_services(hass: HomeAssistant) -> None:
    """Register all tesla_telemetry services. Idempotent — safe to call
    on every entry setup."""
    if not hass.services.has_service(DOMAIN, SERVICE_BOOTSTRAP):
        hass.services.async_register(
            DOMAIN,
            SERVICE_BOOTSTRAP,
            _bootstrap_handler,
            schema=_BOOTSTRAP_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_RESYNC):
        hass.services.async_register(
            DOMAIN,
            SERVICE_RESYNC,
            _resync_handler,
            schema=_RESYNC_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_DUMP_PUBLIC_KEY):
        hass.services.async_register(
            DOMAIN,
            SERVICE_DUMP_PUBLIC_KEY,
            _dump_public_key_handler,
            schema=_DUMP_KEY_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_GET_CONFIG):
        hass.services.async_register(
            DOMAIN,
            SERVICE_GET_CONFIG,
            _get_config_handler,
            schema=_GET_CONFIG_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_GET_TELEMETRY_ERRORS):
        hass.services.async_register(
            DOMAIN,
            SERVICE_GET_TELEMETRY_ERRORS,
            _get_telemetry_errors_handler,
            schema=_GET_ERRORS_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )
    if not hass.services.has_service(DOMAIN, SERVICE_SET_INTERVAL_PRESET):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_INTERVAL_PRESET,
            _set_interval_preset_handler,
            schema=_SET_PRESET_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )


# ---------------------------------------------------------------------
# Auto-resync
# ---------------------------------------------------------------------
async def _auto_resync_if_due(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Re-push fleet_telemetry_config when it is stale.

    Stale means either the last successful sync is older than
    ``AUTO_RESYNC_MAX_AGE_SECONDS``, or the resolved field config no longer
    matches what we last pushed — which is what happens when an integration
    update changes the default signal set. Without the second condition those
    new signals were not requested from the vehicle until the age check
    happened to expire, so their entities sat at ``unknown`` for up to a week
    with nothing explaining why.

    Skips silently if the user hasn't bootstrapped yet (no ``last_sync_at``
    recorded) — we don't push a config they haven't authorized.
    """
    last = entry.data.get(CONF_LAST_SYNC_AT)
    if not last:
        return
    age = time.time() - last
    fields = _config_fields(entry)
    stored_hash = entry.data.get(CONF_LAST_SYNC_FIELDS_HASH)
    # An entry stamped before this key existed has no fingerprint. Treat that
    # as "unknown, not stale" and let the age check drive it, rather than
    # pushing for every such entry on the next tick after an update.
    fields_changed = (
        stored_hash is not None and stored_hash != _fields_fingerprint(fields)
    )
    if age < AUTO_RESYNC_MAX_AGE_SECONDS and not fields_changed:
        return
    try:
        api = _entry_api(hass, entry)
    except HomeAssistantError as err:
        _LOGGER.debug("tesla_telemetry: auto-resync skipped — %s", err)
        return
    cfg = _build_telemetry_config(entry, entry_ca_pem(entry))
    try:
        result = await api.set_fleet_telemetry_config(entry.data[CONF_VIN], cfg)
    except Exception as err:  # noqa: BLE001 — never surface from a timer tick
        _LOGGER.warning(
            "tesla_telemetry: auto-resync failed for vin=%s after %ds: %s",
            entry.data[CONF_VIN],
            int(age),
            err,
        )
        return
    _stamp_last_sync(hass, entry, fields)
    _LOGGER.info(
        "tesla_telemetry: auto-resync ok for vin=%s after %ds (%s, %d fields): %s",
        entry.data[CONF_VIN],
        int(age),
        "field config changed" if fields_changed else "age",
        len(fields),
        result,
    )


@callback
def async_schedule_auto_resync(
    hass: HomeAssistant, entry: ConfigEntry
) -> Any:
    """Register the daily resync timer for an entry. Returns the cancel
    callable HA's entry-unload machinery should invoke when the entry
    goes away."""

    async def _tick(now: datetime) -> None:
        await _auto_resync_if_due(hass, entry)

    return async_track_time_interval(
        hass, _tick, timedelta(seconds=AUTO_RESYNC_CHECK_INTERVAL_SECONDS)
    )
