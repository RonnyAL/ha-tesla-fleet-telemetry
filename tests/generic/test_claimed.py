"""CLAIMED_SIGNALS must match what the curated entities actually subscribe to.

Derived by instantiating the real platforms rather than parsing source: one
class can produce several entities from one signal (DoorState feeds every
DoorBinarySensor), and source-parsing counts that as one.

A curated entity added without claiming its signal would silently get a
duplicate generic entity for the same data; a claim left behind after
deleting one would silently suppress a generic entity that should exist.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from custom_components.tesla_telemetry.generic.claimed import CLAIMED_SIGNALS

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/legacy_unique_ids.json"
REPO = Path(__file__).resolve().parents[2]


def test_every_claimed_signal_is_real() -> None:
    from custom_components.tesla_telemetry.signal_metadata import SIGNALS

    unknown = sorted(CLAIMED_SIGNALS - set(SIGNALS))
    assert not unknown, f"claimed signals that are not in the catalog: {unknown}"


def test_fan_out_signals_are_claimed() -> None:
    """A signal feeding more than one curated entity cannot be generic.

    Counts from the fixture's ``signals`` list (every signal an entity
    actually subscribes to at runtime), not the legacy single-valued
    ``signal`` field. ``signal`` is blank for composite/derived entities
    such as ``AvgBatteryTempSensor`` and both ``device_tracker`` entities, so
    counting by ``signal`` alone would miss fan-out through them entirely —
    e.g. ModuleTempMax/ModuleTempMin, which fan out only because
    ``AvgBatteryTempSensor`` subscribes to both alongside their own direct
    sensors.
    """
    data = json.loads(FIXTURE.read_text())
    counts: dict[str, int] = {}
    for value in data.values():
        for signal in value.get("signals", []):
            counts[signal] = counts.get(signal, 0) + 1
    fan_out = {s for s, c in counts.items() if c > 1}
    missing = sorted(fan_out - CLAIMED_SIGNALS)
    assert not missing, f"fan-out signals not claimed: {missing}"


def test_claimed_signals_exactly_match_what_curated_entities_subscribe_to() -> None:
    """The exhaustive test the design spec calls for.

    Derives the truth by introspection rather than trusting the frozen
    fixture (which predates this branch's entity deletions and is never
    regenerated): instantiates the real sensor/binary_sensor/device_tracker
    platforms and records every dispatcher topic each curated entity
    actually subscribes to in ``async_added_to_hass`` — reusing
    ``scripts/capture_legacy_unique_ids.py``'s ``_collect()``, which already
    does this rather than reading class-level ``_signal_name`` alone. That
    distinction matters: composite entities like ``AvgBatteryTempSensor``
    and both ``device_tracker`` entities subscribe to their signals at
    runtime and have no ``_signal_name`` of their own, so a test built on
    ``_signal_name`` would never see them.

    CLAIMED_SIGNALS must equal this set exactly. Extra means a stale claim
    is silently suppressing a generic entity that should exist (Critical 2);
    missing means a curated entity is racing a generic one over the same
    data, producing a duplicate and a "does not generate unique IDs" log
    line at every restart.
    """
    sys.path.insert(0, str(REPO))
    from scripts.capture_legacy_unique_ids import _collect

    data = asyncio.run(_collect())
    subscribed: set[str] = set()
    for entry in data.values():
        subscribed.update(entry.get("signals", []))

    assert subscribed == set(CLAIMED_SIGNALS)
