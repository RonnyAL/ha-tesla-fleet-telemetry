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
def test_proof_only_ever_rises(stored, arriving, expected) -> None:
    from custom_components.tesla_telemetry.firmware import at_least

    incoming = proof_from_signals([arriving])
    best = stored
    if incoming is not None and (best is None or at_least(incoming, best)):
        best = incoming
    assert best == expected


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
    """Evidence updates must be rare: every entry write runs the listener."""
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
    first = dict(entry.data)

    coordinator.async_publish("ChargerVoltage", value)
    await hass.async_block_till_done()

    assert entry.data == first
    unsub()
