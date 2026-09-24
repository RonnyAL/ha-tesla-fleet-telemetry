"""Tests for the hand-maintained unit / device-class overrides.

Units are deliberately not inferred from Tesla's descriptions: the text for
DCChargingPower mentions both watts and kilowatts, and a Tesla copy-edit
must never be able to silently change a unit and with it the meaning of
every recorded value. This table is the only source.
"""
from __future__ import annotations

from signal_catalog.overrides import OVERRIDES, Override


def test_known_units_are_plain_strings_not_ha_constants() -> None:
    """The generator must not import Home Assistant."""
    for name, override in OVERRIDES.items():
        for value in (override.unit, override.device_class, override.state_class):
            assert value is None or isinstance(value, str), name
            assert value is None or "." not in value, (
                f"{name}: {value!r} looks like an HA constant, not a plain string"
            )


def test_spot_checks() -> None:
    assert OVERRIDES["ChargeAmps"] == Override(
        unit="A", device_class="current", state_class="measurement"
    )
    assert OVERRIDES["BatteryLevel"].unit == "%"
    assert OVERRIDES["ACChargingEnergyIn"].unit == "kWh"


def test_covers_most_numeric_signals() -> None:
    """A floor, not an exact count, so adding an override does not fail."""
    assert len(OVERRIDES) >= 80
