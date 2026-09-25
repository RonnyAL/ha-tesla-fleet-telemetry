"""Helpers for unpacking Tesla `Value` oneofs.

The vehicle's protobuf payload uses one ``Value`` message with a ``oneof``
field whose populated arm depends on the signal.  These helpers narrow the
oneof down to a Python primitive (or ``None`` when the signal carried the
``invalid`` flag or an unset variant).

Keep this module dependency-free except for the protobuf import — sensor and
binary_sensor platforms both consume it.
"""
from __future__ import annotations

import os
import re
from typing import Any

from .proto import vehicle_data_pb2 as vdp


def value_as_float(value: Any) -> float | None:
    """Return a numeric value out of any of the numeric oneof arms."""
    if value.HasField("invalid"):
        return None
    if value.HasField("double_value"):
        return value.double_value
    if value.HasField("float_value"):
        return value.float_value
    if value.HasField("int_value"):
        return float(value.int_value)
    if value.HasField("long_value"):
        return float(value.long_value)
    return None


def value_as_bool(value: Any) -> bool | None:
    """Return a bool from ``boolean_value`` or numeric truthy/falsy fallbacks."""
    if value.HasField("invalid"):
        return None
    if value.HasField("boolean_value"):
        return value.boolean_value
    f = value_as_float(value)
    if f is None:
        return None
    return bool(f)


def value_as_string(value: Any) -> str | None:
    if value.HasField("invalid"):
        return None
    if value.HasField("string_value") and value.string_value:
        return value.string_value
    return None


def value_as_enum_name(value: Any) -> str | None:
    """Return the enum name from whichever enum-typed oneof arm is set.

    Tesla uses many one-off enums (``ShiftState``, ``HvacPowerState``,
    ``DetailedChargeStateValue``, ``SentryModeState``, ``DefrostModeState``,
    ``HvacAutoModeState``, ``ChargingState``, ``FastCharger``, ``CableType``,
    ``DisplayState``).  We don't enumerate them here — instead we use the
    proto's ``WhichOneof`` reflection to find the populated arm and resolve
    its enum descriptor on demand.  Returns the enum value's *name* (e.g.
    ``"DetailedChargeStateCharging"``), the caller is responsible for
    mapping to a friendly string.
    """
    if value.HasField("invalid"):
        return None
    arm = value.WhichOneof("value")
    if arm is None:
        return None
    field = value.DESCRIPTOR.fields_by_name.get(arm)
    if field is None or field.enum_type is None:
        return None
    raw = getattr(value, arm)
    name = field.enum_type.values_by_number.get(int(raw))
    return name.name if name is not None else None


# Friendly mapping for charging state.  Returned as the SENSOR state string
# so the sensor entity can use it directly.
_DETAILED_CHARGE_FRIENDLY = {
    "DetailedChargeStateUnknown": None,
    "DetailedChargeStateDisconnected": "disconnected",
    "DetailedChargeStateNoPower": "no_power",
    "DetailedChargeStateStarting": "starting",
    "DetailedChargeStateCharging": "charging",
    "DetailedChargeStateComplete": "complete",
    "DetailedChargeStateStopped": "stopped",
}


def value_as_charge_state(value: Any) -> str | None:
    name = value_as_enum_name(value)
    if name is None:
        return None
    return _DETAILED_CHARGE_FRIENDLY.get(name, name)


def value_charging_active(value: Any) -> bool | None:
    """True if the car is actively pulling power (Charging or Starting)."""
    name = value_as_enum_name(value)
    if name is None:
        return None
    return name in ("DetailedChargeStateCharging", "DetailedChargeStateStarting")


def value_as_door_state(value: Any) -> dict[str, bool] | None:
    """Decode a ``Doors`` composite into a dict of named bools."""
    if value.HasField("invalid"):
        return None
    if not value.HasField("door_value"):
        return None
    d = value.door_value
    return {
        "DriverFront": d.DriverFront,
        "DriverRear": d.DriverRear,
        "PassengerFront": d.PassengerFront,
        "PassengerRear": d.PassengerRear,
        "TrunkFront": d.TrunkFront,
        "TrunkRear": d.TrunkRear,
    }


# Friendly-name mapping for window state.  ``WindowStateClosed`` → ``"closed"``.
_WINDOW_STATE_FRIENDLY = {
    "WindowStateUnknown": None,
    "WindowStateClosed": "closed",
    "WindowStatePartiallyOpen": "partial",
    "WindowStateOpened": "open",
}


def value_as_window_state(value: Any) -> str | None:
    name = value_as_enum_name(value)
    if name is None:
        return None
    return _WINDOW_STATE_FRIENDLY.get(name, name)


def value_is_window_open(value: Any) -> bool | None:
    """A window is "open" if it isn't fully closed (treats ``partial`` as open)."""
    name = value_as_enum_name(value)
    if name is None:
        return None
    if name == "WindowStateClosed":
        return False
    if name in ("WindowStatePartiallyOpen", "WindowStateOpened"):
        return True
    return None


