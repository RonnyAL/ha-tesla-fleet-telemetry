"""Tests for merging proto, Tesla's catalog and the overrides.

The proto is authoritative for existence and id: a name absent from the
Field enum can never arrive on the wire. The catalog adds category, type
and enum name. Overrides add units.

"In the proto but undocumented" is a normal state, not an error — five
signals already shipped as defaults are in exactly that position.
"""
from __future__ import annotations

import pytest

from scripts.signal_metadata.proto_parser import ProtoField
from scripts.signal_metadata.reconcile import ReconcileError, reconcile
from signal_catalog.overrides import Override

PROTO = ProtoField(
    ids={
        "Unknown": 0,
        "VehicleSpeed": 4,
        "Locked": 10,
        "Deprecated_1": 162,
        "Experimental_1": 170,
        "ScheduledDepartureTime": 46,
        "SemitruckTpms": 73,
        "RemoteStartActive": 268,
    },
    firmware={268: "2026.32"},
    semi_only=frozenset({73}),
)

NODES = [
    {"field_name": "VehicleSpeed", "category": "Driving", "type": "real",
     "proto_enum_name": ""},
    {"field_name": "Locked", "category": "Vehicle State", "type": "boolean",
     "proto_enum_name": ""},
    {"field_name": "SemitruckTpms", "category": "Powertrain", "type": "real",
     "proto_enum_name": ""},
    {"field_name": "RemoteStartActive", "category": "Vehicle State",
     "type": "boolean", "proto_enum_name": ""},
]


def _by_name(records):
    return {r.name: r for r in records}


def test_documented_signals_are_merged() -> None:
    records, _ = reconcile(PROTO, NODES, {})
    speed = _by_name(records)["VehicleSpeed"]
    assert speed.field_id == 4
    assert speed.category == "Driving"
    assert speed.value_type == "real"
    assert speed.documented is True


def test_placeholders_are_excluded() -> None:
    records, _ = reconcile(PROTO, NODES, {})
    names = _by_name(records)
    assert "Deprecated_1" not in names
    assert "Experimental_1" not in names
    assert "Unknown" not in names


def test_undocumented_proto_signals_are_kept() -> None:
    """Not an error: five shipped defaults are in this state today."""
    records, _ = reconcile(PROTO, NODES, {})
    departure = _by_name(records)["ScheduledDepartureTime"]
    assert departure.documented is False
    assert departure.category is None
    assert departure.value_type is None


def test_docs_only_signal_is_excluded_and_warned() -> None:
    """The proto-drift detector: Tesla documents it, our proto lacks it."""
    nodes = [*NODES, {"field_name": "BrandNewSignal", "category": "Driving",
                      "type": "real", "proto_enum_name": ""}]
    records, warnings = reconcile(PROTO, nodes, {})
    assert "BrandNewSignal" not in _by_name(records)
    assert any("BrandNewSignal" in w for w in warnings)
    assert any("proto" in w.lower() for w in warnings)


def test_firmware_floor_and_semi_flag_are_carried() -> None:
    records, _ = reconcile(PROTO, NODES, {})
    names = _by_name(records)
    assert names["RemoteStartActive"].min_firmware == "2026.32"
    assert names["SemitruckTpms"].semi_only is True
    assert names["VehicleSpeed"].semi_only is False


def test_overrides_supply_units() -> None:
    overrides = {"VehicleSpeed": Override("mph", "speed", "measurement")}
    records, _ = reconcile(PROTO, NODES, overrides)
    speed = _by_name(records)["VehicleSpeed"]
    assert (speed.unit, speed.device_class, speed.state_class) == (
        "mph", "speed", "measurement"
    )


def test_stale_override_is_a_hard_error() -> None:
    with pytest.raises(ReconcileError, match="NoSuchSignal"):
        reconcile(PROTO, NODES, {"NoSuchSignal": Override("mph")})


def test_unit_on_a_boolean_is_a_hard_error() -> None:
    """Review Focus 5: an override contradicting the documented type."""
    with pytest.raises(ReconcileError, match="Locked"):
        reconcile(PROTO, NODES, {"Locked": Override("mph")})


def test_unknown_type_passes_through() -> None:
    """Review Focus 2: a type Tesla has not used before must not crash."""
    nodes = [{"field_name": "VehicleSpeed", "category": "Driving",
              "type": "quaternion", "proto_enum_name": ""}]
    records, _ = reconcile(PROTO, nodes, {})
    assert _by_name(records)["VehicleSpeed"].value_type == "quaternion"


def test_empty_strings_normalise_to_none() -> None:
    """Review Focus 4: Tesla ships "" for these, not null."""
    nodes = [{"field_name": "VehicleSpeed", "category": "", "type": "real",
              "proto_enum_name": ""}]
    records, _ = reconcile(PROTO, nodes, {})
    speed = _by_name(records)["VehicleSpeed"]
    assert speed.category is None
    assert speed.enum_name is None


