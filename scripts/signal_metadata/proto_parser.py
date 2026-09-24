"""Parse Tesla's `enum Field` out of the vendored vehicle_data.proto.

Text parsing rather than the protobuf runtime, so the generator has no
dependency beyond the standard library and runs in a bare CI job.

Two comment styles carry a firmware floor and both are load-bearing:

    // fields 260-269 are first available in firmware version 2026.32
    GpsAccuracyMeters = 260;

    Hvil = 107;  // Requires firmware version 2024.26 or later

The range style covers 91 fields today and the per-field style 7. Missing
either means creating entities for signals a given car will never send.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_ENUM_RE = re.compile(r"^enum Field \{(.*?)^\}", re.DOTALL | re.MULTILINE)
_FIELD_RE = re.compile(r"^\s*([A-Za-z]\w*)\s*=\s*(\d+)\s*;(?:\s*//\s*(.*))?$", re.MULTILINE)
_RANGE_RE = re.compile(
    r"//\s*fields?\s+([\d,\s and\-]+?)\s+(?:are|is)\s+"
    r"first available in firmware version\s+([0-9.]+)"
)
_PER_FIELD_RE = re.compile(r"[Rr]equires firmware version\s+([0-9.]+)")
_SEMI_MARKER = "Semi-truck only"


@dataclass(frozen=True, slots=True)
class ProtoField:
    ids: dict[str, int]
    firmware: dict[int, str]
    semi_only: frozenset[int]


def _expand_range(spec: str) -> set[int]:
    """"180-183, 185-228" -> {180, 181, ..., 228}."""
    out: set[int] = set()
    for part in re.split(r",|\s+and\s+", spec):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            out |= set(range(int(start), int(end) + 1))
        elif part.isdigit():
            out.add(int(part))
    return out


def parse_field_enum(proto_text: str) -> ProtoField:
    match = _ENUM_RE.search(proto_text)
    if match is None:
        raise ValueError("no `enum Field` block found in the proto")
    body = match.group(1)

    firmware: dict[int, str] = {}
    for spec, version in _RANGE_RE.findall(body):
        for number in _expand_range(spec):
            firmware[number] = version

    ids: dict[str, int] = {}
    semi: set[int] = set()
    for name, raw_number, comment in _FIELD_RE.findall(body):
        number = int(raw_number)
        ids[name] = number
        if not comment:
            continue
        if _SEMI_MARKER in comment:
            semi.add(number)
        per_field = _PER_FIELD_RE.search(comment)
        if per_field:
            firmware[number] = per_field.group(1)

    if not ids:
        raise ValueError("`enum Field` parsed but contained no fields")
    return ProtoField(ids=ids, firmware=firmware, semi_only=frozenset(semi))
