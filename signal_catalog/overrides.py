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
    # "...as a percentage of battery capacity."
    'ChargeLimitSoc': Override('%', 'battery', 'measurement'),
    'ChargeRateMilePerHour': Override('mph', 'speed', 'measurement'),  # teslemetry
    'ChargerVoltage': Override('V', 'voltage', 'measurement'),  # teslemetry
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
    'EnergyRemaining': Override('kWh', 'energy_storage', 'measurement'),  # teslemetry
    'EstBatteryRange': Override('mi', 'distance', 'measurement'),  # teslemetry
    'EstimatedHoursToChargeTermination': Override('h', 'duration', 'measurement'),  # teslemetry
    'ExpectedEnergyPercentAtTripArrival': Override('%', 'battery', 'measurement'),  # teslemetry
    'ForwardCollisionWarning': Override(None, 'enum', None),  # teslemetry
    'Gear': Override(None, 'enum', None),  # teslemetry
    'GpsHeading': Override('°', None, None),  # teslemetry
    'GuestModeMobileAccessState': Override(None, 'enum', None),  # teslemetry
    'HvacFanSpeed': Override('%', None, None),  # teslemetry
    'HvacFanStatus': Override('%', None, None),  # teslemetry
    'HvacLeftTemperatureRequest': Override('°C', 'temperature', 'measurement'),  # teslemetry
    'HvacPower': Override(None, 'enum', None),  # teslemetry
    'HvacRightTemperatureRequest': Override('°C', 'temperature', 'measurement'),  # teslemetry
    'IdealBatteryRange': Override('mi', 'distance', 'measurement'),  # teslemetry
    'InsideTemp': Override('°C', 'temperature', 'measurement'),  # teslemetry
    'LaneDepartureAvoidance': Override(None, 'enum', None),  # teslemetry
    'LifetimeEnergyGainedRegen': Override('kWh', 'energy', 'total_increasing'),  # teslemetry
    'LifetimeEnergyUsed': Override('kWh', 'energy', 'total_increasing'),  # teslemetry
    'LightsTurnSignal': Override(None, 'enum', None),  # teslemetry
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
    'Odometer': Override('mi', 'distance', 'total_increasing'),  # teslemetry
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
    'SelfDrivingMilesSinceReset': Override('mi', 'distance', 'total_increasing'),  # teslemetry
    'SentryMode': Override(None, 'enum', None),  # teslemetry
    'Soc': Override('%', 'battery', 'measurement'),  # teslemetry
    # "The percent of the software update that has been downloaded."
    # No device_class: a download percentage is not a battery level.
    'SoftwareUpdateDownloadPercentComplete': Override('%', None, 'measurement'),
    'SoftwareUpdateExpectedDurationMinutes': Override('min', 'duration', 'measurement'),  # teslemetry
    # "The percent a software update has finished installing."
    'SoftwareUpdateInstallationPercentComplete': Override('%', None, 'measurement'),
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
