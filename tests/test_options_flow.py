"""Tests for the multi-step options flow.

Edits accumulate in memory and are written once, at `finish`. Abandoning the
flow must persist nothing — a half-applied telemetry config is worse than no
change, because it is pushed to the vehicle.
"""
from __future__ import annotations

import pytest
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tesla_telemetry.const import (
    CONF_COST_PER_MILLION_SIGNALS,
    CONF_INTERVAL_PRESET,
    CONF_SIGNAL_OVERRIDES,
    DOMAIN,
)

VIN = "5YJ3E1EA1PF000000"


@pytest.fixture
def entry(hass):
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"vin": VIN, "hostname": "telemetry.example.invalid", "port": 443},
        options={},
    )
    config_entry.add_to_hass(hass)
    return config_entry


async def test_init_shows_a_menu(hass, entry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert set(result["menu_options"]) >= {
        "preset",
        "category",
        "signal",
        "cost",
        "finish",
    }


async def test_choosing_a_preset_and_finishing_stores_it(hass, entry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "preset"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_INTERVAL_PRESET: "eco"}
    )
    # Back at the menu.
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_INTERVAL_PRESET] == "eco"


async def test_abandoning_the_flow_writes_nothing(hass, entry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "preset"}
    )
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_INTERVAL_PRESET: "live"}
    )
    hass.config_entries.options.async_abort(result["flow_id"])
    assert entry.options == {}


async def test_the_cost_step_reports_both_bounds(hass, entry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "cost"}
    )
    placeholders = result["description_placeholders"]
    assert "ceiling" in placeholders
    assert "floor" in placeholders
    # No resend interval is configured by default, so nothing is guaranteed.
    assert placeholders["floor"].endswith("0.00")


async def test_the_cost_rate_round_trips(hass, entry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "cost"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_COST_PER_MILLION_SIGNALS: 8.5}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )
    assert entry.options[CONF_COST_PER_MILLION_SIGNALS] == 8.5


async def test_existing_options_survive_an_unrelated_edit(hass) -> None:
    """Editing the preset must not discard someone's per-signal overrides."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"vin": VIN, "hostname": "telemetry.example.invalid", "port": 443},
        options={CONF_SIGNAL_OVERRIDES: {"VehicleSpeed": 42}},
    )
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "preset"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_INTERVAL_PRESET: "balanced"}
    )
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )

    overrides = config_entry.options[CONF_SIGNAL_OVERRIDES]
    assert overrides["VehicleSpeed"] == {"interval_seconds": 42}


async def test_a_legacy_int_override_is_normalised_on_save(hass) -> None:
    """The dict form is written back, without changing what it means."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"vin": VIN, "hostname": "telemetry.example.invalid", "port": 443},
        options={CONF_SIGNAL_OVERRIDES: {"VehicleSpeed": 42, "Odometer": 0}},
    )
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )

    assert config_entry.options[CONF_SIGNAL_OVERRIDES] == {
        "VehicleSpeed": {"interval_seconds": 42},
        "Odometer": {"interval_seconds": 0},
    }
