"""Tests for parsing Tesla's Field enum out of the vendored .proto.

Parsed as text rather than through the protobuf runtime so the generator
runs in a bare CI job. Two firmware comment styles exist and both matter:
a range block above a group of fields, and a per-field trailing comment.
"""
from __future__ import annotations

from pathlib import Path

from scripts.signal_metadata.proto_parser import parse_field_enum

SAMPLE = """
enum Field {
  Unknown = 0;
  VehicleSpeed = 4;
  Odometer = 5;
  SemitruckTpmsPressureRe1L0 = 73;  // Semi-truck only
  Hvil = 107;  // Requires firmware version 2024.26 or later

  // fields 260-269 are first available in firmware version 2026.32 (device client version 1.3.0)

  GpsAccuracyMeters = 260;
  RemoteStartActive = 268;
}
"""

_PROTO = (
    Path(__file__).resolve().parents[2]
    / "custom_components/tesla_telemetry/proto/schemas/vehicle_data.proto"
)


def test_ids_are_parsed() -> None:
    result = parse_field_enum(SAMPLE)
    assert result.ids["VehicleSpeed"] == 4
    assert result.ids["RemoteStartActive"] == 268
    assert result.ids["Unknown"] == 0


def test_range_firmware_comment_applies_to_every_field_in_range() -> None:
    result = parse_field_enum(SAMPLE)
    assert result.firmware[260] == "2026.32"
    assert result.firmware[268] == "2026.32"


def test_per_field_firmware_comment_is_parsed() -> None:
    """A second, easily-missed style: the floor is on the field's own line."""
    result = parse_field_enum(SAMPLE)
    assert result.firmware[107] == "2024.26"


def test_semi_truck_only_is_flagged() -> None:
    result = parse_field_enum(SAMPLE)
    assert 73 in result.semi_only
    assert 4 not in result.semi_only


def test_fields_without_a_floor_have_none() -> None:
    result = parse_field_enum(SAMPLE)
    assert 4 not in result.firmware


def test_parses_the_real_vendored_proto() -> None:
    """Counts measured on the pinned proto; a bump that changes them should
    be a visible, deliberate diff rather than a silent shift."""
    result = parse_field_enum(_PROTO.read_text())
    assert len(result.ids) == 270           # includes Unknown
    assert len(result.firmware) == 98       # 91 from ranges + 7 per-field
    assert len(result.semi_only) == 13
    assert result.ids["VehicleSpeed"] == 4
    assert result.firmware[268] == "2026.32"
