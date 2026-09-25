"""Every entity that existed before this phase still has a home.

For each entity captured before the change, its unique_id after upgrading is
either unchanged (curated, or already matching the rule) or listed in
LEGACY_UNIQUE_IDS. Anything else is an orphaned entity and a user's lost
history.
"""
from __future__ import annotations

import json
from pathlib import Path

from custom_components.tesla_telemetry.generic.claimed import CLAIMED_SIGNALS
from custom_components.tesla_telemetry.generic.naming import snake
from custom_components.tesla_telemetry.migration import LEGACY_UNIQUE_IDS

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/legacy_unique_ids.json"


def test_no_entity_is_orphaned() -> None:
    data = json.loads(FIXTURE.read_text())
    orphans = []
    for suffix, value in sorted(data.items()):
        signal = value["signal"]
        if not signal:
            continue                       # accounting entity, untouched
        if signal in CLAIMED_SIGNALS:
            continue                       # curated entity keeps its name
        if suffix == f"{snake(signal)}_telemetry":
            continue                       # already matches the rule
        if suffix in LEGACY_UNIQUE_IDS:
            continue                       # migrated
        orphans.append((suffix, signal))
    assert not orphans, (
        f"entities whose unique_id changes with no migration: {orphans}"
    )


def test_local_extras_is_gone() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "custom_components/tesla_telemetry/local_extras.py"
    )
    assert not path.exists(), "local_extras.py was a stopgap and should be removed"
