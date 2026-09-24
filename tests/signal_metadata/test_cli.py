"""Tests for the generator CLI.

--check is what runs on every pull request: it regenerates from the
committed inputs and fails if the committed output differs. That is what
catches a hand-edited generated file, or an overrides change without a
regeneration.
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts.gen_signal_metadata import main

REPO = Path(__file__).resolve().parents[2]
GENERATED = REPO / "custom_components/tesla_telemetry/signal_metadata.py"
SNAPSHOT = REPO / "signal_catalog/tesla_fields.json"


def test_check_passes_against_the_committed_artifacts() -> None:
    """The committed output must match what the committed inputs produce."""
    assert main(["--check"]) == 0


def test_offline_regeneration_is_byte_identical() -> None:
    """Determinism: regenerating changes nothing."""
    before = GENERATED.read_text()
    assert main(["--offline"]) == 0
    assert GENERATED.read_text() == before


def test_check_fails_when_the_generated_file_is_stale() -> None:
    original = GENERATED.read_text()
    try:
        GENERATED.write_text(original.replace("field_id=4", "field_id=9999", 1))
        assert main(["--check"]) == 2
    finally:
        GENERATED.write_text(original)


def test_the_snapshot_is_committed_and_plausible() -> None:
    nodes = json.loads(SNAPSHOT.read_text())
    assert len(nodes) >= 200
    assert any(n["field_name"] == "VehicleSpeed" for n in nodes)
