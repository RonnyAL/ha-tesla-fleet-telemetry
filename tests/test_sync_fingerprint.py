"""Tests for the pushed-config fingerprint (``services._fields_fingerprint``).

The integration used to assume, at every setup, that the vehicle already held
whatever config currently resolves. That assumption is wrong exactly when it
matters most: an integration update that changes DEFAULT_INTERVALS_SECONDS
leaves the car on the old field set, while the in-memory "already pushed"
marker says otherwise — so the options-change listener short-circuited and the
newly defaulted signals were never requested. Their entities sat at ``unknown``
until the >7-day auto-resync happened to fire.

The fingerprint of what was actually pushed is now persisted on the config
entry, so a changed field set is detectable across restarts.

The digest itself is pure, and ``services.py`` imports cleanly now that Home
Assistant is a test dependency, so these call the real function directly.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from custom_components.tesla_telemetry.services import _fields_fingerprint


def _digest(mapping: dict[str, int]) -> str:
    payload = json.dumps(sorted(mapping.items()), separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def test_fingerprint_is_stable_and_order_independent() -> None:
    a = {"VehicleSpeed": 5, "BatteryLevel": 10, "Odometer": 300}
    b = {"Odometer": 300, "VehicleSpeed": 5, "BatteryLevel": 10}
    assert _fields_fingerprint(a) == _fields_fingerprint(b) == _digest(a)


def test_adding_a_signal_changes_the_fingerprint() -> None:
    """The upgrade case: new defaults must be detectable as a change."""
    before = {"VehicleSpeed": 5, "BatteryLevel": 10}
    after = {**before, "Odometer": 300}
    assert _fields_fingerprint(before) != _fields_fingerprint(after)


def test_retuning_an_interval_changes_the_fingerprint() -> None:
    before = {"Odometer": 60}
    after = {"Odometer": 300}
    assert _fields_fingerprint(before) != _fields_fingerprint(after)


def test_removing_a_signal_changes_the_fingerprint() -> None:
    before = {"VehicleSpeed": 5, "BatteryLevel": 10}
    after = {"VehicleSpeed": 5}
    assert _fields_fingerprint(before) != _fields_fingerprint(after)


def test_identical_config_keeps_the_same_fingerprint() -> None:
    """Otherwise every options save would re-push, and the last_sync stamp
    would retrigger the update listener in a loop."""
    config = {"VehicleSpeed": 5, "BatteryLevel": 10}
    assert _fields_fingerprint(config) == _fields_fingerprint(dict(config))


def test_empty_config_is_handled() -> None:
    assert _fields_fingerprint({}) == hashlib.sha256(b"[]").hexdigest()


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ({"A": 1, "B": 2}, {"A": 2, "B": 1}),
        ({"AB": 1}, {"A": 1, "B": 1}),
        ({"A=1;B": 2}, {"A": 1, "B": 2}),
    ],
)
def test_distinct_configs_do_not_collide(
    left: dict[str, int], right: dict[str, int]
) -> None:
    """Includes separator-confusion cases, where a naive join would collide."""
    assert _fields_fingerprint(left) != _fields_fingerprint(right)


def test_fingerprint_matches_the_real_default_config() -> None:
    """Sanity check against the actual resolved defaults, not a toy mapping."""
    from custom_components.tesla_telemetry.const import DEFAULT_INTERVALS_SECONDS

    assert _fields_fingerprint(DEFAULT_INTERVALS_SECONDS) == _digest(
        dict(DEFAULT_INTERVALS_SECONDS)
    )
    # A single retuned signal has to be visible.
    mutated = {**DEFAULT_INTERVALS_SECONDS, "Odometer": 1}
    assert _fields_fingerprint(mutated) != _fields_fingerprint(
        DEFAULT_INTERVALS_SECONDS
    )
