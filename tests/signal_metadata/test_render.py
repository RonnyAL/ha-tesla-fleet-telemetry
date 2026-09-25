"""Tests for rendering the generated signal metadata module.

Byte-for-byte determinism matters more than it looks: the PR drift gate
compares generated output against the committed file, so any instability
(dict ordering, repr drift) would make CI fail at random.
"""
from __future__ import annotations

import ast

from scripts.signal_metadata.reconcile import SignalRecord
from scripts.signal_metadata.render import render_module

RECORDS = [
    SignalRecord(
        name="VehicleSpeed", field_id=4, category="Driving", value_type="real",
        enum_name=None, unit="mph", device_class="speed",
        state_class="measurement", enum_labels=None, min_firmware=None,
        semi_only=False, documented=True,
    ),
    SignalRecord(
        name="RemoteStartActive", field_id=268, category=None, value_type=None,
        enum_name=None, unit=None, device_class=None, state_class=None,
        enum_labels=None, min_firmware="2026.32", semi_only=False,
        documented=False,
    ),
]


def test_output_is_valid_python() -> None:
    ast.parse(render_module(RECORDS))


def test_header_names_the_regeneration_command() -> None:
    text = render_module(RECORDS)
    assert text.startswith('"""Automatically generated file.')
    assert "python3 scripts/gen_signal_metadata.py" in text


def test_rendering_is_deterministic() -> None:
    assert render_module(RECORDS) == render_module(list(RECORDS))


def test_records_round_trip() -> None:
    namespace: dict[str, object] = {}
    # S102: the "untrusted input" the rule guards against is source we
    # generated two lines up; executing it is the point of the test.
    exec(  # noqa: S102
        compile(render_module(RECORDS), "signal_metadata.py", "exec"), namespace
    )
    signals = namespace["SIGNALS"]
    assert signals["VehicleSpeed"].field_id == 4
    assert signals["VehicleSpeed"].unit == "mph"
    assert signals["RemoteStartActive"].min_firmware == "2026.32"
    assert signals["RemoteStartActive"].documented is False


def test_ends_with_a_single_newline() -> None:
    text = render_module(RECORDS)
    assert text.endswith("\n")
    assert not text.endswith("\n\n")
