"""The v2 -> v3 unique_id migration.

Half the hand-written entities were named editorially rather than
mechanically, so a uniform generic rule renames them. Renaming a unique_id
orphans that entity's recorder history, so the registry is rewritten once
instead.

async_migrate_entry must never raise: an exception there fails the whole
config entry, leaving a user with a dead integration rather than a few stale
names.
"""
from __future__ import annotations

import json
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tesla_telemetry.const import DOMAIN
from custom_components.tesla_telemetry.generic.naming import snake
from custom_components.tesla_telemetry.migration import (
    LEGACY_UNIQUE_IDS,
    async_migrate_unique_ids,
)

VIN = "TESTVIN0000000001"
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/legacy_unique_ids.json"


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=VIN, data={"vin": VIN}, version=2)
    entry.add_to_hass(hass)
    return entry


def test_every_target_matches_the_naming_rule() -> None:
    """The map cannot drift from snake(signal) + '_telemetry'."""
    data = json.loads(FIXTURE.read_text())
    for old, new in LEGACY_UNIQUE_IDS.items():
        signal = data[old]["signal"]
        assert new == f"{snake(signal)}_telemetry", f"{old}: {new} is not the rule"


def test_no_claimed_signal_is_migrated() -> None:
    from custom_components.tesla_telemetry.generic.claimed import CLAIMED_SIGNALS

    data = json.loads(FIXTURE.read_text())
    for old in LEGACY_UNIQUE_IDS:
        assert data[old]["signal"] not in CLAIMED_SIGNALS


async def test_renames_a_legacy_entity(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    registry = er.async_get(hass)
    old, new = next(iter(LEGACY_UNIQUE_IDS.items()))
    created = registry.async_get_or_create(
        "sensor", DOMAIN, f"{VIN}_{old}", config_entry=entry
    )

    assert await async_migrate_unique_ids(hass, entry) >= 1

    assert registry.async_get(created.entity_id).unique_id == f"{VIN}_{new}"


async def test_is_idempotent(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    registry = er.async_get(hass)
    old, _new = next(iter(LEGACY_UNIQUE_IDS.items()))
    registry.async_get_or_create("sensor", DOMAIN, f"{VIN}_{old}", config_entry=entry)

    first = await async_migrate_unique_ids(hass, entry)
    second = await async_migrate_unique_ids(hass, entry)
    assert first >= 1
    assert second == 0


async def test_skips_when_the_target_already_exists(hass: HomeAssistant) -> None:
    """Review Focus 3: async_update_entity raises on a used unique_id, and
    that exception would fail the whole config entry."""
    entry = _entry(hass)
    registry = er.async_get(hass)
    old, new = next(iter(LEGACY_UNIQUE_IDS.items()))
    registry.async_get_or_create("sensor", DOMAIN, f"{VIN}_{old}", config_entry=entry)
    registry.async_get_or_create("sensor", DOMAIN, f"{VIN}_{new}", config_entry=entry)

    migrated = await async_migrate_unique_ids(hass, entry)   # must not raise

    assert migrated == 0
    assert registry.async_get_entity_id("sensor", DOMAIN, f"{VIN}_{old}") is not None


async def test_an_empty_registry_is_fine(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    assert await async_migrate_unique_ids(hass, entry) == 0