def test_records_are_sorted_by_field_id() -> None:
    records, _ = reconcile(PROTO, NODES, {})
    assert [r.field_id for r in records] == sorted(r.field_id for r in records)


def test_unit_on_a_timestamp_is_a_hard_error() -> None:
    """A unit on a timestamp is as meaningless as a unit on a boolean.

    `time` and `timestamp` were missing from the guarded set, leaving most of
    the ways to mis-enter a hand-maintained override unchecked.
    """
    proto = ProtoField(ids={"RouteLastUpdated": 300}, firmware={}, semi_only=frozenset())
    nodes = [{"field_name": "RouteLastUpdated", "category": "Driving",
              "type": "timestamp", "proto_enum_name": ""}]
    with pytest.raises(ReconcileError, match="RouteLastUpdated"):
        reconcile(proto, nodes, {"RouteLastUpdated": Override("kWh", "energy", None)})


def test_unit_on_a_time_is_a_hard_error() -> None:
    proto = ProtoField(ids={"SomeTime": 301}, firmware={}, semi_only=frozenset())
    nodes = [{"field_name": "SomeTime", "category": "Driving", "type": "time",
              "proto_enum_name": ""}]
    with pytest.raises(ReconcileError, match="SomeTime"):
        reconcile(proto, nodes, {"SomeTime": Override("mi", "distance", None)})


def test_state_class_on_a_non_numeric_is_a_hard_error() -> None:
    """state_class describes how a number aggregates; it is meaningless on an
    enum or boolean, and setting one is the same class of typo as a bad unit."""
    with pytest.raises(ReconcileError, match="Locked"):
        reconcile(PROTO, NODES, {"Locked": Override(None, None, "measurement")})


def test_enum_device_class_on_an_enum_is_still_allowed() -> None:
    """The 20 enum signals legitimately carry device_class='enum'; the
    stricter rule must not reject them."""
    records, _ = reconcile(PROTO, NODES, {"Locked": Override(None, "enum", None)})
    assert _by_name(records)["Locked"].device_class == "enum"


def test_a_field_with_delta_advice_needs_an_override_entry() -> None:
    """Tesla adding delta advice to a new field must fail the gate, loudly.

    The values are hand-entered; only the detection is automatic. A silent
    miss would mean a field that needs a delta never gets one.
    """
    proto = ProtoField(
        ids={"ChargerVoltage": 5}, firmware={}, semi_only=frozenset()
    )
    nodes = [
        {
            "field_name": "ChargerVoltage",
            "category": "Charging",
            "type": "real",
            "proto_enum_name": "",
            "description": (
                "It is recommended to set minimum_delta, which is available "
                "on firmware version 2024.44.32 and later."
            ),
        }
    ]
    with pytest.raises(ReconcileError, match="minimum delta"):
        reconcile(proto, nodes, {})


def test_delta_advice_is_satisfied_by_any_of_the_four_attributes() -> None:
    """Required, Tesla's own default, 'recommended', and 'supported' all count."""
    proto = ProtoField(
        ids={"ChargerVoltage": 5}, firmware={}, semi_only=frozenset()
    )
    nodes = [
        {
            "field_name": "ChargerVoltage",
            "category": "Charging",
            "type": "real",
            "proto_enum_name": "",
            "description": "It is recommended to set minimum_delta.",
        }
    ]
    for override in (
        Override(minimum_delta_required=1.0),
        Override(minimum_delta_default=0.3),
        Override(minimum_delta_recommended=True),
        Override(minimum_delta_supported=True),
    ):
        records, _ = reconcile(proto, nodes, {"ChargerVoltage": override})
        assert len(records) == 1


def test_the_rule_matches_both_spellings() -> None:
    """Tesla writes both "minimum_delta" and "minimum delta"."""
    proto = ProtoField(ids={"InsideTemp": 7}, firmware={}, semi_only=frozenset())
    nodes = [
        {
            "field_name": "InsideTemp",
            "category": "Climate",
            "type": "real",
            "proto_enum_name": "",
            "description": "…setting a minimum delta is recommended.",
        }
    ]
    with pytest.raises(ReconcileError, match="minimum delta"):
        reconcile(proto, nodes, {})


def test_delta_attributes_reach_the_record() -> None:
    proto = ProtoField(
        ids={"SelfDrivingMilesSinceReset": 9}, firmware={}, semi_only=frozenset()
    )
    nodes = [
        {
            "field_name": "SelfDrivingMilesSinceReset",
            "category": "Safety",
            "type": "real",
            "proto_enum_name": "",
            "description": "This field requires minimum_delta to be set to >= 1.",
        }
    ]
    override = Override("mi", "distance", "total_increasing", minimum_delta_required=1.0)
    records, _ = reconcile(proto, nodes, {"SelfDrivingMilesSinceReset": override})
    assert records[0].minimum_delta_required == 1.0
    assert records[0].minimum_delta_default is None
    assert records[0].minimum_delta_recommended is False
