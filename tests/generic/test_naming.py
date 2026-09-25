"""Naming rules for generic entities.

snake() decides every generic unique_id, so it is pinned hard: a change here
silently orphans history for every entity it touches.
"""
from __future__ import annotations

import pytest

from custom_components.tesla_telemetry.generic.naming import (
    generic_unique_id,
    humanise,
    snake,
)


@pytest.mark.parametrize(
    ("signal", "expected"),
    [
        ("VehicleSpeed", "vehicle_speed"),
        ("Soc", "soc"),
        ("DCChargingPower", "dc_charging_power"),      # leading acronym
        ("ACChargingEnergyIn", "ac_charging_energy_in"),
        ("TpmsPressureFl", "tpms_pressure_fl"),
        ("Hvil", "hvil"),
        ("DiStatorTempF", "di_stator_temp_f"),
        ("LifetimeEnergyChargedKwh", "lifetime_energy_charged_kwh"),
        ("GpsAccuracyMeters", "gps_accuracy_meters"),
    ],
)
def test_snake(signal: str, expected: str) -> None:
    assert snake(signal) == expected


def test_unique_id_shape() -> None:
    assert generic_unique_id("VIN123", "VehicleSpeed") == "VIN123_vehicle_speed_telemetry"


@pytest.mark.parametrize(
    ("signal", "expected"),
    [
        ("RemoteStartActive", "Remote start active"),
        ("VehicleSpeed", "Vehicle speed"),
        ("DCChargingPower", "DC charging power"),
    ],
)
def test_humanise(signal: str, expected: str) -> None:
    assert humanise(signal) == expected


def test_snake_is_stable_for_the_whole_catalog() -> None:
    """Two signals must never collide into one unique_id."""
    from custom_components.tesla_telemetry.signal_metadata import SIGNALS

    seen: dict[str, str] = {}
    for name in SIGNALS:
        key = snake(name)
        assert key not in seen, f"{name} and {seen[key]} both snake to {key!r}"
        seen[key] = name
