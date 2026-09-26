"""Tests for accumulating firmware evidence from the live stream.

The coordinator's per-signal counter is since-restart only, so proof has to be
persisted as it is earned rather than recomputed at setup.
"""
from __future__ import annotations

import pytest

from custom_components.tesla_telemetry.const import (
    CONF_FIRMWARE_EVIDENCE,
    DEFAULT_INTERVALS_SECONDS,
)
from custom_components.tesla_telemetry.firmware import (
    FLOOR_MINIMUM_DELTA,
    FirmwareEvidence,
    proof_from_signals,
)


def test_version_is_streamed_by_default() -> None:
    """Evidence needs the signal that carries the firmware version."""
    assert "Version" in DEFAULT_INTERVALS_SECONDS


def test_version_has_a_long_interval() -> None:
    """Firmware changes monthly; a tight ceiling would buy nothing."""
    assert DEFAULT_INTERVALS_SECONDS["Version"] >= 3600


def test_four_default_signals_prove_the_minimum_delta_floor() -> None:
    """The gate is only useful if proof is actually reachable.

    Receipt of any one of these establishes 2024.44.32 outright, with no
    version string involved.
    """
    provers = [
        name
        for name in DEFAULT_INTERVALS_SECONDS
        if proof_from_signals([name]) is not None
        and FirmwareEvidence(proven_version=proof_from_signals([name])).supports(
            FLOOR_MINIMUM_DELTA
        )
    ]
    assert "ChargerVoltage" in provers
    assert "LocatedAtHome" in provers
    assert len(provers) >= 4


@pytest.mark.parametrize(
    ("stored", "arriving", "expected"),
    [
        (None, "ChargerVoltage", "2024.44.32"),
        ("2024.26", "ChargerVoltage", "2024.44.32"),
        # Monotonic: a lower floor never lowers what was already proven.
        ("2026.32", "ChargerVoltage", "2026.32"),
        # A signal with no documented floor proves nothing.
        ("2024.26", "VehicleSpeed", "2024.26"),
    ],
)
async def test_proof_only_ever_rises(hass, stored, arriving, expected) -> None:
    """Drives `_handle` through the dispatcher rather than reimplementing it.

    An earlier version of this test recomputed the monotonic fold inline
    (calling `firmware.at_least` directly), which pins `firmware.at_least`
    rather than the subscriber — deleting the monotonicity guard in
    `_async_record_firmware_evidence` would not have failed it. This drives
    the real `_handle` callback via `coordinator.async_publish`, so it
    actually exercises that guard.
    """
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.tesla_telemetry import (
        _async_record_firmware_evidence,
    )
    from custom_components.tesla_telemetry.const import DOMAIN
    from custom_components.tesla_telemetry.coordinator import (
        TeslaTelemetryCoordinator,
    )
    from custom_components.tesla_telemetry.proto import vehicle_data_pb2

    data: dict = {"vin": "5YJ3E1EA1PF000000"}
    if stored is not None:
        data[CONF_FIRMWARE_EVIDENCE] = {"proven": stored}
    entry = MockConfigEntry(domain=DOMAIN, data=data)
    entry.add_to_hass(hass)
    coordinator = TeslaTelemetryCoordinator(hass, "5YJ3E1EA1PF000000", "Test Car")
    unsub = _async_record_firmware_evidence(hass, entry, coordinator)

    value = vehicle_data_pb2.Value()
    value.double_value = 1.0
    coordinator.async_publish(arriving, value)
    await hass.async_block_till_done()

    assert entry.data.get(CONF_FIRMWARE_EVIDENCE, {}).get("proven") == expected
    unsub()


async def test_a_cached_sample_is_replayed_into_evidence(hass) -> None:
    """A sample already on the coordinator before subscription still counts.

    Mirrors the generic factory's `replay_cache`: a datum can arrive and be
    cached between platform setup and this subscriber being wired up. Without
    a matching replay, a slow signal's proof would wait for its *next* datum
    — which, for some signals, may not arrive again today. The self-healing
    behaviour the firmware gate depends on shouldn't be gated on that.
    """
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.tesla_telemetry import (
        _async_record_firmware_evidence,
    )
    from custom_components.tesla_telemetry.const import DOMAIN
    from custom_components.tesla_telemetry.coordinator import (
        TeslaTelemetryCoordinator,
    )
    from custom_components.tesla_telemetry.proto import vehicle_data_pb2

    entry = MockConfigEntry(domain=DOMAIN, data={"vin": "5YJ3E1EA1PF000000"})
    entry.add_to_hass(hass)
    coordinator = TeslaTelemetryCoordinator(hass, "5YJ3E1EA1PF000000", "Test Car")

    # Published *before* the subscriber exists, so it can only reach the
    # evidence store via the setup-time replay of already-cached samples.
    value = vehicle_data_pb2.Value()
    value.double_value = 231.4
    coordinator.async_publish("ChargerVoltage", value)
    await hass.async_block_till_done()
    assert CONF_FIRMWARE_EVIDENCE not in entry.data

    unsub = _async_record_firmware_evidence(hass, entry, coordinator)
    await hass.async_block_till_done()

    assert entry.data[CONF_FIRMWARE_EVIDENCE]["proven"] == "2024.44.32"
    unsub()


