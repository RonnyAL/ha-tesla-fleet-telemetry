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


async def test_browsing_a_category_lists_its_signals(hass, entry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "category"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"category": "driving"}
    )
    keys = {str(key) for key in result["data_schema"].schema}
    assert "VehicleSpeed" in keys
    # Driving only: a Charging signal must not appear here.
    assert "ChargerVoltage" not in keys


async def test_editing_a_category_pins_only_what_changed(hass, entry) -> None:
    """An untouched row must not become a pinned override."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "category"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"category": "driving"}
    )
    submitted = {
        str(key): key.default() for key in result["data_schema"].schema
    }
    submitted["VehicleSpeed"] = 7
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], submitted
    )
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )

    overrides = entry.options[CONF_SIGNAL_OVERRIDES]
    assert overrides["VehicleSpeed"] == {"interval_seconds": 7}
    # Sampling one other row (e.g. "Gear") would pass even if the other nine
    # rows in "driving" were wrongly pinned; check the whole set.
    assert set(overrides) == {"VehicleSpeed"}


async def test_tuning_one_signal_stores_all_four_knobs(hass, entry) -> None:
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "firmware_evidence": {"proven": "2026.32"}}
    )
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "signal"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"signal": "InsideTemp"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "interval_seconds": 120,
            "minimum_delta": 0.5,
            "resend_interval_seconds": 3600,
            "include_fields": ["VehicleSpeed"],
        },
    )
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )
    stored = entry.options[CONF_SIGNAL_OVERRIDES]["InsideTemp"]
    assert stored["interval_seconds"] == 120
    assert stored["minimum_delta"] == 0.5
    assert stored["resend_interval_seconds"] == 3600
    assert stored["include_fields"] == ["VehicleSpeed"]


async def test_include_fields_rejects_a_disabled_target(hass, entry) -> None:
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, "firmware_evidence": {"proven": "2026.32"}},
        options={CONF_SIGNAL_OVERRIDES: {"Hvil": {"interval_seconds": 0}}},
    )
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "signal"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"signal": "Odometer"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"interval_seconds": 300, "include_fields": ["Hvil"]},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"include_fields": "include_disabled"}


async def test_include_fields_enforces_the_miles_pair_rule(hass, entry) -> None:
    """Tesla: these two may only be included by each other."""
    from custom_components.tesla_telemetry.options_flow import (
        validate_include_fields,
    )

    enabled = {"MilesSinceReset", "SelfDrivingMilesSinceReset", "Odometer"}
    assert (
        validate_include_fields(
            "MilesSinceReset", ["SelfDrivingMilesSinceReset"], enabled
        )
        is None
    )
    assert (
        validate_include_fields("Odometer", ["MilesSinceReset"], enabled)
        == "include_miles_pair"
    )
    assert (
        validate_include_fields(
            "MilesSinceReset", ["Odometer"], enabled
        )
        == "include_miles_pair"
    )


async def test_a_signal_cannot_include_itself(hass) -> None:
    from custom_components.tesla_telemetry.options_flow import (
        validate_include_fields,
    )

    assert (
        validate_include_fields("Odometer", ["Odometer"], {"Odometer"})
        == "include_self"
    )


async def test_include_fields_accepts_a_valid_selection(hass) -> None:
    from custom_components.tesla_telemetry.options_flow import (
        validate_include_fields,
    )

    assert (
        validate_include_fields(
            "Odometer", ["VehicleSpeed"], {"Odometer", "VehicleSpeed"}
        )
        is None
    )


async def test_resend_shorter_than_interval_is_rejected(hass, entry) -> None:
    """A resend the interval can never honour is refused, not silently capped.

    cost.py takes max(resend, interval) to defend its own arithmetic, but the
    form is where the contradiction should be caught.
    """
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "firmware_evidence": {"proven": "2026.32"}}
    )
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "signal"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"signal": "InsideTemp"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "interval_seconds": 300,
            "minimum_delta": 0,
            "resend_interval_seconds": 60,
            "include_fields": [],
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {
        "resend_interval_seconds": "resend_shorter_than_interval"
    }


async def test_a_zero_resend_interval_stays_valid(hass, entry) -> None:
    """0 means "never resend" and must not trip the shorter-than check."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "signal"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"signal": "InsideTemp"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "interval_seconds": 30,
            "minimum_delta": 0,
            "resend_interval_seconds": 0,
            "include_fields": [],
        },
    )
    assert result["type"] is FlowResultType.MENU


