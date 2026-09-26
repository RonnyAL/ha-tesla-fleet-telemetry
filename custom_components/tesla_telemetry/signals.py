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

The full catalog of selectable signals the options flow offers is the 251
reconciled signals in ``signal_metadata.SIGNALS`` — the raw Tesla ``Field``
proto enum is a superset of that (it also carries sentinels like
``Deprecated_1..3`` and ``Experimental_1..15`` that were never reconciled into
metadata) and has no caller here.

The pure resolver (``resolve_field_policies`` and ``resolve_effective_intervals``)
deliberately avoids Home Assistant imports so it can be unit-tested without the
HA test harness; its own dependencies on ``presets``, ``firmware`` and
``signal_metadata`` are imported lazily for the same reason. The options flow
itself (category browsing, per-signal tuning) lives in ``options_flow.py``.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .const import (
    CONF_INTERVAL_PRESET,
    CONF_SIGNAL_OVERRIDES,
    DEFAULT_INTERVALS_SECONDS,
    DEFAULT_NEW_SIGNAL_INTERVAL,
    SIGNAL_INTERVAL_MAX,
    SIGNAL_INTERVAL_MIN,
)

_LOGGER = logging.getLogger(__name__)


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

    Has no production callers as of Phase 4 — the coordinator's staleness map
    and the services module both moved to the gated path
    (``resolve_field_policies``) instead. Kept as a plain, ungated interval
    view because it is correct, tested, and a reasonable thing for a future
    caller (or a test) to want; the interval is never gated on firmware, so
    this needs no evidence.
    """
    return {
        name: policy.interval_seconds
        for name, policy in resolve_field_policies(entry).items()
    }


