"""Tests for staleness, which resend intervals make answerable.

`STALE_INTERVAL_MULTIPLIER x interval_seconds` assumes a signal arrives at
least every interval. Push-on-change means it does not: an unchanged signal is
never resent, so a healthy entity can read `unavailable`. Today's behaviour is
kept deliberately — it is also the only sign a user gets that a car has gone
offline — but where a resend interval exists, it is the sound basis and is
used instead.
"""
from __future__ import annotations

from custom_components.tesla_telemetry.const import STALE_INTERVAL_MULTIPLIER
from custom_components.tesla_telemetry.coordinator import (
    TeslaTelemetryCoordinator,
)
from custom_components.tesla_telemetry.proto import vehicle_data_pb2

VIN = "5YJ3E1EA1PF000000"


def _coordinator(hass) -> TeslaTelemetryCoordinator:
    return TeslaTelemetryCoordinator(hass, VIN, "Test Car")


def _publish(coordinator, name: str) -> None:
    value = vehicle_data_pb2.Value()
    value.double_value = 1.0
    coordinator.async_publish(name, value)


async def test_a_fresh_sample_is_not_stale(hass) -> None:
    coordinator = _coordinator(hass)
    coordinator.effective_intervals = {"Odometer": 300}
    _publish(coordinator, "Odometer")
    assert not coordinator.is_stale("Odometer")


async def test_the_interval_basis_is_unchanged_without_a_resend(hass) -> None:
    coordinator = _coordinator(hass)
    coordinator.effective_intervals = {"Odometer": 300}
    _publish(coordinator, "Odometer")
    later = coordinator.get("Odometer").received_at + 300 * STALE_INTERVAL_MULTIPLIER + 1
    assert coordinator.is_stale("Odometer", now=later)


async def test_a_resend_interval_becomes_the_basis(hass) -> None:
    """With a resend the car really does send, so the question is answerable."""
    coordinator = _coordinator(hass)
    coordinator.effective_intervals = {"Odometer": 300}
    coordinator.resend_intervals = {"Odometer": 3600}
    _publish(coordinator, "Odometer")
    received = coordinator.get("Odometer").received_at

    # Well past the interval basis, nowhere near the resend basis.
    assert not coordinator.is_stale(
        "Odometer", now=received + 300 * STALE_INTERVAL_MULTIPLIER + 1
    )
    assert coordinator.is_stale(
        "Odometer", now=received + 3600 * STALE_INTERVAL_MULTIPLIER + 1
    )


async def test_a_signal_never_seen_is_stale(hass) -> None:
    assert _coordinator(hass).is_stale("Odometer")
