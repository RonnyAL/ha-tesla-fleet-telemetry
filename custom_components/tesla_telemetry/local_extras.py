"""Local additions: extra telemetry signals + their entities.

Kept in one module so the patch against upstream stays small: const.py merges
LOCAL_EXTRA_INTERVALS / LOCAL_EXTRA_CATEGORIES into the defaults, and the
sensor / binary_sensor platforms each add the entities returned here.

Entity definitions for odometer, charger current/voltage, TPMS, climate
setpoints, HVAC power and sentry are restored from upstream before v0.4.0
(commit c377e5d), with the same unique_id suffixes.
"""
from __future__ import annotations

import os
import re
from typing import Any

# ---------------------------------------------------------------------------
# Signals + default intervals (seconds). Push-on-change still applies: the
# interval is only a ceiling while a value is actually moving.
#
# That is what decides a sensible default here, not the number in isolation. A
# near-static boolean (ValetModeEnabled, LocatedAtHome, SentryMode) costs
# essentially nothing at a 5 s ceiling, because the car only sends it when it
# flips — and the tight ceiling keeps the UI responsive when it does. The
# signals worth spending a long ceiling on are the ones whose value moves
# continuously, where the ceiling binds on every single interval:
#
#   Odometer, EnergyRemaining, ExpectedEnergyPercentAtTripArrival — move
#   continuously while driving, so they are held at 300 s. Odometer's 300 s is
#   also the value it carried upstream before v0.4.0 removed it (c377e5d^).
#   LifetimeEnergy{Used,GainedRegen,ChargedKwh} — monotonic counters that
#   increment throughout every drive and charge. Nothing consumes them at
#   five-minute resolution, so they sit at 3600 s.
# ---------------------------------------------------------------------------
LOCAL_EXTRA_INTERVALS: dict[str, int] = {
    # charging
    "ChargeAmps": 10,
    "ChargerVoltage": 10,
    "ChargeCurrentRequest": 10,
    "ChargerPhases": 60,
    "ChargePortLatch": 5,
    "ChargePortColdWeatherMode": 30,
    "ScheduledChargingPending": 30,
    "PreconditioningEnabled": 5,
    # battery
    "EnergyRemaining": 300,
    "BatteryHeaterOn": 10,
    # climate
    "HvacPower": 5,
    "HvacLeftTemperatureRequest": 10,
    "HvacRightTemperatureRequest": 10,
    "DefrostMode": 5,
    "ClimateKeeperMode": 5,
    "WiperHeatEnabled": 30,
    # body / security
    "SentryMode": 5,
    "GuestModeEnabled": 30,
    "ValetModeEnabled": 30,
    "LocatedAtHome": 5,
    "HomelinkNearby": 5,
    "TpmsPressureFl": 300,
    "TpmsPressureFr": 300,
    "TpmsPressureRl": 300,
    "TpmsPressureRr": 300,
    "TpmsSoftWarnings": 60,
    "TpmsHardWarnings": 60,
    # driving
    "Odometer": 300,
    "ExpectedEnergyPercentAtTripArrival": 300,
    # battery health / lifetime
    "NominalFullPackEnergyKwh": 3600,
    "LifetimeEnergyChargedKwh": 3600,
    "LifetimeEnergyUsed": 3600,
    "LifetimeEnergyGainedRegen": 3600,
    # schedules
    "ScheduledChargingStartTime": 60,
    "ScheduledDepartureTime": 60,
    # cabin
    "CabinOverheatProtectionMode": 30,
    "HvacSteeringWheelHeatLevel": 5,
    "RemoteStartActive": 5,
    # places / oddities
    "LocatedAtWork": 5,
    "LocatedAtFavorite": 5,
    "ServiceMode": 60,
    "LightsHazardsActive": 5,
}