async def test_reentering_category_edit_preserves_the_pending_value(
    hass, entry
) -> None:
    """Re-entering must show the pending value, not what is stored on the entry."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "category"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"category": "driving"}
    )
    submitted = {str(key): key.default() for key in result["data_schema"].schema}
    submitted["VehicleSpeed"] = 9
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], submitted
    )

    # Re-enter the same category without finishing.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "category"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"category": "driving"}
    )
    prefilled = {str(key): key.default() for key in result["data_schema"].schema}
    assert prefilled["VehicleSpeed"] == 9

    # Submitting exactly what the form pre-filled must not discard the edit.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], prefilled
    )
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )
    assert entry.options[CONF_SIGNAL_OVERRIDES]["VehicleSpeed"] == {
        "interval_seconds": 9
    }


async def test_reentering_signal_edit_preserves_the_pending_value(
    hass, entry
) -> None:
    """Re-entering must show the pending value, not what is stored on the entry."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "signal"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"signal": "InsideTemp"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "interval_seconds": 45,
            "minimum_delta": 0,
            "resend_interval_seconds": 0,
            "include_fields": [],
        },
    )

    # Re-enter the same signal without finishing.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "signal"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"signal": "InsideTemp"}
    )
    prefilled = result["data_schema"]({})
    assert prefilled["interval_seconds"] == 45

    # Submitting exactly what the form pre-filled must not discard the edit.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], prefilled
    )
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )
    assert entry.options[CONF_SIGNAL_OVERRIDES]["InsideTemp"]["interval_seconds"] == 45


# ---------------------------------------------------------------------------
# Fix round 1 — Critical 1: the miles-pair rule must not fire on an empty
# selection. `MilesSinceReset` and `SelfDrivingMilesSinceReset` may only be
# INCLUDED BY each other -- that says nothing about what they themselves must
# include, so leaving "include_fields" empty must stay valid for both.
# ---------------------------------------------------------------------------
async def test_miles_pair_rule_does_not_fire_on_an_empty_selection(hass) -> None:
    from custom_components.tesla_telemetry.options_flow import (
        validate_include_fields,
    )

    enabled = {"MilesSinceReset", "SelfDrivingMilesSinceReset", "Odometer"}
    # Before the fix: bool(True) != bool(False) was True, wrongly refusing
    # this with "include_miles_pair" even though nothing is being included.
    assert validate_include_fields("MilesSinceReset", [], enabled) is None
    assert validate_include_fields("SelfDrivingMilesSinceReset", [], enabled) is None
    # A non-member with no includes was never affected, but check it too.
    assert validate_include_fields("Odometer", [], enabled) is None
    # The pair rule must still bite once something is actually being
    # included — this is the case the fix must not weaken.
    assert (
        validate_include_fields("Odometer", ["MilesSinceReset"], enabled)
        == "include_miles_pair"
    )


