# tests/generic/test_entities.py
"""The generic entity classes.

Built on real protobuf Value messages rather than mocks, so a proto bump that
renames or renumbers an enum fails here.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pb = pytest.importorskip("custom_components.tesla_telemetry.proto.vehicle_data_pb2")

from custom_components.tesla_telemetry.generic.entities import (
    GenericBinarySensor,
    GenericSensor,
    GenericTracker,
)
from custom_components.tesla_telemetry.signal_metadata import SignalMeta

VIN = "TESTVIN0000000001"


def _coordinator():
    return SimpleNamespace(vin=VIN, device_info={}, get=lambda name: None)


def _meta(**kw) -> SignalMeta:
    base = {
        "field_id": 1, "category": None, "value_type": None, "enum_name": None,
        "unit": None, "device_class": None, "state_class": None,
        "enum_labels": None, "min_firmware": None, "semi_only": False,
        "documented": True,
    }
    base.update(kw)
    return SignalMeta(**base)


def _sample(value):
    return SimpleNamespace(value=value, received_at=0.0, payload_created_at=None)


def test_numeric_sensor_carries_metadata() -> None:
    meta = _meta(unit="mph", device_class="speed", state_class="measurement")
    entity = GenericSensor(_coordinator(), "VehicleSpeed", meta)
    assert entity.unique_id == f"{VIN}_vehicle_speed_telemetry"
    assert entity.name == "Vehicle speed"
    assert entity.native_unit_of_measurement == "mph"
    entity._handle(_sample(pb.Value(double_value=42.5)))
    assert entity.native_value == 42.5


def test_enum_sensor_uses_snake_case_by_default() -> None:
    entity = GenericSensor(_coordinator(), "Gear", _meta(enum_name="ShiftState"))
    entity._handle(_sample(pb.Value(shift_state_value=pb.ShiftStateD)))
    assert entity.native_value == "d"


def test_enum_labels_override_the_default() -> None:
    """What keeps Gear reporting "P" instead of "p"."""
    meta = _meta(enum_name="ShiftState",
                 enum_labels={"ShiftStateP": "P", "ShiftStateD": "D"})
    entity = GenericSensor(_coordinator(), "Gear", meta)
    entity._handle(_sample(pb.Value(shift_state_value=pb.ShiftStateP)))
    assert entity.native_value == "P"
    assert entity.options is not None
    assert "P" in entity.options and "p" not in entity.options


def test_unknown_enum_member_is_none_not_an_option_violation() -> None:
    """Review Focus 2: a member the vendored proto does not know."""
    entity = GenericSensor(_coordinator(), "Gear", _meta(enum_name="ShiftState"))
    entity._handle(_sample(pb.Value(shift_state_value=pb.ShiftStateUnknown)))
    assert entity.native_value is None
    assert "unknown" not in (entity.options or [])


def test_invalid_makes_an_existing_entity_unavailable() -> None:
    entity = GenericSensor(_coordinator(), "VehicleSpeed", _meta(unit="mph"))
    entity._handle(_sample(pb.Value(double_value=10.0)))
    assert entity.available is True
    entity._handle(_sample(pb.Value(invalid=True)))
    assert entity.available is False


def test_a_datum_in_an_unrepresentable_arm_keeps_the_last_value() -> None:
    """Review Focus 1: sentry is boolean on old firmware, enum on new.

    The platform is fixed at creation — changing it would lose history — so an
    arm the entity cannot represent must leave the last value alone rather
    than crash or flap to unavailable.
    """
    entity = GenericBinarySensor(_coordinator(), "SentryMode", _meta())
    entity._handle(_sample(pb.Value(boolean_value=True)))
    assert entity.is_on is True
    entity._handle(_sample(pb.Value(string_value="nonsense")))
    assert entity.is_on is True
    assert entity.available is True


def test_binary_sensor_reads_a_boolean() -> None:
    entity = GenericBinarySensor(_coordinator(), "Locked", _meta())
    entity._handle(_sample(pb.Value(boolean_value=False)))
    assert entity.is_on is False


def test_numeric_sensor_keeps_last_value_on_an_unrepresentable_arm() -> None:
    """Fix round 1: GenericSensor must not overwrite a good reading with the
    None that value_as_string() returns for an arm it cannot represent.

    boolean_value and location_value are reserved for GenericBinarySensor
    and GenericTracker respectively; a GenericSensor seeing either (e.g. a
    signal that changes representation across firmware) must leave the last
    numeric value in place rather than clobber it.
    """
    entity = GenericSensor(_coordinator(), "VehicleSpeed", _meta(unit="mph"))
    entity._handle(_sample(pb.Value(double_value=42.5)))
    assert entity.native_value == 42.5

    entity._handle(_sample(pb.Value(boolean_value=True)))
    assert entity.native_value == 42.5
    assert entity.available is True

    entity._handle(_sample(pb.Value(
        location_value=pb.LocationValue(latitude=1.0, longitude=2.0)
    )))
    assert entity.native_value == 42.5
    assert entity.available is True


def test_tracker_keeps_last_location_on_an_unrepresentable_arm() -> None:
    """GenericTracker already behaves correctly; this pins that behaviour."""
    entity = GenericTracker(_coordinator(), "Location", _meta())
    entity._handle(_sample(pb.Value(
        location_value=pb.LocationValue(latitude=1.0, longitude=2.0)
    )))
    assert entity.latitude == 1.0
    assert entity.longitude == 2.0

    entity._handle(_sample(pb.Value(double_value=42.5)))
    assert entity.latitude == 1.0
    assert entity.longitude == 2.0
    assert entity.available is True
