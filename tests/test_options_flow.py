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
    CONF_ASSUME_FIRMWARE_SUPPORT,
    CONF_COST_PER_MILLION_SIGNALS,
    CONF_FIRMWARE_EVIDENCE,
    CONF_INTERVAL_PRESET,
    CONF_SIGNAL_OVERRIDES,
    DEFAULT_COST_PER_MILLION_SIGNALS,
    DOMAIN,
)
from custom_components.tesla_telemetry.cost import SECONDS_PER_MONTH, signals_to_cost

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


async def test_reentering_preset_step_preserves_the_pending_choice(hass, entry) -> None:
    """Re-entering the step must show the pending choice, not the stored one.

    A frontend "Save" click resubmits whatever the form is pre-filled with.
    If the default reverted to what is stored on the entry rather than what
    is pending in this flow, that resubmission would silently discard the
    choice the user already made — the exact defect fixed in round 1.
    """
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "preset"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_INTERVAL_PRESET: "eco"}
    )

    # Re-enter the step without finishing. The form's own default must
    # reflect the pending "eco", not the (still empty) stored options.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "preset"}
    )
    assert result["type"] is FlowResultType.FORM
    prefilled = result["data_schema"]({})
    assert prefilled[CONF_INTERVAL_PRESET] == "eco"

    # Submitting exactly what the form pre-filled — the frontend Save path —
    # must not revert the pending choice.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], prefilled
    )
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )
    assert entry.options[CONF_INTERVAL_PRESET] == "eco"


async def test_edits_from_two_different_steps_both_survive_to_finish(
    hass, entry
) -> None:
    """The headline accumulation property: nothing overwrites anything else."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "preset"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_INTERVAL_PRESET: "eco"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "cost"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_COST_PER_MILLION_SIGNALS: 8.5}
    )
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )
    assert entry.options[CONF_INTERVAL_PRESET] == "eco"
    assert entry.options[CONF_COST_PER_MILLION_SIGNALS] == 8.5


async def test_assume_firmware_support_round_trips(hass, entry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "cost"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_COST_PER_MILLION_SIGNALS: 8.5,
            CONF_ASSUME_FIRMWARE_SUPPORT: True,
        },
    )
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )
    assert entry.options[CONF_ASSUME_FIRMWARE_SUPPORT] is True


async def test_firmware_evidence_changes_the_cost_floor(hass) -> None:
    """The floor must reflect pending firmware evidence, with exact values.

    `resend_interval_seconds` is withheld until firmware proves support
    (firmware.py's FLOOR_RESEND_INTERVAL), so the *same* pending override
    must price out to a real floor once the entry has proof, and to exactly
    zero without it. `endswith("0.00")` cannot tell a genuine zero from a
    real value that got silently zeroed by the firmware gate, so this checks
    the whole string.
    """
    overrides = {CONF_SIGNAL_OVERRIDES: {"VehicleSpeed": {"resend_interval_seconds": 60}}}
    currency = hass.config.currency

    unproven = MockConfigEntry(
        domain=DOMAIN,
        data={"vin": VIN, "hostname": "telemetry.example.invalid", "port": 443},
        options=overrides,
    )
    unproven.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(unproven.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "cost"}
    )
    unproven_floor = result["description_placeholders"]["floor"]
    assert unproven_floor == f"{currency} 0.00"

    proven = MockConfigEntry(
        domain=DOMAIN,
        data={
            "vin": VIN,
            "hostname": "telemetry.example.invalid",
            "port": 443,
            CONF_FIRMWARE_EVIDENCE: {"proven": "2024.44.32"},
        },
        options=overrides,
    )
    proven.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(proven.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "cost"}
    )
    proven_floor = result["description_placeholders"]["floor"]

    expected_signals = 1 * SECONDS_PER_MONTH / 60
    expected_cost = signals_to_cost(expected_signals, DEFAULT_COST_PER_MILLION_SIGNALS)
    assert proven_floor == f"{currency} {expected_cost:.2f}"
    assert proven_floor != unproven_floor


async def test_cost_step_reflects_pending_assume_firmware_support(hass) -> None:
    """Toggling the cost page's own control must change what it displays.

    Round-1 defect: the displayed bounds were computed from the *stored*
    firmware evidence, so the toggle on this very page had no visible effect
    on this very page until after `finish`.
    """
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"vin": VIN, "hostname": "telemetry.example.invalid", "port": 443},
        options={CONF_SIGNAL_OVERRIDES: {"VehicleSpeed": {"resend_interval_seconds": 60}}},
    )
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "cost"}
    )
    before = result["description_placeholders"]["floor"]

    # Turn assume_firmware_support on, then re-enter the cost step without
    # finishing. The floor must already reflect the pending toggle.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_COST_PER_MILLION_SIGNALS: DEFAULT_COST_PER_MILLION_SIGNALS,
            CONF_ASSUME_FIRMWARE_SUPPORT: True,
        },
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "cost"}
    )
    after = result["description_placeholders"]["floor"]

    assert after != before
    expected_signals = 1 * SECONDS_PER_MONTH / 60
    expected_cost = signals_to_cost(expected_signals, DEFAULT_COST_PER_MILLION_SIGNALS)
    assert after == f"{hass.config.currency} {expected_cost:.2f}"
