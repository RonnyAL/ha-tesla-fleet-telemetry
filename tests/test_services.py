"""Tests for `set_interval_preset`'s validation and reporting.

The service used to validate its `preset` argument against
`INTERVAL_PRESET_OVERRIDES`, a table that only ever held `default` and
`high_rate`. The options UI added in this phase lets a user pick `eco`,
`balanced` or `live` too — so the service rejected exactly the presets the UI
offered, the same "two stores disagree about one setting" bug the preset
work in this phase exists to remove. Validation now runs against
`presets.PRESETS`, the same five names the options flow's preset step offers.
"""
from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.tesla_telemetry.presets import PRESETS
from custom_components.tesla_telemetry.services import (
    _SET_PRESET_SCHEMA,
    ATTR_PRESET,
)


@pytest.mark.parametrize("preset", PRESETS)
def test_every_preset_name_is_accepted(preset: str) -> None:
    validated = _SET_PRESET_SCHEMA({ATTR_PRESET: preset})
    assert validated[ATTR_PRESET] == preset


def test_all_five_presets_are_covered() -> None:
    """Pin the count so a future preset added to PRESETS but not exercised
    above is caught rather than silently under-tested."""
    assert len(PRESETS) == 5


def test_an_unknown_preset_is_rejected() -> None:
    with pytest.raises(vol.Invalid):
        _SET_PRESET_SCHEMA({ATTR_PRESET: "not_a_real_preset"})
