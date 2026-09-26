"""Tests for the per-category interval presets.

A preset retunes signals that are already enabled. It must never enable one:
"eco" that switched on all 56 Charging signals would be the opposite of its
name.
"""
from __future__ import annotations

from custom_components.tesla_telemetry.presets import (
    PRESET_BALANCED,
    PRESET_CATEGORY_INTERVALS,
    PRESET_DEFAULT,
    PRESET_ECO,
    PRESET_HIGH_RATE,
    PRESET_LIVE,
    PRESETS,
    UNCATEGORISED,
    normalise_preset,
    preset_interval,
)
from custom_components.tesla_telemetry.signal_metadata import SIGNALS


def test_default_retunes_nothing() -> None:
    """An entry that never chose a preset must resolve to today's config."""
    for signal in ("VehicleSpeed", "ChargerVoltage", "Odometer"):
        assert preset_interval(PRESET_DEFAULT, signal) is None


def test_every_category_in_the_catalog_has_an_entry_in_every_preset() -> None:
    """A category with no entry would silently fall through to the default."""
    categories = {m.category or UNCATEGORISED for m in SIGNALS.values()}
    for preset in (PRESET_ECO, PRESET_BALANCED, PRESET_LIVE):
        missing = categories - set(PRESET_CATEGORY_INTERVALS[preset])
        assert not missing, f"{preset} has no interval for {sorted(missing)}"


def test_presets_order_from_sparse_to_dense() -> None:
    """eco >= balanced >= live for every category, or the names are lies."""
    for category in PRESET_CATEGORY_INTERVALS[PRESET_ECO]:
        eco = PRESET_CATEGORY_INTERVALS[PRESET_ECO][category]
        balanced = PRESET_CATEGORY_INTERVALS[PRESET_BALANCED][category]
        live = PRESET_CATEGORY_INTERVALS[PRESET_LIVE][category]
        assert eco >= balanced >= live, category


def test_a_signal_resolves_through_its_documented_category() -> None:
    assert SIGNALS["VehicleSpeed"].category == "Driving"
    assert preset_interval(PRESET_LIVE, "VehicleSpeed") == 1
    assert preset_interval(PRESET_ECO, "VehicleSpeed") == 300


def test_an_uncategorised_signal_uses_the_uncategorised_entry() -> None:
    uncategorised = next(
        name for name, meta in SIGNALS.items() if meta.category is None
    )
    assert preset_interval(PRESET_BALANCED, uncategorised) == (
        PRESET_CATEGORY_INTERVALS[PRESET_BALANCED][UNCATEGORISED]
    )


def test_an_unknown_signal_has_no_preset_interval() -> None:
    """A stored override can name a signal a proto bump removed."""
    assert preset_interval(PRESET_LIVE, "NotASignal") is None


def test_high_rate_is_per_signal_and_touches_nothing_else() -> None:
    """The legacy preset keeps working for existing automations."""
    assert preset_interval(PRESET_HIGH_RATE, "Location") == 1
    assert preset_interval(PRESET_HIGH_RATE, "VehicleSpeed") == 1
    assert preset_interval(PRESET_HIGH_RATE, "ChargerVoltage") is None


def test_normalise_preset_falls_back_to_default() -> None:
    """A preset name from a newer version must not break an entry at setup."""
    assert normalise_preset("eco") == PRESET_ECO
    assert normalise_preset("no_such_preset") == PRESET_DEFAULT
    assert normalise_preset(None) == PRESET_DEFAULT
    assert normalise_preset(17) == PRESET_DEFAULT


def test_presets_tuple_matches_the_defined_names() -> None:
    assert set(PRESETS) == {
        PRESET_DEFAULT,
        PRESET_ECO,
        PRESET_BALANCED,
        PRESET_LIVE,
        PRESET_HIGH_RATE,
    }
