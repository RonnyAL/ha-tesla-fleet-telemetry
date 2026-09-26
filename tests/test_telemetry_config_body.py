"""Tests for the JSON body pushed to fleet_telemetry_config_create.

The byte-identity test is the important one. Tesla does not document what a
vehicle does with a per-field key its firmware predates, so a default
configuration must serialise exactly as it did before Phase 4 — an untouched
entry cannot be put at risk by a feature it never used.
"""
from __future__ import annotations

from types import SimpleNamespace

from custom_components.tesla_telemetry.const import CONF_SIGNAL_OVERRIDES
from custom_components.tesla_telemetry.services import (
    _config_fields,
    _fields_fingerprint,
)
from custom_components.tesla_telemetry.tesla_api import TelemetryFieldConfig


def entry(options=None, data=None):
    return SimpleNamespace(options=options or {}, data=data or {})


def test_an_unset_key_is_omitted_entirely() -> None:
    assert TelemetryFieldConfig(interval_seconds=60).to_dict() == {
        "interval_seconds": 60
    }


def test_set_keys_are_included() -> None:
    config = TelemetryFieldConfig(
        interval_seconds=60,
        minimum_delta=0.5,
        resend_interval_seconds=3600,
        include_fields=["VehicleSpeed"],
    )
    assert config.to_dict() == {
        "interval_seconds": 60,
        "minimum_delta": 0.5,
        "resend_interval_seconds": 3600,
        "include_fields": ["VehicleSpeed"],
    }


def test_an_empty_include_list_is_omitted() -> None:
    config = TelemetryFieldConfig(interval_seconds=60, include_fields=[])
    assert config.to_dict() == {"interval_seconds": 60}


def test_a_default_entry_serialises_with_intervals_only() -> None:
    """No new key may appear in a configuration nobody asked to change."""
    fields = _config_fields(entry())
    assert fields
    for name, body in fields.items():
        assert set(body) == {"interval_seconds"}, f"{name} gained {set(body)}"


def test_the_fingerprint_still_accepts_a_plain_interval_mapping() -> None:
    """The pre-Phase-4 call shape must keep producing the same digest."""
    plain = {"VehicleSpeed": 5, "Odometer": 300}
    expanded = {
        "VehicleSpeed": {"interval_seconds": 5},
        "Odometer": {"interval_seconds": 300},
    }
    assert _fields_fingerprint(plain) == _fields_fingerprint(expanded)


def test_changing_only_a_minimum_delta_changes_the_fingerprint() -> None:
    """Otherwise the options listener short-circuits and never re-pushes.

    This is the Phase 1 fingerprint bug in a new place: the digest has to
    cover everything that is actually sent.
    """
    before = {"InsideTemp": {"interval_seconds": 60}}
    after = {"InsideTemp": {"interval_seconds": 60, "minimum_delta": 0.5}}
    assert _fields_fingerprint(before) != _fields_fingerprint(after)


def test_changing_only_a_resend_interval_changes_the_fingerprint() -> None:
    before = {"Odometer": {"interval_seconds": 300}}
    after = {"Odometer": {"interval_seconds": 300, "resend_interval_seconds": 3600}}
    assert _fields_fingerprint(before) != _fields_fingerprint(after)


def test_changing_only_include_fields_changes_the_fingerprint() -> None:
    before = {"Odometer": {"interval_seconds": 300}}
    after = {
        "Odometer": {"interval_seconds": 300, "include_fields": ["VehicleSpeed"]}
    }
    assert _fields_fingerprint(before) != _fields_fingerprint(after)


def test_the_fingerprint_is_independent_of_key_order() -> None:
    a = {"Odometer": {"interval_seconds": 300, "minimum_delta": 0.1}}
    b = {"Odometer": {"minimum_delta": 0.1, "interval_seconds": 300}}
    assert _fields_fingerprint(a) == _fields_fingerprint(b)


def test_a_gated_key_does_not_reach_the_body() -> None:
    """No firmware evidence on the entry, so nothing new is sent."""
    options = {CONF_SIGNAL_OVERRIDES: {"InsideTemp": {"minimum_delta": 0.5}}}
    fields = _config_fields(entry(options=options))
    assert fields["InsideTemp"] == {"interval_seconds": fields["InsideTemp"]["interval_seconds"]}
    assert "minimum_delta" not in fields["InsideTemp"]


def test_evidence_on_the_entry_lets_a_key_through() -> None:
    options = {CONF_SIGNAL_OVERRIDES: {"InsideTemp": {"minimum_delta": 0.5}}}
    data = {"firmware_evidence": {"proven": "2026.32"}}
    fields = _config_fields(entry(options=options, data=data))
    assert fields["InsideTemp"]["minimum_delta"] == 0.5
