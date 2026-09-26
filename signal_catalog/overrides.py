"""Hand-maintained units and device/state classes for telemetry signals.

Tesla publishes a type for each signal but not a unit; units appear only
inside prose descriptions, and inconsistently. They are therefore never
parsed — every unit here was entered deliberately.

Values are plain strings, not Home Assistant constants, so the generator
depends on nothing but the standard library. Phase 3 maps them to
`UnitOfEnergy.KILO_WATT_HOUR` and friends when it builds entities, where
Home Assistant is already imported.

Entries marked `# teslemetry` were cross-referenced from Home Assistant's
`teslemetry` integration, Apache-2.0, Copyright the Home Assistant
authors. See signal_catalog/README.md.

Signals with no entry get no unit. For the ~26 dimensionless ones —
ChargerPhases, SeatHeaterLeft (a 0-3 level), MediaAudioVolume,
NumBrickVoltageMax (a brick index) — that is the correct answer, not a gap.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Override:
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    # Display labels for an enum signal, keyed by the proto member name.
    # Without one, members are stripped and snake_cased. With one, these
    # values are both the mapping and the HA `options` list — which is what
    # keeps Gear reporting "P" rather than "p" after genericization.
    enum_labels: dict[str, str] | None = None
    # Minimum-delta advice, transcribed by hand from Tesla's field
    # descriptions. Four senses, four fields:
    #
    #   minimum_delta_required     the field does not report at all without it
    #   minimum_delta_default      the value the car already applies itself
    #   minimum_delta_recommended  Tesla advises one but sets no value
    #   minimum_delta_supported    Tesla says a delta is possible but gives no
    #                              advice either way
    #
    # The generator fails if a description mentions a minimum delta and none of
    # these is set, so a new case cannot pass unnoticed.
    minimum_delta_required: float | None = None
    minimum_delta_default: float | None = None
    minimum_delta_recommended: bool = False
    minimum_delta_supported: bool = False


OVERRIDES: dict[str, Override] = {
    'ACChargingEnergyIn': Override('kWh', 'energy', 'total_increasing'),  # teslemetry
    'ACChargingPower': Override('kW', 'power', 'measurement'),  # teslemetry
    'BMSState': Override(None, 'enum', None),  # teslemetry
    'BatteryLevel': Override('%', 'battery', 'measurement'),  # teslemetry
    'BrakePedalPos': Override('%', None, 'measurement'),  # teslemetry
    'BrickVoltageMax': Override('V', 'voltage', 'measurement'),  # teslemetry
    'BrickVoltageMin': Override('V', 'voltage', 'measurement'),  # teslemetry
    'ChargeAmps': Override('A', 'current', 'measurement'),  # teslemetry
    # "The requested amps to charge the vehicle."
    'ChargeCurrentRequest': Override('A', 'current', 'measurement'),
    # "...as a percentage of battery capacity." No device_class/state_class:
    # this is a configured limit, not a live reading, and the curated
    # ChargeLimitSocSensor it replaces deliberately carried neither — a
    # measurement state_class would pull a rarely-changing setting into
    # long-term statistics for no reason.
    'ChargeLimitSoc': Override('%', None, None),
    # No unit/state_class override needed (boolean, no metadata entry
    # otherwise): device_class matches the curated ChargePortBinarySensor
    # it replaces.
    'ChargePortDoorOpen': Override(None, 'opening', None),
    'ChargeRateMilePerHour': Override('mph', 'speed', 'measurement'),  # teslemetry
    # "It is recommended to set minimum_delta... Beginning with firmware
    # version 2025.2.6, minimum_delta is set to 0.3 by default."
    'ChargerVoltage': Override(
        'V', 'voltage', 'measurement',  # teslemetry
        minimum_delta_default=0.3, minimum_delta_recommended=True,
    ),
    'ChargingCableType': Override(None, 'enum', None),  # teslemetry
    # NOT teslemetry's UnitOfTime.SECONDS/DURATION: Tesla documents this as
    # type 'enum' with proto enum FollowDistance, whose members are
    # FollowDistance1..FollowDistance7 — the 1-7 setting in vehicle controls,
    # not a time. A duration unit here would be meaningless.
    'CruiseFollowDistance': Override(None, 'enum', None),
    'CruiseSetSpeed': Override('mph', 'speed', 'measurement'),  # teslemetry
    'CurrentLimitMph': Override('mph', 'speed', 'measurement'),  # teslemetry
    'DCChargingEnergyIn': Override('kWh', 'energy', 'total_increasing'),  # teslemetry
    'DCChargingPower': Override('kW', 'power', 'measurement'),  # teslemetry
    'DetailedChargeState': Override(None, 'enum', None),  # teslemetry
    'DiHeatsinkTF': Override('°C', 'temperature', 'measurement'),  # teslemetry
    'DiHeatsinkTR': Override('°C', 'temperature', 'measurement'),  # teslemetry
    'DiHeatsinkTREL': Override('°C', 'temperature', 'measurement'),  # teslemetry
    'DiHeatsinkTRER': Override('°C', 'temperature', 'measurement'),  # teslemetry
    'DiInverterTF': Override('°C', 'temperature', 'measurement'),  # teslemetry
    'DiInverterTR': Override('°C', 'temperature', 'measurement'),  # teslemetry
    'DiInverterTREL': Override('°C', 'temperature', 'measurement'),  # teslemetry
    'DiInverterTRER': Override('°C', 'temperature', 'measurement'),  # teslemetry
    'DiMotorCurrentF': Override('A', 'current', 'measurement'),  # teslemetry
    'DiMotorCurrentR': Override('A', 'current', 'measurement'),  # teslemetry
    'DiMotorCurrentREL': Override('A', 'current', 'measurement'),  # teslemetry
    'DiMotorCurrentRER': Override('A', 'current', 'measurement'),  # teslemetry
    'DiStateF': Override(None, 'enum', None),  # teslemetry
    'DiStateR': Override(None, 'enum', None),  # teslemetry
    'DiStateREL': Override(None, 'enum', None),  # teslemetry
    'DiStateRER': Override(None, 'enum', None),  # teslemetry
    'DiStatorTempF': Override('°C', 'temperature', 'measurement'),  # teslemetry
    'DiStatorTempR': Override('°C', 'temperature', 'measurement'),  # teslemetry
    'DiStatorTempREL': Override('°C', 'temperature', 'measurement'),  # teslemetry
    'DiStatorTempRER': Override('°C', 'temperature', 'measurement'),  # teslemetry
    'DiVBatF': Override('V', 'voltage', 'measurement'),  # teslemetry
    'DiVBatR': Override('V', 'voltage', 'measurement'),  # teslemetry
    'DiVBatREL': Override('V', 'voltage', 'measurement'),  # teslemetry
    'DiVBatRER': Override('V', 'voltage', 'measurement'),  # teslemetry
    # device_class matches the curated UserPresentBinarySensor it replaces.
    'DriverSeatOccupied': Override(None, 'occupancy', None),
    'EnergyRemaining': Override('kWh', 'energy_storage', 'measurement'),  # teslemetry
    'EstBatteryRange': Override('mi', 'distance', 'measurement'),  # teslemetry
    'EstimatedHoursToChargeTermination': Override('h', 'duration', 'measurement'),  # teslemetry
    'ExpectedEnergyPercentAtTripArrival': Override('%', 'battery', 'measurement'),  # teslemetry
    'ForwardCollisionWarning': Override(None, 'enum', None),  # teslemetry
    'Gear': Override(None, 'enum', None, {
        "ShiftStateP": "P",
        "ShiftStateR": "R",
        "ShiftStateN": "N",
        "ShiftStateD": "D",
    }),
    'GpsHeading': Override('°', None, None),  # teslemetry
    'GuestModeMobileAccessState': Override(None, 'enum', None),  # teslemetry
    'HvacFanSpeed': Override('%', None, None),  # teslemetry
    'HvacFanStatus': Override('%', None, None),  # teslemetry
    'HvacLeftTemperatureRequest': Override('°C', 'temperature', 'measurement'),  # teslemetry
    'HvacPower': Override(None, 'enum', None),  # teslemetry
    'HvacRightTemperatureRequest': Override('°C', 'temperature', 'measurement'),  # teslemetry
    'IdealBatteryRange': Override('mi', 'distance', 'measurement'),  # teslemetry
    # "This field frequently changes in small increments and setting a
    # minimum delta is recommended." Tesla sets no value of its own.
    'InsideTemp': Override(
        '°C', 'temperature', 'measurement',  # teslemetry
        minimum_delta_recommended=True,
    ),
    'LaneDepartureAvoidance': Override(None, 'enum', None),  # teslemetry
    # Undocumented by Tesla, so the generator has no type for it. The unit
    # is in Tesla's own field name (…Kwh), which is a statement rather than
    # an inference; LifetimeEnergyGainedRegen below is the same quantity and
    # teslemetry gives it kWh.
    'LifetimeEnergyChargedKwh': Override('kWh', 'energy', 'total_increasing'),
    'LifetimeEnergyGainedRegen': Override('kWh', 'energy', 'total_increasing'),  # teslemetry
    'LifetimeEnergyUsed': Override('kWh', 'energy', 'total_increasing'),  # teslemetry
    'LightsTurnSignal': Override(None, 'enum', None),  # teslemetry
    # "Beginning with firmware version 2025.2.6, specifying minimum delta for
    # location values is possible. Changes in distance are measured in meters."
    # No unit/device_class: Tesla documents the type as Location, and the
    # reconciler rejects a unit on a non-numeric type. "is possible" is the
    # merely-supported sense, not a recommendation.
    'Location': Override(minimum_delta_supported=True),
    'MilesSinceReset': Override('mi', 'distance', 'total_increasing'),  # teslemetry
    'MilesToArrival': Override('mi', 'distance', 'measurement'),  # teslemetry
    # "The minutes until arriving at the navigation destination."
    # teslemetry maps this to device_class=timestamp, converting the value to
    # an absolute arrival time in its own code. This table describes the raw
    # signal, and the generic entity path passes values through unconverted,
    # so a timestamp device class on a bare number would break the entity.
    'MinutesToArrival': Override('min', 'duration', 'measurement'),
    'ModuleTempMax': Override('°C', None, 'measurement'),  # teslemetry
    'ModuleTempMin': Override('°C', None, 'measurement'),  # teslemetry
    # Undocumented by Tesla; the unit is in the field name (…Kwh). Pack
    # capacity is a measurement, not a running total, so no total_increasing.
    # device_class is 'energy_storage', not 'energy': HA's
    # DEVICE_CLASS_STATE_CLASSES only allows 'energy' to pair with
    # total/total_increasing, never 'measurement' — 'energy' + 'measurement'
    # is an invalid combination the entity would refuse to add. The curated
    # entity this replaces used 'energy_storage' for exactly this reason.
    'NominalFullPackEnergyKwh': Override('kWh', 'energy_storage', 'measurement'),
    # "Beginning with firmware version 2025.2.6, the minimum delta for
    # Odometer is set to 0.1 by default."
    'Odometer': Override(
        'mi', 'distance', 'total_increasing',  # teslemetry
        minimum_delta_default=0.1,
    ),
    'OutsideTemp': Override('°C', 'temperature', 'measurement'),  # teslemetry
    'PackCurrent': Override('A', 'current', 'measurement'),  # teslemetry
    'PackVoltage': Override('V', 'voltage', 'measurement'),  # teslemetry
    'PedalPosition': Override('%', None, 'measurement'),  # teslemetry
    'PowershareHoursLeft': Override('h', 'duration', 'measurement'),  # teslemetry
    'PowershareInstantaneousPowerKW': Override('kW', 'power', 'measurement'),  # teslemetry
    'PowershareStatus': Override(None, 'enum', None),  # teslemetry
    'PowershareStopReason': Override(None, 'enum', None),  # teslemetry
    'PowershareType': Override(None, 'enum', None),  # teslemetry
    'RatedRange': Override('mi', 'distance', 'measurement'),  # teslemetry
    'RouteTrafficMinutesDelay': Override('min', 'duration', 'measurement'),  # teslemetry
    'ScheduledChargingMode': Override(None, 'enum', None),  # teslemetry
    # "This field requires minimum_delta to be explicitly set to a value >= 1"
    # — without one it never reports at all.
    'SelfDrivingMilesSinceReset': Override(
        'mi', 'distance', 'total_increasing',  # teslemetry
        minimum_delta_required=1.0,
    ),
    'SentryMode': Override(None, 'enum', None),  # teslemetry
    'Soc': Override('%', 'battery', 'measurement'),  # teslemetry
    # "The percent of the software update that has been downloaded."
    # No device_class: a download percentage is not a battery level. No
    # state_class either — the curated SoftwareUpdateDownloadSensor it
    # replaces deliberately left it unset, since a progress percentage that
    # sits at 0 between updates and jumps to 100 is not a meaningful
    # long-term statistic.
    'SoftwareUpdateDownloadPercentComplete': Override('%', None, None),
    'SoftwareUpdateExpectedDurationMinutes': Override('min', 'duration', 'measurement'),  # teslemetry
    # "The percent a software update has finished installing." Same
    # no-state_class reasoning as SoftwareUpdateDownloadPercentComplete.
    'SoftwareUpdateInstallationPercentComplete': Override('%', None, None),
    'SpeedLimitWarning': Override(None, 'enum', None),  # teslemetry
    # "The number of hours until charging is complete."
    'TimeToFullCharge': Override('h', 'duration', 'measurement'),
    'TonneauTentMode': Override(None, 'enum', None),  # teslemetry
    'TpmsPressureFl': Override('bar', 'pressure', 'measurement'),  # teslemetry
    'TpmsPressureFr': Override('bar', 'pressure', 'measurement'),  # teslemetry
    'TpmsPressureRl': Override('bar', 'pressure', 'measurement'),  # teslemetry
    'TpmsPressureRr': Override('bar', 'pressure', 'measurement'),  # teslemetry
    'VehicleSpeed': Override('mph', 'speed', 'measurement'),  # teslemetry
}
