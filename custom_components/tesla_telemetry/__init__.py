"""The Tesla Fleet Telemetry custom integration."""
from __future__ import annotations

import logging
from collections.abc import Callable

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import aiohttp_client, config_entry_oauth2_flow
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import (
    CONF_FIRMWARE_EVIDENCE,
    CONF_LAST_SYNC_AT,
    CONF_LAST_SYNC_FIELDS_HASH,
    CONF_PRIVATE_KEY_PEM,
    CONF_PROXY_SECRET,
    CONF_REGION,
    CONF_VEHICLE_NAME,
    CONF_VIN,
    DEFAULT_REGION,
    DOMAIN,
)
from .coordinator import SignalSample, TeslaTelemetryCoordinator, all_signals_topic
from .firmware import at_least, proof_from_signals
from .generic.factory import GenericEntityFactory
from .migration import async_migrate_unique_ids
from .receiver import TeslaTelemetryView
from .services import (
    _fields_fingerprint,
    async_register_services,
    async_schedule_auto_resync,
)
from .signals import resolve_effective_intervals
from .tesla_api import TeslaApi
from .values import value_as_string

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.DEVICE_TRACKER,
    Platform.SENSOR,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up tesla_telemetry from a config entry."""
    domain_data = hass.data.setdefault(DOMAIN, {})

    vin: str = entry.data[CONF_VIN]
    proxy_secret: str = entry.data.get(CONF_PROXY_SECRET, "")
    # Entries created before CONF_VEHICLE_NAME existed fall back to the
    # entry title (minus the " (VIN)" suffix the config flow appends).
    vehicle_name: str = (
        entry.data.get(CONF_VEHICLE_NAME)
        or entry.title.removesuffix(f" ({vin})")
        or vin
    )

    # Resolve the application_credentials-backed OAuth implementation and
    # build the long-lived session HA's framework will refresh through.
    implementation = (
        await config_entry_oauth2_flow.async_get_config_entry_implementation(
            hass, entry
        )
    )
    oauth_session = config_entry_oauth2_flow.OAuth2Session(
        hass, entry, implementation
    )
    # Ensure we hold a fresh access token before the first API call —
    # also surfaces auth errors at setup time rather than mid-bootstrap.
    try:
        await oauth_session.async_ensure_token_valid()
    except aiohttp.ClientResponseError as err:
        if err.status in (400, 401, 403):
            # The refresh token is no longer usable (revoked in the Tesla
            # account, or the chain broken). Raising ConfigEntryAuthFailed is
            # what starts the reauth flow, so the user gets a "Reconfigure"
            # prompt instead of an entry that silently never loads.
            raise ConfigEntryAuthFailed(
                f"Tesla rejected the stored credentials ({err.status}); "
                "re-authorization is required"
            ) from err
        raise ConfigEntryNotReady(
            f"Tesla token refresh failed ({err.status})"
        ) from err
    except (TimeoutError, aiohttp.ClientError) as err:
        raise ConfigEntryNotReady(f"Tesla token refresh failed: {err}") from err

    # ``LocalOAuth2Implementation`` (the standard application_credentials
    # backing) exposes client_id / client_secret directly. We need them
    # for the partner client_credentials grant used by partner_accounts.
    client_id = getattr(implementation, "client_id", "")
    client_secret = getattr(implementation, "client_secret", "")

    coordinator = TeslaTelemetryCoordinator(hass, vin, vehicle_name)
    # Seed the staleness map from the entry's resolved config (defaults +
    # options overrides + preset) so disabled/retuned signals are judged
    # against their configured interval, not the hardcoded default.
    coordinator.effective_intervals = resolve_effective_intervals(entry)

    api = TeslaApi(
        aiohttp_client.async_get_clientsession(hass),
        oauth_session,
        client_id=client_id,
        client_secret=client_secret,
        region=entry.data.get(CONF_REGION, DEFAULT_REGION),
        partner_private_key_pem=entry.data.get(CONF_PRIVATE_KEY_PEM),
    )

    # The HTTP view is registered once per HA instance and routes incoming
    # WS connections to the right coordinator by VIN. Multiple entries
    # (one per vehicle) share the same view.
    coordinators_by_vin: dict[str, TeslaTelemetryCoordinator] = (
        domain_data.setdefault("coordinators_by_vin", {})
    )
    coordinators_by_vin[vin] = coordinator

    if "view" not in domain_data:
        view = TeslaTelemetryView(coordinators_by_vin, proxy_secret)
        hass.http.register_view(view)
        domain_data["view"] = view
        _LOGGER.info(
            "tesla_telemetry: WebSocket view registered at /api/tesla_telemetry/ws"
        )
    elif proxy_secret and proxy_secret != domain_data["view"]._proxy_secret:
        _LOGGER.warning(
            "tesla_telemetry: entry %s has a different proxy secret than the "
            "first entry; the first secret remains in effect",
            entry.entry_id,
        )

    factory = GenericEntityFactory(hass, entry, coordinator)

    domain_data[entry.entry_id] = {
        "coordinator": coordinator,
        "vin": vin,
        "api": api,
        "generic_factory": factory,
    }

    async_register_services(hass)

    # Daily check that re-pushes the telemetry config when it's >7 days old.
    # No-op until the user has run `bootstrap` at least once.
    entry.async_on_unload(async_schedule_auto_resync(hass, entry))

    # Re-push the telemetry config whenever the user edits signals/intervals
    # via the options flow, so changes take effect without waiting a day.
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    if PLATFORMS:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Recreate generic entities already in the registry (e.g. a slow signal
    # like odometer, whose next datum could be hours away), then subscribe
    # the factory to every future sample so new signals get an entity the
    # first time the car actually sends them.
    factory.async_restore_known(er.async_get(hass))
    entry.async_on_unload(
        async_dispatcher_connect(
            hass, all_signals_topic(vin), factory.handle_sample
        )
    )
    # A sample can arrive and be cached between platform setup and this
    # subscription being wired up. Replay what's cached now that the factory
    # is listening; `_created` makes this a no-op for anything already handled.
    factory.replay_cache()

    entry.async_on_unload(
        _async_record_firmware_evidence(hass, entry, coordinator)
    )

    return True


@callback
def _async_record_firmware_evidence(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: TeslaTelemetryCoordinator,
) -> Callable[[], None]:
    """Accumulate firmware evidence from the live stream.

    Two things are recorded. The `Version` signal is the vehicle's own claim,
    which is fast but can overstate — before firmware 2024.44 it reported the
    available update rather than the installed version. Receipt of any signal
    with a documented firmware floor is proof, which can only understate: a
    car cannot send a field its firmware does not have.

    Written to `entry.data`, which is deliberate. An entry update runs the
    options-change listener, and that re-pushes when the fields fingerprint
    changed — so once proof unlocks a key, a config that actually uses it
    reaches the car without the user touching anything. Today that arming is
    conditional: no default signal carries a required `minimum_delta`, and the
    default options set none of `minimum_delta`/`resend_interval_seconds`/
    `include_fields`, so the pushed fingerprint is identical across the whole
    proof ladder until a user opts into one of those per-field keys. Writes
    are therefore kept rare regardless: nothing happens unless the evidence
    actually changed.
    """

    @callback
    def _handle(name: str, sample: SignalSample) -> None:
        stored = dict(entry.data.get(CONF_FIRMWARE_EVIDENCE) or {})
        updated = dict(stored)

        if name == "Version":
            reported = value_as_string(sample.value)
            if reported:
                updated["reported"] = reported

        incoming = proof_from_signals([name])
        if incoming is not None:
            current = stored.get("proven")
            if current is None or at_least(incoming, current):
                updated["proven"] = incoming

        if updated == stored:
            return
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_FIRMWARE_EVIDENCE: updated}
        )
        _LOGGER.debug(
            "tesla_telemetry: firmware evidence for vin=%s is now %s",
            entry.data.get(CONF_VIN),
            updated,
        )

    unsub = async_dispatcher_connect(hass, all_signals_topic(coordinator.vin), _handle)
    # A sample proving firmware support can already be cached on the
    # coordinator before this subscription exists (the window between
    # platform setup and this being wired up). Without this replay, a slow
    # signal's proof would wait for its *next* datum — hours, for some — even
    # though the self-healing behaviour the gate depends on shouldn't wait on
    # a signal that may not arrive again today. `_handle` is idempotent
    # against anything already handled live, since it only writes when the
    # evidence actually changes.
    for name, sample in coordinator.all_samples():
        _handle(name, sample)
    return unsub


async def _async_options_updated(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """React to an options change (per-signal enable/interval edits).

    Refresh the coordinator's staleness map and re-push the telemetry config
    so edits reach the vehicle immediately rather than waiting for the daily
    auto-resync. Entities are not reloaded — the entity set is static, so a
    disabled signal's entity simply stops receiving and goes unavailable.
    """
    record = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not record:
        return
    coordinator: TeslaTelemetryCoordinator = record["coordinator"]

    from .services import (
        _build_telemetry_config,
        _config_fields,
        _stamp_last_sync,
        entry_ca_pem,
    )

    new_fields = _config_fields(entry)
    coordinator.effective_intervals = {
        name: body["interval_seconds"] for name, body in new_fields.items()
    }

    # Skip a redundant push when the effective config is unchanged — e.g. only
    # the cost rate was edited, or this fired from our own last_sync stamp
    # below (which breaks what would otherwise be an update loop).
    #
    # The comparison is against the fingerprint of the config we last actually
    # pushed, persisted on the entry. It used to be against an in-memory value
    # seeded at setup from the freshly resolved config, which assumed the car
    # already held it — so after an update that changed the default signal set
    # the two always matched and the new signals were never pushed.
    if entry.data.get(CONF_LAST_SYNC_FIELDS_HASH) == _fields_fingerprint(
        new_fields
    ):
        return

    # Don't push for an entry that hasn't been bootstrapped/authorized yet; its
    # first bootstrap will push the current config. Record the marker so an
    # unrelated later update doesn't push either.
    if not entry.data.get(CONF_LAST_SYNC_AT):
        return

    api = record.get("api")
    if api is None:
        return

    # entry_ca_pem honours a stored ca_pem override; using the default
    # bundle here would quietly undo a private CA on the next options edit.
    cfg = _build_telemetry_config(entry, entry_ca_pem(entry))
    try:
        result = await api.set_fleet_telemetry_config(entry.data[CONF_VIN], cfg)
    except Exception as err:  # noqa: BLE001 — never raise from an update listener
        _LOGGER.warning(
            "tesla_telemetry: options-change re-push failed for vin=%s: %s",
            entry.data[CONF_VIN],
            err,
        )
        return
    _stamp_last_sync(hass, entry, new_fields)
    _LOGGER.info(
        "tesla_telemetry: options change re-pushed telemetry config for "
        "vin=%s — %s",
        entry.data[CONF_VIN],
        result,
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if PLATFORMS:
        unload_ok = await hass.config_entries.async_unload_platforms(
            entry, PLATFORMS
        )
    else:
        unload_ok = True

    if unload_ok:
        domain_data = hass.data.get(DOMAIN, {})
        record = domain_data.pop(entry.entry_id, None)
        if record is not None:
            domain_data.get("coordinators_by_vin", {}).pop(record["vin"], None)
        # The view + services stay registered: HA does not support
        # unregistering them without a restart. With no entries left, the
        # view's empty routing table will reject any subsequent
        # connections with 403, and the services raise ServiceValidationError.
    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate an older config entry.

    v1 entries used a hand-rolled OAuth flow whose data shape is
    incompatible with HA's OAuth2 framework. Returning False leaves the
    entry in a setup-failed state and prompts the user to re-create it
    (which will go through the new application_credentials flow and obtain
    an independent grant from Tesla).

    v2 -> v3 renames legacy entity unique_ids onto the generic naming rule
    (see migration.py). This runs before async_setup_entry, so the rewrite
    always completes before any generic entity could claim one of the
    target ids.
    """
    if entry.version < 2:
        _LOGGER.error(
            "tesla_telemetry: config entry %s was created against the old "
            "hand-rolled OAuth path (v%d). Delete this entry and re-add the "
            "integration — it now uses HA's application_credentials so it "
            "no longer fights tesla_fleet over the refresh token.",
            entry.entry_id,
            entry.version,
        )
        return False

    if entry.version < 3:
        await async_migrate_unique_ids(hass, entry)
        hass.config_entries.async_update_entry(entry, version=3)

    return True
