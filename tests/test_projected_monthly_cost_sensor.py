"""Tests for `ProjectedMonthlyCostSensor`.

Added alongside the fix for two defects in the original brief: an invalid
device/state class pairing (MONETARY + MEASUREMENT, which Home Assistant
rejects) and a startup spike where a sub-second uptime window projected a
single datum into an absurd monthly figure. See `cost.py`'s
`MIN_PROJECTION_WINDOW_SECONDS` for the latter.
"""
from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tesla_telemetry.const import DOMAIN
from custom_components.tesla_telemetry.coordinator import TeslaTelemetryCoordinator
from custom_components.tesla_telemetry.sensor import ProjectedMonthlyCostSensor

_VIN = "5YJ3E1EA1PF000000"


def test_unique_id_matches_the_documented_suffix(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={"vin": _VIN})
    entry.add_to_hass(hass)
    coordinator = TeslaTelemetryCoordinator(hass, _VIN, "Test Car")

    sensor = ProjectedMonthlyCostSensor(coordinator, entry)

    assert sensor.unique_id == f"{_VIN}_projected_monthly_cost_telemetry"


def test_native_value_is_unknown_before_the_measurement_window(hass) -> None:
    """A process that just started has no window to measure yet.

    `coordinator.started_at` is set at construction, so querying the sensor
    immediately after gives an uptime far below
    `MIN_PROJECTION_WINDOW_SECONDS` — the exact case that used to project a
    single early datum into an absurd monthly figure.
    """
    entry = MockConfigEntry(domain=DOMAIN, data={"vin": _VIN})
    entry.add_to_hass(hass)
    coordinator = TeslaTelemetryCoordinator(hass, _VIN, "Test Car")
    coordinator.signals_since_start = 1

    sensor = ProjectedMonthlyCostSensor(coordinator, entry)
    sensor.hass = hass

    assert sensor.native_value is None


def test_native_unit_falls_back_to_usd_when_currency_is_unset(hass) -> None:
    """No `hass` attached yet (or no configured currency) means "USD"."""
    entry = MockConfigEntry(domain=DOMAIN, data={"vin": _VIN})
    entry.add_to_hass(hass)
    coordinator = TeslaTelemetryCoordinator(hass, _VIN, "Test Car")

    sensor = ProjectedMonthlyCostSensor(coordinator, entry)
    # Mirrors the state of a freshly constructed entity before the platform
    # attaches it to hass -- exactly the branch `native_unit_of_measurement`
    # guards against with `if self.hass else None`.
    assert sensor.hass is None

    assert sensor.native_unit_of_measurement == "USD"
