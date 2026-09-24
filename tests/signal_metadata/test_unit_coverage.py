"""Every signal we stream by default has a unit, or is explicitly dimensionless.

Adding a numeric signal to DEFAULT_INTERVALS_SECONDS without a unit would
give users a sensor whose values mean nothing and which cannot be graphed
against anything else. Making the exemption list explicit means a new
unitless default is a deliberate edit rather than an oversight.
"""
from __future__ import annotations

from custom_components.tesla_telemetry.const import DEFAULT_INTERVALS_SECONDS
from custom_components.tesla_telemetry.signal_metadata import SIGNALS

# Genuinely dimensionless: counts, indexes and 0-3 comfort levels.
DIMENSIONLESS = {
    "ChargerPhases",
    "HomelinkDeviceCount",
    "HvacSteeringWheelHeatLevel",
    "MediaAudioVolume",
    "MediaAudioVolumeIncrement",
    "MediaAudioVolumeMax",
    "NumBrickVoltageMax",
    "NumBrickVoltageMin",
    "NumModuleTempMax",
    "NumModuleTempMin",
    "PairedPhoneKeyAndKeyFobQty",
    "SeatHeaterLeft",
    "SeatHeaterRearCenter",
    "SeatHeaterRearLeft",
    "SeatHeaterRearRight",
    "SeatHeaterRight",
}

_NUMERIC = {"real", "integer"}


def test_numeric_defaults_have_a_unit_or_are_listed_dimensionless() -> None:
    missing = sorted(
        name
        for name in DEFAULT_INTERVALS_SECONDS
        if (meta := SIGNALS.get(name)) is not None
        and meta.value_type in _NUMERIC
        and not meta.unit
        and name not in DIMENSIONLESS
    )
    assert not missing, (
        f"numeric default signals with no unit: {missing}. Add a unit to "
        "signal_catalog/overrides.py, or add the name to DIMENSIONLESS here "
        "if it genuinely has no unit."
    )


def test_every_default_signal_exists_in_the_catalog() -> None:
    """A default naming a signal the proto does not have can never arrive."""
    unknown = sorted(set(DEFAULT_INTERVALS_SECONDS) - set(SIGNALS))
    assert not unknown, f"defaults that are not real signals: {unknown}"


def test_dimensionless_list_has_no_stale_entries() -> None:
    """Keeps the exemption list honest as overrides are filled in."""
    stale = sorted(
        name
        for name in DIMENSIONLESS
        if (meta := SIGNALS.get(name)) is not None and meta.unit
    )
    assert not stale, f"these now have units and should leave DIMENSIONLESS: {stale}"
