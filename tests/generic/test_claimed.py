"""CLAIMED_SIGNALS must match what the curated entities actually subscribe to.

Derived by instantiating the real platforms rather than parsing source: one
class can produce several entities from one signal (DoorState feeds every
DoorBinarySensor), and source-parsing counts that as one.

A curated entity added without claiming its signal would silently get a
duplicate generic entity for the same data; a claim left behind after
deleting one would silently suppress a generic entity that should exist.
"""
from __future__ import annotations

import json
from pathlib import Path

from custom_components.tesla_telemetry.generic.claimed import CLAIMED_SIGNALS

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/legacy_unique_ids.json"


def test_every_claimed_signal_is_real() -> None:
    from custom_components.tesla_telemetry.signal_metadata import SIGNALS

    unknown = sorted(CLAIMED_SIGNALS - set(SIGNALS))
    assert not unknown, f"claimed signals that are not in the catalog: {unknown}"


def test_fan_out_signals_are_claimed() -> None:
    """A signal feeding more than one curated entity cannot be generic."""
    data = json.loads(FIXTURE.read_text())
    counts: dict[str, int] = {}
    for value in data.values():
        if value["signal"]:
            counts[value["signal"]] = counts.get(value["signal"], 0) + 1
    fan_out = {s for s, c in counts.items() if c > 1}
    missing = sorted(fan_out - CLAIMED_SIGNALS)
    assert not missing, f"fan-out signals not claimed: {missing}"
