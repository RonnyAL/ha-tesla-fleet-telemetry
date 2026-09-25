"""Signals owned by hand-written entities.

A claimed signal gets no generic entity and is not migrated. Three reasons a
signal is claimed:

  fan-out     one signal feeding several entities — DoorState produces every
              DoorBinarySensor from one composite struct
  derived     a value no single signal carries, such as charging-active or
              the plugged-in cable veto
  transform   a conversion the generic path cannot express

Keep this in step with the curated entities: tests/generic/test_claimed.py
fails both when a claim is missing and when one is left behind.

Partition decided in task-1 (.superpowers/sdd/2026-09-25-generic-entities/
task-1-report.md), from instantiating the real platforms against
scripts/capture_legacy_unique_ids.py and tests/fixtures/legacy_unique_ids.json.
`Gear` was proposed but deliberately left OUT: a later task adds an
`enum_labels` override for `ShiftState` so the generic enum path renders
"P"/"R"/"N"/"D" itself, and its unique_id is unchanged by the generic naming
rule (`snake("Gear") == "gear"`).
"""
from __future__ import annotations

CLAIMED_SIGNALS: frozenset[str] = frozenset(
    {
        # fan-out — script-detected (tests/fixtures/legacy_unique_ids.json)
        "DoorState",  # composite struct feeds all 6 DoorBinarySensor instances
        "ChargingCableType",  # feeds ChargingCableTypeSensor (enum) + ChargeCableBinarySensor (presence bool)
        "DetailedChargeState",  # feeds ChargingStateSensor (enum) + ChargingActiveBinarySensor (derived bool)
        "HvacPower",  # feeds the enum sensor + hvac_power_telemetry's derived "running" bool
        "SentryMode",  # feeds the enum sensor + sentry_armed_telemetry's derived bool

        # fan-out — not visible to the script's single-valued _signal_name
        # counter: AvgBatteryTempSensor subscribes to both via
        # async_dispatcher_connect in its own async_added_to_hass, alongside
        # each signal's own direct sensor
        "ModuleTempMax",  # feeds ModuleTempMaxSensor + AvgBatteryTempSensor's mean
        "ModuleTempMin",  # feeds ModuleTempMinSensor + AvgBatteryTempSensor's mean

        # derived — device_tracker entities never set _signal_name, so this
        # is also invisible to the script; leaving these unclaimed would let
        # a generic device_tracker/sensor duplicate the same data
        "Location",  # LocationTracker's only source; matches the generic location->device_tracker mapping exactly
        "DestinationName",  # combines with DestinationLocation to derive RouteTracker's single state
        "DestinationLocation",  # combines with DestinationName to derive RouteTracker's lat/lon and state

        # transform — a conversion the generic path cannot express
        "MinutesToArrival",  # TimeToArrivalSensor: minutes offset -> absolute UTC timestamp anchored on payload_created_at
        "TimeToFullCharge",  # TimeToFullChargeSensor: metadata unit is hours ('h'); curated rounds to minutes
        "FastChargerPresent",  # metadata says boolean; curated resolves enum-or-bool into a friendly charger-type string sensor
        "ScheduledChargingStartTime",  # value_as_clock_time: timestamp/minutes-after-midnight -> "HH:MM" string
        "ScheduledDepartureTime",  # same clock-time transform as ScheduledChargingStartTime
        "HvacSteeringWheelHeatLevel",  # _number_or_enum: numeric level or enum name depending on which arm the car sent
        "Locked",  # LockBinarySensor inverts the raw bool for HA's LOCK device-class semantics (on = unlocked)
        "FdWindow",  # metadata is enum (WindowState); curated collapses closed/partial/open into a binary_sensor
        "FpWindow",  # same enum-to-binary collapse as FdWindow
        "RdWindow",  # same enum-to-binary collapse as FdWindow
        "RpWindow",  # same enum-to-binary collapse as FdWindow
        "TpmsSoftWarnings",  # metadata is enum (TireLocation); curated treats nonzero ordinal as bool-truthy
        "TpmsHardWarnings",  # same enum-as-bool-truthy transform as TpmsSoftWarnings
    }
)