async def test_a_sample_raises_the_stored_proof(hass) -> None:
    """End to end: publishing ChargerVoltage writes evidence to the entry."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.tesla_telemetry import (
        _async_record_firmware_evidence,
    )
    from custom_components.tesla_telemetry.const import DOMAIN
    from custom_components.tesla_telemetry.coordinator import (
        TeslaTelemetryCoordinator,
    )
    from custom_components.tesla_telemetry.proto import vehicle_data_pb2

    entry = MockConfigEntry(domain=DOMAIN, data={"vin": "5YJ3E1EA1PF000000"})
    entry.add_to_hass(hass)
    coordinator = TeslaTelemetryCoordinator(hass, "5YJ3E1EA1PF000000", "Test Car")

    unsub = _async_record_firmware_evidence(hass, entry, coordinator)

    value = vehicle_data_pb2.Value()
    value.double_value = 231.4
    coordinator.async_publish("ChargerVoltage", value)
    await hass.async_block_till_done()

    assert entry.data[CONF_FIRMWARE_EVIDENCE]["proven"] == "2024.44.32"
    unsub()


async def test_the_version_signal_is_recorded_as_the_reported_version(hass) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.tesla_telemetry import (
        _async_record_firmware_evidence,
    )
    from custom_components.tesla_telemetry.const import DOMAIN
    from custom_components.tesla_telemetry.coordinator import (
        TeslaTelemetryCoordinator,
    )
    from custom_components.tesla_telemetry.proto import vehicle_data_pb2

    entry = MockConfigEntry(domain=DOMAIN, data={"vin": "5YJ3E1EA1PF000000"})
    entry.add_to_hass(hass)
    coordinator = TeslaTelemetryCoordinator(hass, "5YJ3E1EA1PF000000", "Test Car")
    unsub = _async_record_firmware_evidence(hass, entry, coordinator)

    value = vehicle_data_pb2.Value()
    value.string_value = "2025.32.4 a1b2c3d"
    coordinator.async_publish("Version", value)
    await hass.async_block_till_done()

    assert entry.data[CONF_FIRMWARE_EVIDENCE]["reported"] == "2025.32.4 a1b2c3d"
    unsub()


async def test_an_unchanged_evidence_write_does_not_touch_the_entry(hass) -> None:
    """Evidence updates must be rare: every entry write runs the listener.

    Comparing `entry.data` before and after a repeat publish is not enough:
    Home Assistant's `async_update_entry` no-ops on an identical dict, so that
    comparison cannot tell "no write happened" apart from "a same-value write
    happened". This spies on `hass.config_entries.async_update_entry` itself
    and counts calls, so it actually exercises the `if updated == stored:
    return` guard in `_async_record_firmware_evidence` rather than one of its
    side effects.
    """
    from unittest.mock import Mock

    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.tesla_telemetry import (
        _async_record_firmware_evidence,
    )
    from custom_components.tesla_telemetry.const import DOMAIN
    from custom_components.tesla_telemetry.coordinator import (
        TeslaTelemetryCoordinator,
    )
    from custom_components.tesla_telemetry.proto import vehicle_data_pb2

    entry = MockConfigEntry(domain=DOMAIN, data={"vin": "5YJ3E1EA1PF000000"})
    entry.add_to_hass(hass)
    coordinator = TeslaTelemetryCoordinator(hass, "5YJ3E1EA1PF000000", "Test Car")
    unsub = _async_record_firmware_evidence(hass, entry, coordinator)

    real_update_entry = hass.config_entries.async_update_entry
    spy = Mock(side_effect=real_update_entry)
    hass.config_entries.async_update_entry = spy

    value = vehicle_data_pb2.Value()
    value.double_value = 231.4
    for _ in range(6):
        coordinator.async_publish("ChargerVoltage", value)
        await hass.async_block_till_done()

    assert spy.call_count == 1
    assert entry.data[CONF_FIRMWARE_EVIDENCE]["proven"] == "2024.44.32"
    unsub()