# Each extra signal goes into an existing options-flow section (no new
# translation keys needed).
LOCAL_EXTRA_CATEGORIES: dict[str, list[str]] = {
    "charging": [
        "ChargeAmps",
        "ChargerVoltage",
        "ChargeCurrentRequest",
        "ChargerPhases",
        "ChargePortLatch",
        "ChargePortColdWeatherMode",
        "ScheduledChargingPending",
        "PreconditioningEnabled",
        "ScheduledChargingStartTime",
        "ScheduledDepartureTime",
    ],
    "battery": [
        "EnergyRemaining",
        "BatteryHeaterOn",
        "NominalFullPackEnergyKwh",
        "LifetimeEnergyChargedKwh",
        "LifetimeEnergyUsed",
        "LifetimeEnergyGainedRegen",
    ],
    "climate": [
        "HvacPower",
        "HvacLeftTemperatureRequest",
        "HvacRightTemperatureRequest",
        "DefrostMode",
        "ClimateKeeperMode",
        "WiperHeatEnabled",
        "CabinOverheatProtectionMode",
        "HvacSteeringWheelHeatLevel",
        "RemoteStartActive",
    ],
    "body": [
        "SentryMode",
        "GuestModeEnabled",
        "ValetModeEnabled",
        "LocatedAtHome",
        "HomelinkNearby",
        "TpmsPressureFl",
        "TpmsPressureFr",
        "TpmsPressureRl",
        "TpmsPressureRr",
        "TpmsSoftWarnings",
        "TpmsHardWarnings",
        "LocatedAtWork",
        "LocatedAtFavorite",
        "ServiceMode",
        "LightsHazardsActive",
    ],
    "driving": ["Odometer", "ExpectedEnergyPercentAtTripArrival"],
}


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
    from .values import value_as_bool

    if value.WhichOneof("value") == "boolean_value":
        return value_as_bool(value)
    state = value_as_short_enum(value, keep_unknown=True)
    if state is None:
        # Not an enum we recognise — older firmware may still have sent a bool.
        return value_as_bool(value)
    return state in ("armed", "aware", "panic")


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------
def local_sensor_entities(coordinator: Any) -> list[Any]:
    from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
    from homeassistant.const import (
        PERCENTAGE,
        UnitOfElectricCurrent,
        UnitOfElectricPotential,
        UnitOfEnergy,
        UnitOfLength,
        UnitOfPressure,
        UnitOfTemperature,
    )

    from .sensor import _scalar_sensor

    def enum_sensor(signal: str, suffix: str, name: str) -> Any:
        return _scalar_sensor(
            signal=signal,
            suffix=suffix,
            name=name,
            state_class=None,
            extractor=value_as_short_enum,
        )

    def tpms(signal: str, position: str) -> Any:
        return _scalar_sensor(
            signal=signal,
            suffix=f"tire_pressure_{position}_telemetry",
            name=f"Tire pressure {position.replace('_', ' ')}",
            device_class=SensorDeviceClass.PRESSURE,
            unit=UnitOfPressure.BAR,
            precision=2,
        )

    classes = [
        _scalar_sensor(
            signal="Odometer",
            suffix="odometer_telemetry",
            name="Odometer",
            device_class=SensorDeviceClass.DISTANCE,
            state_class=SensorStateClass.TOTAL_INCREASING,
            unit=UnitOfLength.MILES,
            precision=1,
        ),
        _scalar_sensor(
            signal="ChargeAmps",
            suffix="charge_amps_telemetry",
            name="Charger current",
            device_class=SensorDeviceClass.CURRENT,
            unit=UnitOfElectricCurrent.AMPERE,
            precision=1,
        ),
        _scalar_sensor(
            signal="ChargerVoltage",
            suffix="charger_voltage_telemetry",
            name="Charger voltage",
            device_class=SensorDeviceClass.VOLTAGE,
            unit=UnitOfElectricPotential.VOLT,
            precision=0,
        ),
        _scalar_sensor(
            signal="ChargeCurrentRequest",
            suffix="charge_current_request_telemetry",
            name="Charge current setting",
            device_class=SensorDeviceClass.CURRENT,
            state_class=None,
            unit=UnitOfElectricCurrent.AMPERE,
            precision=0,
        ),
        _scalar_sensor(
            signal="ChargerPhases",
            suffix="charger_phases_telemetry",
            name="Charger phases",
            state_class=None,
            precision=0,
        ),
        _scalar_sensor(
            signal="EnergyRemaining",
            suffix="energy_remaining_telemetry",
            name="Energy remaining",
            device_class=SensorDeviceClass.ENERGY_STORAGE,
            unit=UnitOfEnergy.KILO_WATT_HOUR,
            precision=1,
        ),
        _scalar_sensor(
            signal="HvacLeftTemperatureRequest",
            suffix="hvac_left_temp_request_telemetry",
            name="Climate left setpoint",
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=None,
            unit=UnitOfTemperature.CELSIUS,
            precision=1,
        ),
        _scalar_sensor(
            signal="HvacRightTemperatureRequest",
            suffix="hvac_right_temp_request_telemetry",
            name="Climate right setpoint",
            device_class=SensorDeviceClass.TEMPERATURE,
            state_class=None,
            unit=UnitOfTemperature.CELSIUS,
            precision=1,
        ),
        enum_sensor("HvacPower", "hvac_power_state_telemetry", "Climate state"),
        enum_sensor("SentryMode", "sentry_mode_state_telemetry", "Sentry mode"),
        enum_sensor("DefrostMode", "defrost_mode_telemetry", "Defrost mode"),
        enum_sensor("ClimateKeeperMode", "climate_keeper_mode_telemetry", "Climate keeper mode"),
        enum_sensor("ChargePortLatch", "charge_port_latch_telemetry", "Charge port latch"),
        tpms("TpmsPressureFl", "front_left"),
        tpms("TpmsPressureFr", "front_right"),
        tpms("TpmsPressureRl", "rear_left"),
        tpms("TpmsPressureRr", "rear_right"),
        _scalar_sensor(
            signal="NominalFullPackEnergyKwh",
            suffix="nominal_full_pack_energy_telemetry",
            name="Battery capacity",
            device_class=SensorDeviceClass.ENERGY_STORAGE,
            unit=UnitOfEnergy.KILO_WATT_HOUR,
            precision=1,
        ),
        _scalar_sensor(
            signal="LifetimeEnergyChargedKwh",
            suffix="lifetime_energy_charged_telemetry",
            name="Lifetime energy charged",
            device_class=SensorDeviceClass.ENERGY,
            state_class=SensorStateClass.TOTAL_INCREASING,
            unit=UnitOfEnergy.KILO_WATT_HOUR,
            precision=0,
        ),
        _scalar_sensor(
            signal="LifetimeEnergyUsed",
            suffix="lifetime_energy_used_telemetry",
            name="Lifetime energy used",
            device_class=SensorDeviceClass.ENERGY,
            state_class=SensorStateClass.TOTAL_INCREASING,
            unit=UnitOfEnergy.KILO_WATT_HOUR,
            precision=0,
        ),
        _scalar_sensor(
            signal="LifetimeEnergyGainedRegen",
            suffix="lifetime_energy_regen_telemetry",
            name="Lifetime energy regenerated",
            device_class=SensorDeviceClass.ENERGY,
            state_class=SensorStateClass.TOTAL_INCREASING,
            unit=UnitOfEnergy.KILO_WATT_HOUR,
            precision=0,
        ),
        _scalar_sensor(
            signal="ExpectedEnergyPercentAtTripArrival",
            suffix="arrival_soc_telemetry",
            name="Battery at arrival",
            device_class=SensorDeviceClass.BATTERY,
            unit=PERCENTAGE,
            precision=0,
        ),
        _scalar_sensor(
            signal="ScheduledChargingStartTime",
            suffix="scheduled_charging_start_telemetry",
            name="Scheduled charging start",
            state_class=None,
            extractor=value_as_clock_time,
        ),
        _scalar_sensor(
            signal="ScheduledDepartureTime",
            suffix="scheduled_departure_telemetry",
            name="Scheduled departure",
            state_class=None,
            extractor=value_as_clock_time,
        ),
        enum_sensor(
            "CabinOverheatProtectionMode",
            "cabin_overheat_protection_telemetry",
            "Cabin overheat protection",
        ),
        _scalar_sensor(
            signal="HvacSteeringWheelHeatLevel",
            suffix="steering_wheel_heat_level_telemetry",
            name="Steering wheel heat level",
            state_class=None,
            precision=0,
            extractor=_number_or_enum,
        ),
    ]
    return [cls(coordinator) for cls in classes]


