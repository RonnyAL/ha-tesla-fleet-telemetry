"""Tests for the generator CLI.

--check is what runs on every pull request: it regenerates from the
committed inputs and fails if the committed output differs. That is what
catches a hand-edited generated file, or an overrides change without a
regeneration.

Nothing here writes to the tracked source tree. An earlier version did, and
it meant that a developer who hand-edited the generated file to confirm the
drift gate fires had that edit silently overwritten by running pytest — the
exact state the gate exists to catch. It also made the suite unsafe to run
in parallel, and put a deliberately-corrupted file one abort away from the
refresh workflow's `git add`.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from scripts import gen_signal_metadata
from scripts.gen_signal_metadata import main

REPO = Path(__file__).resolve().parents[2]
GENERATED = REPO / "custom_components/tesla_telemetry/signal_metadata.py"
SNAPSHOT = REPO / "signal_catalog/tesla_fields.json"


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the CLI's output paths at copies, leaving the real files alone."""
    generated = tmp_path / "signal_metadata.py"
    snapshot = tmp_path / "tesla_fields.json"
    shutil.copy(GENERATED, generated)
    shutil.copy(SNAPSHOT, snapshot)
    monkeypatch.setattr(gen_signal_metadata, "GENERATED", generated)
    monkeypatch.setattr(gen_signal_metadata, "SNAPSHOT", snapshot)
    return generated


def test_check_passes_against_the_committed_artifacts() -> None:
    """The committed output must match what the committed inputs produce.

    Deliberately not sandboxed: this asserts about the real tracked files,
    and --check only reads them.
    """
    assert main(["--check"]) == 0


def test_offline_regeneration_is_byte_identical(sandbox: Path) -> None:
    """Determinism: regenerating changes nothing."""
    before = sandbox.read_text()
    assert main(["--offline"]) == 0
    assert sandbox.read_text() == before


def test_offline_does_not_rewrite_the_snapshot(sandbox: Path) -> None:
    """--offline reads the vendored snapshot; only a live run rewrites it."""
    snapshot = sandbox.parent / "tesla_fields.json"
    before = snapshot.read_text()
    assert main(["--offline"]) == 0
    assert snapshot.read_text() == before


def test_check_fails_when_the_generated_file_is_stale(sandbox: Path) -> None:
    sandbox.write_text(sandbox.read_text().replace("field_id=4", "field_id=9999", 1))
    assert main(["--check"]) == 2


def test_check_fails_when_the_generated_file_is_missing(sandbox: Path) -> None:
    sandbox.unlink()
    assert main(["--check"]) == 2


def test_the_snapshot_is_committed_and_plausible() -> None:
    nodes = json.loads(SNAPSHOT.read_text())
    assert len(nodes) >= 200
    assert any(n["field_name"] == "VehicleSpeed" for n in nodes)


def test_running_the_suite_leaves_the_tracked_files_untouched() -> None:
    """The guard on the guard: if a future test forgets the sandbox fixture,
    this records what the real files looked like before it ran."""
    assert GENERATED.read_text().startswith('"""Automatically generated file.')
    assert json.loads(SNAPSHOT.read_text())