# ---------------------------------------------------------------------------
# Value helper: any enum arm -> short snake_case name ("SentryModeStateArmed"
# -> "armed", "ChargePortLatchEngaged" -> "engaged"). The prefix is derived
# from the enum's own value names, so it works for every Tesla enum without
# a per-enum table.
# ---------------------------------------------------------------------------
_CAMEL = re.compile(r"(?<!^)(?=[A-Z])")
_PREFIX_CACHE: dict[str, str] = {}


def _enum_prefix(enum_type: Any) -> str:
    cached = _PREFIX_CACHE.get(enum_type.full_name)
    if cached is None:
        names = [v.name for v in enum_type.values]
        cached = os.path.commonprefix(names) if len(names) > 1 else ""
        _PREFIX_CACHE[enum_type.full_name] = cached
    return cached


def value_as_short_enum(value: Any, *, keep_unknown: bool = False) -> str | None:
    """Enum arm as a snake_case name with the enum's common prefix stripped.

    ``None`` means "no usable value": an invalid sample, no oneof arm set, or
    an enum number the vendored proto does not know.

    By default an enum that explicitly reports Unknown/SNA also collapses to
    ``None``, which is right for a sensor — there is nothing to display. It is
    wrong for a binary sensor deriving on/off, where "the car told us it is in
    the Unknown state" is a real answer and distinct from "we could not read
    the sample at all". Those pass ``keep_unknown=True`` and decide for
    themselves.
    """
    if value.HasField("invalid"):
        return None
    arm = value.WhichOneof("value")
    if arm is None:
        return None
    field = value.DESCRIPTOR.fields_by_name.get(arm)
    raw = getattr(value, arm)
    if field is not None and field.enum_type is not None:
        ev = field.enum_type.values_by_number.get(int(raw))
        if ev is None:
            return None
        short = ev.name[len(_enum_prefix(field.enum_type)):] or ev.name
        if short.lower() in ("unknown", "sna") and not keep_unknown:
            return None
        return _CAMEL.sub("_", short).lower()
    if isinstance(raw, str):
        return raw or None
    return str(raw)


def value_as_clock_time(value: Any) -> str | None:
    """Schedule times as "HH:MM".

    Newer firmware sends a Time message; older ones may send minutes after
    midnight or an epoch timestamp, so all three are accepted.
    """
    if value.HasField("invalid"):
        return None
    arm = value.WhichOneof("value")
    if arm is None:
        return None
    if arm == "time_value":
        t = value.time_value
        return f"{t.hour:02d}:{t.minute:02d}"
    raw = getattr(value, arm)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    if raw < 0:
        return None
    if raw < 24 * 60:
        minutes = int(raw)
        return f"{minutes // 60:02d}:{minutes % 60:02d}"
    from homeassistant.util import dt as dt_util

    ts = raw / 1000 if raw > 10**11 else raw
    return dt_util.as_local(dt_util.utc_from_timestamp(ts)).strftime("%H:%M")


def _hvac_running(value: Any) -> bool | None:
    """HVAC is "on" for any state other than Off/Unknown.

    Matches the HvacPowerBinarySensor this restores: it read
    ``name not in ("HvacPowerStateOff", "HvacPowerStateUnknown")``, so an
    explicit Unknown reported *off*. Returning None there instead would leave
    `binary_sensor.<vehicle>_climate` at `unknown`, and any automation or
    dashboard condition testing for `off` would never fire.
    """
    state = value_as_short_enum(value, keep_unknown=True)
    if state is None:
        return None
    return state not in ("off", "unknown", "sna")


def _number_or_enum(value: Any) -> Any:
    """Numeric level if sent as a number, otherwise the short enum name."""
    arm = value.WhichOneof("value")
    if arm in ("int_value", "long_value", "float_value", "double_value"):
        return getattr(value, arm)
    return value_as_short_enum(value)


def _sentry_armed(value: Any) -> bool | None:
    """Armed when the enum reports Armed/Aware/Panic.

    Matches the SentryArmedBinarySensor this restores, whose docstring is
    explicit: "Idle/Off/Unknown are reported as off so the dashboard chip
    lights up only when the car is actively watching its surroundings." It
    also fell back to a plain bool, which older firmware sends instead of the
    enum.
    """
    if value.WhichOneof("value") == "boolean_value":
        return value_as_bool(value)
    state = value_as_short_enum(value, keep_unknown=True)
    if state is None:
        # Not an enum we recognise — older firmware may still have sent a bool.
        return value_as_bool(value)
    return state in ("armed", "aware", "panic")


__all__ = [
    "_enum_prefix",
    "_hvac_running",
    "_number_or_enum",
    "_sentry_armed",
    "value_as_bool",
    "value_as_charge_state",
    "value_as_clock_time",
    "value_as_door_state",
    "value_as_enum_name",
    "value_as_float",
    "value_as_short_enum",
    "value_as_string",
    "value_as_window_state",
    "value_charging_active",
    "value_is_window_open",
]


# Suppress the unused-import warning for vdp — kept for typing/symbol export
# reasons, the proto package being importable here also serves as a quick
# fail-fast if the build is missing the compiled protos.
_ = vdp
