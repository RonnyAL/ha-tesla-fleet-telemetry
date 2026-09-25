"""Tests for the derived on/off extractors, now living in ``values.py``.

These entities were removed upstream in v0.4.0 and restored by the local
patch. The restored versions route through ``value_as_short_enum``, which maps
an enum explicitly reporting Unknown/SNA to ``None`` — right for a sensor,
wrong for a binary sensor, where it turns a real answer into HA's ``unknown``
and any automation testing for ``off`` stops firing.

The behaviour pinned here is the pre-removal behaviour (commit ``c377e5d^``):

    HvacPowerBinarySensor:  is_on = name not in ("HvacPowerStateOff",
                                                 "HvacPowerStateUnknown")
    SentryArmedBinarySensor: "Idle/Off/Unknown are reported as off so the
                              dashboard chip lights up only when the car is
                              actively watching its surroundings."

Built on real protobuf ``Value`` messages rather than mocks, so a proto bump
that renames or renumbers an enum shows up here.
"""
from __future__ import annotations

import pytest

pb = pytest.importorskip(
    "custom_components.tesla_telemetry.proto.vehicle_data_pb2",
    reason="needs protobuf",
)

from custom_components.tesla_telemetry.values import (
    _hvac_running,
    _sentry_armed,
    value_as_short_enum,
)


# ---------------------------------------------------------------------------
# HVAC
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("HvacPowerStateOn", True),
        ("HvacPowerStatePrecondition", True),
        ("HvacPowerStateOverheatProtect", True),
        ("HvacPowerStateOff", False),
        # The regression: an explicit Unknown is "off", not HA `unknown`.
        ("HvacPowerStateUnknown", False),
    ],
)
def test_hvac_running(state: str, expected: bool) -> None:
    value = pb.Value(hvac_power_value=getattr(pb, state))
    assert _hvac_running(value) is expected


def test_hvac_running_is_none_only_when_unreadable() -> None:
    """A genuinely unreadable sample still has no answer."""
    assert _hvac_running(pb.Value(invalid=True)) is None
    assert _hvac_running(pb.Value()) is None


# ---------------------------------------------------------------------------
# Sentry
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("SentryModeStateArmed", True),
        ("SentryModeStateAware", True),
        ("SentryModeStatePanic", True),
        ("SentryModeStateOff", False),
        ("SentryModeStateIdle", False),
        ("SentryModeStateQuiet", False),
        # The regression, and what the original docstring called out by name.
        ("SentryModeStateUnknown", False),
    ],
)
def test_sentry_armed(state: str, expected: bool) -> None:
    value = pb.Value(sentry_mode_state_value=getattr(pb, state))
    assert _sentry_armed(value) is expected


@pytest.mark.parametrize("raw", [True, False])
def test_sentry_armed_accepts_a_plain_bool(raw: bool) -> None:
    """Older firmware sends sentry status as a bool instead of the enum."""
    assert _sentry_armed(pb.Value(boolean_value=raw)) is raw


def test_sentry_armed_is_none_when_unreadable() -> None:
    assert _sentry_armed(pb.Value(invalid=True)) is None
    assert _sentry_armed(pb.Value()) is None


# ---------------------------------------------------------------------------
# The helper's two modes
# ---------------------------------------------------------------------------
def test_short_enum_hides_unknown_by_default() -> None:
    """Sensors want nothing to display for an Unknown enum."""
    value = pb.Value(hvac_power_value=pb.HvacPowerStateUnknown)
    assert value_as_short_enum(value) is None


def test_short_enum_can_keep_unknown() -> None:
    """Binary sensors need to tell "car said Unknown" from "unreadable"."""
    value = pb.Value(hvac_power_value=pb.HvacPowerStateUnknown)
    assert value_as_short_enum(value, keep_unknown=True) == "unknown"


def test_short_enum_strips_the_common_prefix_and_snake_cases() -> None:
    value = pb.Value(hvac_power_value=pb.HvacPowerStateOverheatProtect)
    assert value_as_short_enum(value) == "overheat_protect"


def test_short_enum_returns_none_for_unreadable_samples() -> None:
    assert value_as_short_enum(pb.Value(invalid=True)) is None
    assert value_as_short_enum(pb.Value()) is None
    assert value_as_short_enum(pb.Value(), keep_unknown=True) is None
