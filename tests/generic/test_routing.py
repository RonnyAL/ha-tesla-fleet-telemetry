"""Which platform a datum belongs to.

Decided from the oneof arm Tesla actually sent, never from metadata: the
catalog says a signal exists and what Tesla documents it as, not that this
car sends it or in which arm.
"""
from __future__ import annotations

import pytest

pb = pytest.importorskip("custom_components.tesla_telemetry.proto.vehicle_data_pb2")

from custom_components.tesla_telemetry.generic.routing import platform_for


def test_boolean_becomes_a_binary_sensor() -> None:
    assert platform_for(pb.Value(boolean_value=True)) == "binary_sensor"


def test_location_becomes_a_device_tracker() -> None:
    value = pb.Value(location_value=pb.LocationValue(latitude=1.0, longitude=2.0))
    assert platform_for(value) == "device_tracker"


@pytest.mark.parametrize(
    "value",
    [
        pb.Value(shift_state_value=pb.ShiftStateP),
        pb.Value(double_value=12.5),
        pb.Value(int_value=3),
        pb.Value(long_value=4),
        pb.Value(float_value=1.5),
        pb.Value(string_value="2026.32"),
    ],
)
def test_everything_else_becomes_a_sensor(value: object) -> None:
    assert platform_for(value) == "sensor"


def test_invalid_creates_nothing() -> None:
    """An invalid first datum carries no type information, so it cannot
    decide a platform. Wait for a real one."""
    assert platform_for(pb.Value(invalid=True)) is None


def test_empty_value_creates_nothing() -> None:
    assert platform_for(pb.Value()) is None
