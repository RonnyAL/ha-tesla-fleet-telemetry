"""Render reconciled records as the generated metadata module.

The header format follows homeassistant/generated/*.py, which names the
command that rebuilds the file.
"""
from __future__ import annotations

from .reconcile import SignalRecord

_HEADER = '''"""Automatically generated file.

To update, run python3 scripts/gen_signal_metadata.py

Describes every signal in Tesla's telemetry catalog: its Field enum id, the
category and type Tesla documents, its unit and device/state class, and the
firmware floor below which a vehicle will never report it.

`value_type` is a hint. The entity type is decided from the Value oneof arm
the vehicle actually sends; this is what Tesla's documentation claims, which
is useful for the options UI and for validating units.

`enum_name` names the proto enum but not its members: those are read from the
live protobuf descriptor, so a proto bump cannot put them out of step with the
bindings actually decoding the stream.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SignalMeta:
    """Everything known about one telemetry signal."""

    field_id: int
    category: str | None
    value_type: str | None
    enum_name: str | None
    unit: str | None
    device_class: str | None
    state_class: str | None
    min_firmware: str | None
    semi_only: bool
    documented: bool


SIGNALS: dict[str, SignalMeta] = {
'''

_FOOTER = "}\n"


def render_module(records: list[SignalRecord]) -> str:
    lines = [_HEADER]
    for record in records:
        lines.append(
            f"    {record.name!r}: SignalMeta(\n"
            f"        field_id={record.field_id!r},\n"
            f"        category={record.category!r},\n"
            f"        value_type={record.value_type!r},\n"
            f"        enum_name={record.enum_name!r},\n"
            f"        unit={record.unit!r},\n"
            f"        device_class={record.device_class!r},\n"
            f"        state_class={record.state_class!r},\n"
            f"        min_firmware={record.min_firmware!r},\n"
            f"        semi_only={record.semi_only!r},\n"
            f"        documented={record.documented!r},\n"
            f"    ),\n"
        )
    lines.append(_FOOTER)
    return "".join(lines)