async def test_miles_pair_member_is_tunable_through_the_actual_flow(
    hass, entry
) -> None:
    """End-to-end: MilesSinceReset must be configurable with no includes."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "signal"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"signal": "MilesSinceReset"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "interval_seconds": 60,
            "minimum_delta": 0,
            "resend_interval_seconds": 0,
            "include_fields": [],
        },
    )
    assert result["type"] is FlowResultType.MENU
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )
    assert entry.options[CONF_SIGNAL_OVERRIDES]["MilesSinceReset"][
        "interval_seconds"
    ] == 60


# ---------------------------------------------------------------------------
# Fix round 1 — Critical 2: the gated knobs (minimum_delta,
# resend_interval_seconds, include_fields) must pre-fill from the raw stored
# override, not from the firmware-gated resolved policy — otherwise
# reopening signal_edit on a car that hasn't proven support silently zeroes
# them, and saving the pre-filled form destroys the stored value.
# ---------------------------------------------------------------------------
async def test_signal_edit_preserves_gated_knobs_with_no_firmware_evidence(
    hass,
) -> None:
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"vin": VIN, "hostname": "telemetry.example.invalid", "port": 443},
        options={
            CONF_SIGNAL_OVERRIDES: {
                "InsideTemp": {
                    "interval_seconds": 60,
                    "minimum_delta": 0.5,
                    "resend_interval_seconds": 3600,
                }
            }
        },
    )
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "signal"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"signal": "InsideTemp"}
    )

    # Before the fix, these came back 0 / 0: the resolved policy nulls
    # minimum_delta and resend_interval_seconds when firmware has not proven
    # support (this entry has none), and the form pre-filled from that.
    prefilled = result["data_schema"]({})
    assert prefilled["interval_seconds"] == 60
    assert prefilled["minimum_delta"] == 0.5
    assert prefilled["resend_interval_seconds"] == 3600

    # Submitting exactly what the form pre-filled -- the frontend Save
    # path -- must not destroy the values it cannot send yet.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], prefilled
    )
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )
    stored = config_entry.options[CONF_SIGNAL_OVERRIDES]["InsideTemp"]
    assert stored["interval_seconds"] == 60
    assert stored["minimum_delta"] == 0.5
    assert stored["resend_interval_seconds"] == 3600


# ---------------------------------------------------------------------------
# Fix round 1 — Important 3: _signal_placeholders had zero test coverage,
# which is exactly why the "default AND recommended" case (ChargerVoltage)
# went unnoticed. Pin each sense of the four delta attributes here.
# ---------------------------------------------------------------------------
def _placeholder_notes(name: str) -> str:
    from custom_components.tesla_telemetry.firmware import FirmwareEvidence
    from custom_components.tesla_telemetry.options_flow import (
        TeslaTelemetryOptionsFlow,
    )
    from custom_components.tesla_telemetry.signal_metadata import SIGNALS

    flow = TeslaTelemetryOptionsFlow()
    meta = SIGNALS[name]
    # Evidence proven well past every floor, so gating notes never obscure
    # the delta-advice notes under test here.
    evidence = FirmwareEvidence(proven_version="2026.32")
    return flow._signal_placeholders(name, meta, evidence)["notes"]


def test_placeholders_state_a_required_minimum_delta() -> None:
    notes = _placeholder_notes("SelfDrivingMilesSinceReset")
    assert "requires a minimum delta" in notes


def test_placeholders_state_both_default_and_recommended_when_both_true() -> None:
    """ChargerVoltage carries both facts; the fix round 1 bug hid one."""
    notes = _placeholder_notes("ChargerVoltage")
    assert "already applies a default minimum delta" in notes
    assert "Tesla recommends setting a minimum delta" in notes


def test_placeholders_state_a_plain_recommendation() -> None:
    notes = _placeholder_notes("InsideTemp")
    assert "Tesla recommends setting a minimum delta" in notes
    assert "already applies a default minimum delta" not in notes


def test_placeholders_never_invent_a_recommendation_tesla_never_made() -> None:
    """Location: Tesla says a delta is merely possible, never recommended."""
    notes = _placeholder_notes("Location")
    assert "possible" in notes
    assert "recommends" not in notes


def test_placeholders_say_nothing_special_with_no_delta_advice() -> None:
    notes = _placeholder_notes("VehicleSpeed")
    assert notes == "No special requirements."


# ---------------------------------------------------------------------------
# Fix round 1 — Minor 6: every error key the flow can raise must have a
# translation, in both files. Mirrors test_translations.py's existing
# abort-reason check.
# ---------------------------------------------------------------------------
def test_every_options_error_key_is_translated() -> None:
    import json
    import re
    from pathlib import Path

    _dir = Path(
        "custom_components/tesla_telemetry"
    )
    source = (_dir / "options_flow.py").read_text(encoding="utf-8")
    # validate_include_fields returns its error keys as bare `return "..."`;
    # scope the search to that function so an unrelated `return "..."`
    # elsewhere in the module (e.g. category_slug's "uncategorised") is not
    # mistaken for a translated error key.
    start = source.index("def validate_include_fields")
    end = source.index("\nclass ", start)
    include_fields_body = source[start:end]
    used = set(re.findall(r'return "([a-z_]+)"', include_fields_body))
    # The resend check assigns its error key to the local `error` variable
    # before it is shown in the form.
    used |= set(re.findall(r'\berror = "([a-z_]+)"', source))
    assert used, "no options-flow error keys found to check"

    for name in ("strings.json", "translations/en.json"):
        defined = set(json.loads((_dir / name).read_text(encoding="utf-8"))["options"]["error"])
        missing = used - defined
        assert not missing, f"{name}: untranslated options error keys {sorted(missing)}"


# ---------------------------------------------------------------------------
# Final fix wave — Important 1: the per-signal form must not pin the interval
# just because some *other* field on the signal was edited, or it permanently
# defeats every future preset for that signal. Mirrors
# test_editing_a_category_pins_only_what_changed for the signal_edit form.
# ---------------------------------------------------------------------------
async def test_editing_a_signal_pins_only_what_changed(hass) -> None:
    """An untouched interval must not become a pinned override.

    Reproduction from the review: an entry on the `eco` preset, opening
    InsideTemp (pre-filled 900, eco's Climate interval), changing only
    minimum_delta and saving used to store
    `{"interval_seconds": 900, "minimum_delta": 0.5}` — a permanent pin at
    exactly the value the preset already gave it, so switching presets
    afterwards no longer moved this signal at all.
    """
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"vin": VIN, "hostname": "telemetry.example.invalid", "port": 443},
        options={CONF_INTERVAL_PRESET: "eco"},
    )
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "signal"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"signal": "InsideTemp"}
    )
    prefilled = result["data_schema"]({})
    assert prefilled["interval_seconds"] == 900  # eco's Climate interval

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "interval_seconds": prefilled["interval_seconds"],
            "minimum_delta": 0.5,
            "resend_interval_seconds": 0,
            "include_fields": [],
        },
    )
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )

    stored = config_entry.options[CONF_SIGNAL_OVERRIDES]["InsideTemp"]
    assert stored == {"minimum_delta": 0.5}
    assert "interval_seconds" not in stored

    # And the preset must actually still be in control of this signal.
    from custom_components.tesla_telemetry.firmware import evidence_from_entry
    from custom_components.tesla_telemetry.signals import resolve_field_policies

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "preset"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_INTERVAL_PRESET: "live"}
    )
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )

    policies = resolve_field_policies(config_entry, evidence_from_entry(config_entry))
    assert policies["InsideTemp"].interval_seconds == 30  # live's Climate interval


async def test_editing_a_pinned_signal_keeps_the_pin(hass) -> None:
    """An unchanged value that was already an explicit pin must stay pinned.

    Not a regression the review flagged, but the direct counterpart of the
    fix above: resubmitting the same value the form rendered must not be
    treated as "no explicit choice" when that value was already a genuine
    pin (as opposed to happening to equal what a preset would give anyway).
    """
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"vin": VIN, "hostname": "telemetry.example.invalid", "port": 443},
        options={CONF_SIGNAL_OVERRIDES: {"InsideTemp": {"interval_seconds": 45}}},
    )
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "signal"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"signal": "InsideTemp"}
    )
    prefilled = result["data_schema"]({})
    assert prefilled["interval_seconds"] == 45

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "interval_seconds": 45,
            "minimum_delta": 0.5,  # the actual edit
            "resend_interval_seconds": 0,
            "include_fields": [],
        },
    )
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )
    stored = config_entry.options[CONF_SIGNAL_OVERRIDES]["InsideTemp"]
    assert stored["interval_seconds"] == 45
    assert stored["minimum_delta"] == 0.5


# ---------------------------------------------------------------------------
# Final fix wave — Important 2: the per-signal form's advisory text must read
# the same pending firmware-evidence stand-in `_pending_policies` uses, not
# `self.config_entry` directly, or toggling `assume_firmware_support` on the
# cost step has no visible effect on the signal form until after `finish`.
# ---------------------------------------------------------------------------
async def test_signal_form_note_reflects_pending_assume_firmware_support(
    hass,
) -> None:
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"vin": VIN, "hostname": "telemetry.example.invalid", "port": 443},
        options={},
    )
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "signal"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"signal": "InsideTemp"}
    )
    before = result["description_placeholders"]["notes"]
    assert "has not yet confirmed firmware" in before

    # Turn assume_firmware_support on via the cost step, without finishing.
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "cost"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_COST_PER_MILLION_SIGNALS: DEFAULT_COST_PER_MILLION_SIGNALS,
            CONF_ASSUME_FIRMWARE_SUPPORT: True,
        },
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "signal"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"signal": "InsideTemp"}
    )
    after = result["description_placeholders"]["notes"]

    assert "has not yet confirmed firmware" not in after
    assert after != before


# ---------------------------------------------------------------------------
# Final fix wave — M2: a signal the required-delta gate drops entirely from
# the resolved policies (unproven firmware) must not render as 0 in the
# category form -- 0 is indistinguishable from "disabled", and typing 0 to
# actually disable it was previously a no-op because it already matched the
# rendered default.
# ---------------------------------------------------------------------------
async def test_category_form_renders_a_gated_away_signals_stored_pin(hass) -> None:
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"vin": VIN, "hostname": "telemetry.example.invalid", "port": 443},
        options={
            CONF_SIGNAL_OVERRIDES: {
                "SelfDrivingMilesSinceReset": {"interval_seconds": 120}
            }
        },
    )
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "category"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"category": "safety"}
    )
    prefilled = {str(key): key.default() for key in result["data_schema"].schema}
    # Before the fix this was 0, indistinguishable from "disabled".
    assert prefilled["SelfDrivingMilesSinceReset"] == 120

    # Disabling it must actually take effect: 0 now differs from what was
    # rendered, so it is written rather than silently skipped.
    prefilled["SelfDrivingMilesSinceReset"] = 0
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], prefilled
    )
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )
    assert config_entry.options[CONF_SIGNAL_OVERRIDES][
        "SelfDrivingMilesSinceReset"
    ] == {"interval_seconds": 0}