def local_binary_sensor_entities(coordinator: Any) -> list[Any]:
    from homeassistant.components.binary_sensor import BinarySensorDeviceClass

    from .binary_sensor import _bool_binary_sensor

    classes = [
        _bool_binary_sensor(
            signal="HvacPower",
            slug="hvac_power_telemetry",
            name="Climate",
            device_class=BinarySensorDeviceClass.RUNNING,
            extractor=_hvac_running,
        ),
        _bool_binary_sensor(
            signal="SentryMode",
            slug="sentry_armed_telemetry",
            name="Sentry armed",
            device_class=BinarySensorDeviceClass.SAFETY,
            extractor=_sentry_armed,
        ),
        _bool_binary_sensor(
            signal="PreconditioningEnabled",
            slug="preconditioning_enabled_telemetry",
            name="Preconditioning",
        ),
        _bool_binary_sensor(
            signal="BatteryHeaterOn",
            slug="battery_heater_telemetry",
            name="Battery heater",
            device_class=BinarySensorDeviceClass.HEAT,
        ),
        _bool_binary_sensor(
            signal="ChargePortColdWeatherMode",
            slug="charge_port_cold_weather_mode_telemetry",
            name="Charge port cold weather mode",
        ),
        _bool_binary_sensor(
            signal="ScheduledChargingPending",
            slug="scheduled_charging_pending_telemetry",
            name="Scheduled charging pending",
        ),
        _bool_binary_sensor(
            signal="WiperHeatEnabled",
            slug="wiper_heat_telemetry",
            name="Wiper heater",
            device_class=BinarySensorDeviceClass.HEAT,
        ),
        _bool_binary_sensor(
            signal="GuestModeEnabled",
            slug="guest_mode_telemetry",
            name="Guest mode",
        ),
        _bool_binary_sensor(
            signal="ValetModeEnabled",
            slug="valet_mode_telemetry",
            name="Valet mode",
        ),
        _bool_binary_sensor(
            signal="LocatedAtHome",
            slug="located_at_home_telemetry",
            name="At home",
            device_class=BinarySensorDeviceClass.PRESENCE,
        ),
        _bool_binary_sensor(
            signal="HomelinkNearby",
            slug="homelink_nearby_telemetry",
            name="HomeLink nearby",
            device_class=BinarySensorDeviceClass.PRESENCE,
        ),
        _bool_binary_sensor(
            signal="TpmsSoftWarnings",
            slug="tpms_soft_warning_telemetry",
            name="Tire pressure warning",
            device_class=BinarySensorDeviceClass.PROBLEM,
        ),
        _bool_binary_sensor(
            signal="TpmsHardWarnings",
            slug="tpms_hard_warning_telemetry",
            name="Tire pressure critical",
            device_class=BinarySensorDeviceClass.PROBLEM,
        ),
        _bool_binary_sensor(
            signal="LocatedAtWork",
            slug="located_at_work_telemetry",
            name="At work",
            device_class=BinarySensorDeviceClass.PRESENCE,
        ),
        _bool_binary_sensor(
            signal="LocatedAtFavorite",
            slug="located_at_favorite_telemetry",
            name="At favorite location",
            device_class=BinarySensorDeviceClass.PRESENCE,
        ),
        _bool_binary_sensor(
            signal="ServiceMode",
            slug="service_mode_telemetry",
            name="Service mode",
        ),
        _bool_binary_sensor(
            signal="LightsHazardsActive",
            slug="hazard_lights_telemetry",
            name="Hazard lights",
            device_class=BinarySensorDeviceClass.LIGHT,
        ),
        _bool_binary_sensor(
            signal="RemoteStartActive",
            slug="remote_start_active_telemetry",
            name="Remote start active",
            device_class=BinarySensorDeviceClass.RUNNING,
        ),
    ]
    return [cls(coordinator) for cls in classes]
