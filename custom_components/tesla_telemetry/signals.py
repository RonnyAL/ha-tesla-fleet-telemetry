"""Signal catalog + effective-config resolution for tesla_telemetry.

The set of signals streamed from the vehicle and the full streaming policy for
each — interval, minimum delta, resend interval, include fields — is resolved
here from six layers, lowest to highest precedence:

  1. ``DEFAULT_INTERVALS_SECONDS`` — the built-in default set (const.py).
  2. The active preset's per-category interval (presets.py).
  3. ``entry.options[CONF_SIGNAL_OVERRIDES]`` — per-signal user overrides.
  4. Tesla's required minimum deltas, as a floor under the user's value.
  5. Include-field hygiene: a target must be an enabled, known signal.
  6. The firmware gate — any key the vehicle has not shown it supports is
     removed (firmware.py).

The full catalog of selectable signals is the Tesla ``Field`` proto enum,
enumerated at runtime so it tracks proto updates with no hardcoded list.

The pure resolver (``resolve_field_policies`` and ``resolve_effective_intervals``)
and the catalog (``all_catalog_signals``) deliberately avoid Home Assistant
imports so they can be unit-tested without the HA test harness; the
options-flow schema helpers import voluptuous / HA selectors lazily inside the
functions that need them, and the resolver's own dependencies on ``presets``,
``firmware`` and ``signal_metadata`` are imported lazily for the same reason.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from .const import (
    CONF_INTERVAL_PRESET,
    CONF_SIGNAL_OVERRIDES,
    DEFAULT_INTERVALS_SECONDS,
    DEFAULT_NEW_SIGNAL_INTERVAL,
    SIGNAL_CATEGORIES,
    SIGNAL_INTERVAL_MAX,
    SIGNAL_INTERVAL_MIN,
)

_LOGGER = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def all_catalog_signals() -> tuple[str, ...]:
    """Every selectable Tesla signal name, sorted.

    Sourced from the generated ``Field`` proto enum minus the ``Unknown``
    sentinel (value 0). Cached — the enum never changes at runtime.
    """
    # Lazy import keeps the resolver above free of the protobuf dependency.
    from .proto import vehicle_data_pb2

    field_names = vehicle_data_pb2.Field.keys()
    names = [n for n in field_names if n != "Unknown"]
    return tuple(sorted(names))


@lru_cache(maxsize=1)
def _curated_signals() -> frozenset[str]:
    """Signals that belong to a named category — i.e. the curated set."""
    return frozenset(s for sigs in SIGNAL_CATEGORIES.values() for s in sigs)


def _coerce_interval(value: Any) -> int | None:
    """Parse a form/stored value into a non-negative int, else ``None``.

    Rounds rather than truncates, so a fractional value is never mistaken for
    the interval-0 disable sentinel (see below).
    """
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed < 0:
        return None
    interval = round(parsed)
    if interval == 0 and parsed != 0:
        # A fractional value such as 0.5 rounds down to the interval-0
        # disable sentinel, which would silently turn the signal off. Only a
        # genuine, exact zero disables; anything else that rounds to zero
        # floors to the fastest real interval instead.
        return 1
    return interval


def _coerce_delta(value: Any) -> float | None:
    """Parse a minimum delta, else ``None``.

    Zero is meaningless as a delta — a field always changes by at least
    nothing — so it is read as "unset" rather than pushed as a key that does
    nothing but change the config fingerprint.
    """
    try:
        delta = float(value)
    except (TypeError, ValueError):
        return None
    return delta if delta > 0 else None


@dataclass(frozen=True, slots=True)
class FieldPolicy:
    """How one signal is streamed: everything Tesla accepts per field."""

    interval_seconds: int
    minimum_delta: float | None = None
    resend_interval_seconds: int | None = None
    include_fields: tuple[str, ...] = ()


def _normalise_override(value: Any) -> dict[str, Any] | None:
    """One stored override, in either shape, as a normalised dict.

    ``interval_seconds`` has three distinct states and the distinction is load
    bearing:

    =============  ==========================================================
    ``0``          disabled — removes a default-on signal from the config
    positive int   pinned — this exact interval, overriding any preset
    key absent     enabled, interval inherited from the preset or the default
    =============  ==========================================================

    Without the third state the signal picker and presets would fight: the
    picker would have to store a number, and a stored number is pinned, so
    every signal a user added would permanently ignore every preset.
    """
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        if "interval_seconds" in value:
            interval = _coerce_interval(value["interval_seconds"])
            # An unparseable interval drops to the inherit state rather than
            # discarding the whole override and its other keys.
            if interval is not None:
                result["interval_seconds"] = interval
        delta = _coerce_delta(value.get("minimum_delta"))
        if delta is not None:
            result["minimum_delta"] = delta
        resend = _coerce_interval(value.get("resend_interval_seconds"))
        if resend:
            result["resend_interval_seconds"] = min(resend, SIGNAL_INTERVAL_MAX)
        raw_include = value.get("include_fields")
        if isinstance(raw_include, (list, tuple)):
            # A bare string is iterable too, and would silently explode into
            # its individual characters rather than being rejected — accept
            # only an actual list/tuple. Dedupe while keeping first-seen
            # order, since a repeated name would otherwise duplicate a key in
            # the pushed config and needlessly change the fingerprint.
            seen: set[str] = set()
            ordered: list[str] = []
            for name in raw_include:
                if isinstance(name, str) and name not in seen:
                    seen.add(name)
                    ordered.append(name)
            if ordered:
                result["include_fields"] = tuple(ordered)
        return result
    # The legacy shape: a bare interval.
    interval = _coerce_interval(value)
    if interval is None:
        return None
    return {"interval_seconds": interval}


def signal_overrides(entry: Any) -> dict[str, dict[str, Any]]:
    """The sanitised per-signal overrides stored on ``entry``.

    Home Assistant does not version ``entry.options``, so both the pre-Phase-4
    shape (``{signal: int}``) and the current one (``{signal: {...}}``) are
    accepted permanently. The dict form is written back on the next save.
    """
    options = getattr(entry, "options", None) or {}
    raw = options.get(CONF_SIGNAL_OVERRIDES) or {}
    result: dict[str, dict[str, Any]] = {}
    for name, value in raw.items():
        normalised = _normalise_override(value)
        if normalised is not None:
            result[name] = normalised
    return result


def active_preset(entry: Any) -> str:
    """The preset in force, from options, falling back to data.

    ``set_interval_preset`` used to write ``entry.data``. Options is now the
    single source of truth — two stores for one setting produces the bug where
    the service appears to do nothing — and the old location is still read so
    an entry written by the old service keeps its preset.
    """
    from .presets import normalise_preset

    options = getattr(entry, "options", None) or {}
    if CONF_INTERVAL_PRESET in options:
        return normalise_preset(options[CONF_INTERVAL_PRESET])
    data = getattr(entry, "data", None) or {}
    return normalise_preset(data.get(CONF_INTERVAL_PRESET))


def resolve_field_policies(
    entry: Any, evidence: Any | None = None
) -> dict[str, FieldPolicy]:
    """The ``{signal: FieldPolicy}`` map to push to Tesla for ``entry``.

    Six layers, lowest precedence first: built-in defaults, the preset's
    per-category interval, the user's per-signal overrides, Tesla's required
    deltas, include-field hygiene, and finally the firmware gate — which
    removes any key the vehicle has not demonstrated it can honour.

    ``evidence`` defaults to nothing proven, so a caller that forgets it gates
    rather than opens.
    """
    from .firmware import (
        FLOOR_INCLUDE_FIELDS,
        FLOOR_MINIMUM_DELTA,
        FLOOR_RESEND_INTERVAL,
        FirmwareEvidence,
    )
    from .presets import preset_interval
    from .signal_metadata import SIGNALS

    if evidence is None:
        evidence = FirmwareEvidence()

    overrides = signal_overrides(entry)
    preset = active_preset(entry)

    policies: dict[str, FieldPolicy] = {}
    for name in sorted(set(DEFAULT_INTERVALS_SECONDS) | set(overrides)):
        meta = SIGNALS.get(name)
        if meta is None:
            # A stored override can name a field a proto bump removed. Pushing
            # a name the car does not know is not something Tesla documents a
            # response to, so it is dropped.
            _LOGGER.debug("ignoring override for unknown signal %s", name)
            continue
        override = overrides.get(name, {})
        if override.get("interval_seconds") == 0:
            continue  # disabled

        interval = DEFAULT_INTERVALS_SECONDS.get(name)          # layer 1
        from_preset = preset_interval(preset, name)             # layer 2
        if from_preset is not None:
            interval = from_preset
        pinned = override.get("interval_seconds")               # layer 3
        if pinned:
            interval = pinned
        if interval is None:
            interval = DEFAULT_NEW_SIGNAL_INTERVAL
        interval = max(SIGNAL_INTERVAL_MIN, min(int(interval), SIGNAL_INTERVAL_MAX))

        delta = override.get("minimum_delta")                   # layer 4
        required = meta.minimum_delta_required
        if required is not None and (delta is None or delta < required):
            delta = required

        policies[name] = FieldPolicy(
            interval_seconds=interval,
            minimum_delta=delta,
            resend_interval_seconds=override.get("resend_interval_seconds"),
            include_fields=tuple(override.get("include_fields", ())),
        )

    supports_delta = evidence.supports(FLOOR_MINIMUM_DELTA)
    supports_resend = evidence.supports(FLOOR_RESEND_INTERVAL)
    supports_include = evidence.supports(FLOOR_INCLUDE_FIELDS)

    if not supports_delta:
        for name in list(policies):
            meta = SIGNALS.get(name)
            if meta is not None and meta.minimum_delta_required is not None:
                # Tesla documents that this field never reports at all
                # without a minimum_delta of at least this value. Emitting it
                # anyway would just be a dead entity stuck at "unknown" with
                # nothing to explain why. Proven evidence gets written to
                # entry.data, which triggers a re-push, so the signal
                # reappears on its own once the car demonstrates support —
                # no action needed from the user.
                _LOGGER.debug(
                    "dropping %s: requires minimum_delta but firmware has "
                    "not proven support for it (floor %s)",
                    name,
                    FLOOR_MINIMUM_DELTA,
                )
                del policies[name]

    enabled = set(policies)

    for name, policy in list(policies.items()):
        # Options can be edited in any order, so an include can name a signal
        # that is disabled or gone by the time it resolves.
        include = tuple(
            target
            for target in policy.include_fields
            if target in enabled and target != name
        )
        policies[name] = FieldPolicy(
            interval_seconds=policy.interval_seconds,
            minimum_delta=policy.minimum_delta if supports_delta else None,
            resend_interval_seconds=(
                policy.resend_interval_seconds if supports_resend else None
            ),
            include_fields=include if supports_include else (),
        )
    return policies


def resolve_effective_intervals(entry: Any) -> dict[str, int]:
    """The ``{signal: interval}`` view of the resolved config.

    Kept for the coordinator's staleness map and the services module. The
    interval is never gated on firmware, so this needs no evidence.
    """
    return {
        name: policy.interval_seconds
        for name, policy in resolve_field_policies(entry).items()
    }


def additional_signals(entry: Any) -> list[str]:
    """Overridden signals not part of any curated category — the ones the user
    added from the full catalog. Sorted for stable display."""
    curated = _curated_signals()
    return sorted(s for s in signal_overrides(entry) if s not in curated)


# --------------------------------------------------------------------------
# Options-flow schema helpers.
#
# HA / voluptuous imports are lazy so importing this module (for the resolver
# above) never requires Home Assistant to be installed.
# --------------------------------------------------------------------------
def build_options_schema(entry: Any) -> Any:
    """Voluptuous schema for the options form.

    One collapsible section per curated category (a number field per signal,
    0 = disabled), an "Additional signals" section for catalog signals the
    user has added, an "Add signals" picker over the rest of the catalog, and
    the estimated-cost rate.
    """
    import voluptuous as vol
    from homeassistant import data_entry_flow
    from homeassistant.helpers import selector

    from .const import (
        CONF_COST_PER_MILLION_SIGNALS,
        DEFAULT_COST_PER_MILLION_SIGNALS,
    )

    overrides = signal_overrides(entry)

    def _section(signals: list[str], default_for: Any) -> Any:
        fields: dict[Any, Any] = {}
        for signal in signals:
            current = overrides.get(signal, default_for(signal))
            fields[vol.Optional(signal, default=current)] = vol.All(
                vol.Coerce(int), vol.Range(min=0, max=SIGNAL_INTERVAL_MAX)
            )
        return data_entry_flow.section(vol.Schema(fields), {"collapsed": True})

    schema: dict[Any, Any] = {}
    for category, signals in SIGNAL_CATEGORIES.items():
        schema[vol.Required(category)] = _section(
            signals, lambda s: DEFAULT_INTERVALS_SECONDS.get(s, 0)
        )

    extras = additional_signals(entry)
    if extras:
        schema[vol.Required("additional")] = _section(
            extras, lambda s: DEFAULT_NEW_SIGNAL_INTERVAL
        )

    addable = [
        s
        for s in all_catalog_signals()
        if s not in _curated_signals() and s not in overrides
    ]
    schema[vol.Required("add_signals")] = data_entry_flow.section(
        vol.Schema(
            {
                vol.Optional("signals", default=list): selector.SelectSelector(
                    # ``addable`` is already sorted by all_catalog_signals().
                    selector.SelectSelectorConfig(
                        options=addable,
                        multiple=True,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        custom_value=False,
                    )
                ),
            }
        ),
        {"collapsed": True},
    )

    current_cost = (getattr(entry, "options", None) or {}).get(
        CONF_COST_PER_MILLION_SIGNALS, DEFAULT_COST_PER_MILLION_SIGNALS
    )
    schema[vol.Required("cost")] = data_entry_flow.section(
        vol.Schema(
            {
                vol.Optional(
                    CONF_COST_PER_MILLION_SIGNALS, default=current_cost
                ): vol.All(vol.Coerce(float), vol.Range(min=0)),
            }
        ),
        {"collapsed": True},
    )

    return vol.Schema(schema)


def parse_options_input(entry: Any, user_input: dict[str, Any]) -> dict[str, Any]:
    """Turn submitted options-form values into the stored options dict.

    Only per-signal values that deviate from the default are stored, so an
    untouched form leaves ``signal_overrides`` empty (== default config). A
    value of 0 is a real override that disables a default-on signal. Signals
    chosen in the "Add signals" picker are added at
    ``DEFAULT_NEW_SIGNAL_INTERVAL`` and drop back out if later set to 0.
    """
    from .const import (
        CONF_COST_PER_MILLION_SIGNALS,
        DEFAULT_COST_PER_MILLION_SIGNALS,
    )

    new_overrides: dict[str, int] = {}

    for key in (*SIGNAL_CATEGORIES.keys(), "additional"):
        section = user_input.get(key) or {}
        for signal, value in section.items():
            interval = _coerce_interval(value)
            if interval is None:
                continue
            interval = min(interval, SIGNAL_INTERVAL_MAX)
            default = DEFAULT_INTERVALS_SECONDS.get(signal, 0)
            if interval != default:
                new_overrides[signal] = interval

    add_section = user_input.get("add_signals") or {}
    for signal in add_section.get("signals") or []:
        if signal in DEFAULT_INTERVALS_SECONDS or signal in new_overrides:
            continue
        new_overrides[signal] = DEFAULT_NEW_SIGNAL_INTERVAL

    cost_section = user_input.get("cost") or {}
    cost = cost_section.get(CONF_COST_PER_MILLION_SIGNALS)
    if cost is None:
        cost = (getattr(entry, "options", None) or {}).get(
            CONF_COST_PER_MILLION_SIGNALS, DEFAULT_COST_PER_MILLION_SIGNALS
        )

    return {
        CONF_SIGNAL_OVERRIDES: new_overrides,
        CONF_COST_PER_MILLION_SIGNALS: float(cost),
    }
