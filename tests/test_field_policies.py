"""Tests for six-layer field policy resolution.

The layering is the whole design, so each layer gets a test that can only
pass if it sits in the right place relative to its neighbours.
"""
from __future__ import annotations

from types import SimpleNamespace

from custom_components.tesla_telemetry.const import (
    CONF_ASSUME_FIRMWARE_SUPPORT,
    CONF_INTERVAL_PRESET,
    CONF_SIGNAL_OVERRIDES,
    DEFAULT_INTERVALS_SECONDS,
    DEFAULT_NEW_SIGNAL_INTERVAL,
)
from custom_components.tesla_telemetry.firmware import FirmwareEvidence
from custom_components.tesla_telemetry.signals import (
    active_preset,
    resolve_effective_intervals,
    resolve_field_policies,
    signal_overrides,
)

MODERN = FirmwareEvidence(proven_version="2026.32")
ANCIENT = FirmwareEvidence()


def entry(options=None, data=None):
    return SimpleNamespace(options=options or {}, data=data or {})


# --- layer 1: defaults -----------------------------------------------------

def test_an_untouched_entry_resolves_to_the_built_in_defaults() -> None:
    policies = resolve_field_policies(entry(), MODERN)
    assert set(policies) == set(DEFAULT_INTERVALS_SECONDS)
    for name, interval in DEFAULT_INTERVALS_SECONDS.items():
        assert policies[name].interval_seconds == interval
        assert policies[name].minimum_delta is None
        assert policies[name].resend_interval_seconds is None
        assert policies[name].include_fields == ()


# --- layer 2: preset -------------------------------------------------------

def test_a_preset_retunes_an_enabled_signal_by_category() -> None:
    policies = resolve_field_policies(
        entry(options={CONF_INTERVAL_PRESET: "live"}), MODERN
    )
    # VehicleSpeed is category Driving, which `live` sets to 1s.
    assert policies["VehicleSpeed"].interval_seconds == 1


def test_a_preset_never_enables_a_signal() -> None:
    before = resolve_field_policies(entry(), MODERN)
    after = resolve_field_policies(
        entry(options={CONF_INTERVAL_PRESET: "eco"}), MODERN
    )
    assert set(before) == set(after)


def test_the_default_preset_changes_nothing() -> None:
    plain = resolve_field_policies(entry(), MODERN)
    explicit = resolve_field_policies(
        entry(options={CONF_INTERVAL_PRESET: "default"}), MODERN
    )
    assert plain == explicit


def test_a_preset_stored_by_the_legacy_service_on_data_is_honoured() -> None:
    """`set_interval_preset` used to write entry.data."""
    policies = resolve_field_policies(
        entry(data={CONF_INTERVAL_PRESET: "high_rate"}), MODERN
    )
    assert policies["Location"].interval_seconds == 1


def test_options_beat_data_for_the_preset() -> None:
    policies = resolve_field_policies(
        entry(
            options={CONF_INTERVAL_PRESET: "default"},
            data={CONF_INTERVAL_PRESET: "high_rate"},
        ),
        MODERN,
    )
    assert policies["Location"].interval_seconds == (
        DEFAULT_INTERVALS_SECONDS["Location"]
    )


def test_an_unknown_preset_falls_back_to_default() -> None:
    policies = resolve_field_policies(
        entry(options={CONF_INTERVAL_PRESET: "from_the_future"}), MODERN
    )
    assert policies["VehicleSpeed"].interval_seconds == (
        DEFAULT_INTERVALS_SECONDS["VehicleSpeed"]
    )


# --- layer 3: the three interval states ------------------------------------

def test_a_pinned_interval_beats_the_preset() -> None:
    policies = resolve_field_policies(
        entry(
            options={
                CONF_INTERVAL_PRESET: "live",
                CONF_SIGNAL_OVERRIDES: {"VehicleSpeed": {"interval_seconds": 42}},
            }
        ),
        MODERN,
    )
    assert policies["VehicleSpeed"].interval_seconds == 42


def test_an_inherit_override_enables_a_signal_and_follows_the_preset() -> None:
    """The picker stores {} — enabled, no interval chosen."""
    options = {
        CONF_INTERVAL_PRESET: "live",
        CONF_SIGNAL_OVERRIDES: {"Hvil": {}},
    }
    policies = resolve_field_policies(entry(options=options), MODERN)
    assert "Hvil" in policies
    # Hvil is category Powertrain, which `live` sets to 5s.
    assert policies["Hvil"].interval_seconds == 5


def test_an_inherit_override_with_no_preset_uses_the_new_signal_default() -> None:
    policies = resolve_field_policies(
        entry(options={CONF_SIGNAL_OVERRIDES: {"Hvil": {}}}), MODERN
    )
    assert policies["Hvil"].interval_seconds == DEFAULT_NEW_SIGNAL_INTERVAL


def test_zero_disables_a_default_on_signal() -> None:
    policies = resolve_field_policies(
        entry(
            options={CONF_SIGNAL_OVERRIDES: {"VehicleSpeed": {"interval_seconds": 0}}}
        ),
        MODERN,
    )
    assert "VehicleSpeed" not in policies


def test_zero_disables_even_under_a_preset() -> None:
    policies = resolve_field_policies(
        entry(
            options={
                CONF_INTERVAL_PRESET: "live",
                CONF_SIGNAL_OVERRIDES: {"VehicleSpeed": {"interval_seconds": 0}},
            }
        ),
        MODERN,
    )
    assert "VehicleSpeed" not in policies


# --- the legacy stored shape -----------------------------------------------

def test_a_bare_int_override_means_a_pinned_interval() -> None:
    """Every existing user's options are {signal: int}."""
    legacy = resolve_field_policies(
        entry(options={CONF_SIGNAL_OVERRIDES: {"VehicleSpeed": 42}}), MODERN
    )
    modern = resolve_field_policies(
        entry(
            options={CONF_SIGNAL_OVERRIDES: {"VehicleSpeed": {"interval_seconds": 42}}}
        ),
        MODERN,
    )
    assert legacy == modern


def test_a_bare_zero_still_disables() -> None:
    policies = resolve_field_policies(
        entry(options={CONF_SIGNAL_OVERRIDES: {"VehicleSpeed": 0}}), MODERN
    )
    assert "VehicleSpeed" not in policies


def test_an_interval_stored_as_a_string_is_coerced() -> None:
    """A YAML round trip or a form coercion can produce "60"."""
    policies = resolve_field_policies(
        entry(options={CONF_SIGNAL_OVERRIDES: {"VehicleSpeed": "42"}}), MODERN
    )
    assert policies["VehicleSpeed"].interval_seconds == 42


def test_an_unparseable_interval_is_ignored_rather_than_raising() -> None:
    policies = resolve_field_policies(
        entry(options={CONF_SIGNAL_OVERRIDES: {"VehicleSpeed": "soon"}}), MODERN
    )
    assert policies["VehicleSpeed"].interval_seconds == (
        DEFAULT_INTERVALS_SECONDS["VehicleSpeed"]
    )


def test_an_override_for_a_signal_no_longer_in_the_catalog_is_skipped() -> None:
    """A proto bump can remove a field a stored override still names."""
    policies = resolve_field_policies(
        entry(options={CONF_SIGNAL_OVERRIDES: {"NotASignal": {"interval_seconds": 30}}}),
        MODERN,
    )
    assert "NotASignal" not in policies


# --- normalisation hygiene --------------------------------------------------

def test_a_fractional_interval_never_lands_on_the_disable_sentinel() -> None:
    """0.5 must not truncate to 0, the interval-0 disable sentinel."""
    policies = resolve_field_policies(
        entry(options={CONF_SIGNAL_OVERRIDES: {"VehicleSpeed": {"interval_seconds": 0.5}}}),
        MODERN,
    )
    assert "VehicleSpeed" in policies
    assert policies["VehicleSpeed"].interval_seconds == 1


def test_a_bare_string_include_fields_is_rejected_not_exploded() -> None:
    """A string is iterable; naively iterating it yields single characters."""
    options = {CONF_SIGNAL_OVERRIDES: {"Odometer": {"include_fields": "VehicleSpeed"}}}
    policies = resolve_field_policies(entry(options=options), MODERN)
    assert policies["Odometer"].include_fields == ()


def test_include_fields_deduplicates_preserving_first_seen_order() -> None:
    options = {
        CONF_SIGNAL_OVERRIDES: {
            "Odometer": {"include_fields": ["VehicleSpeed", "Soc", "VehicleSpeed"]}
        }
    }
    policies = resolve_field_policies(entry(options=options), MODERN)
    assert policies["Odometer"].include_fields == ("VehicleSpeed", "Soc")


# --- layer 4: required deltas ----------------------------------------------

def test_a_required_delta_is_applied_when_the_user_sets_none() -> None:
    policies = resolve_field_policies(
        entry(options={CONF_SIGNAL_OVERRIDES: {"SelfDrivingMilesSinceReset": {}}}),
        MODERN,
    )
    assert policies["SelfDrivingMilesSinceReset"].minimum_delta == 1.0


def test_a_user_delta_above_the_required_floor_is_kept() -> None:
    policies = resolve_field_policies(
        entry(
            options={
                CONF_SIGNAL_OVERRIDES: {
                    "SelfDrivingMilesSinceReset": {"minimum_delta": 5}
                }
            }
        ),
        MODERN,
    )
    assert policies["SelfDrivingMilesSinceReset"].minimum_delta == 5.0


def test_a_user_delta_below_the_required_floor_is_raised_to_it() -> None:
    policies = resolve_field_policies(
        entry(
            options={
                CONF_SIGNAL_OVERRIDES: {
                    "SelfDrivingMilesSinceReset": {"minimum_delta": 0.2}
                }
            }
        ),
        MODERN,
    )
    assert policies["SelfDrivingMilesSinceReset"].minimum_delta == 1.0


def test_a_zero_delta_is_treated_as_unset() -> None:
    """Zero is meaningless as a delta; sending the key would do nothing."""
    policies = resolve_field_policies(
        entry(
            options={CONF_SIGNAL_OVERRIDES: {"InsideTemp": {"minimum_delta": 0}}}
        ),
        MODERN,
    )
    assert policies["InsideTemp"].minimum_delta is None


def test_tesla_s_own_default_delta_is_not_sent() -> None:
    """The car applies 0.3 to ChargerVoltage itself; we do not restate it."""
    policies = resolve_field_policies(entry(), MODERN)
    assert policies["ChargerVoltage"].minimum_delta is None


# --- include_fields hygiene ------------------------------------------------

def test_include_fields_naming_a_disabled_signal_is_dropped() -> None:
    options = {
        CONF_SIGNAL_OVERRIDES: {
            "Odometer": {"include_fields": ["VehicleSpeed"]},
            "VehicleSpeed": {"interval_seconds": 0},
        }
    }
    policies = resolve_field_policies(entry(options=options), MODERN)
    assert policies["Odometer"].include_fields == ()


def test_include_fields_naming_an_unknown_signal_is_dropped() -> None:
    options = {CONF_SIGNAL_OVERRIDES: {"Odometer": {"include_fields": ["Nope"]}}}
    policies = resolve_field_policies(entry(options=options), MODERN)
    assert policies["Odometer"].include_fields == ()


def test_a_signal_cannot_include_itself() -> None:
    options = {CONF_SIGNAL_OVERRIDES: {"Odometer": {"include_fields": ["Odometer"]}}}
    policies = resolve_field_policies(entry(options=options), MODERN)
    assert policies["Odometer"].include_fields == ()


def test_a_valid_include_survives() -> None:
    options = {CONF_SIGNAL_OVERRIDES: {"Odometer": {"include_fields": ["VehicleSpeed"]}}}
    policies = resolve_field_policies(entry(options=options), MODERN)
    assert policies["Odometer"].include_fields == ("VehicleSpeed",)


# --- layer 5: the firmware gate --------------------------------------------

def test_old_firmware_loses_every_new_key() -> None:
    options = {
        CONF_SIGNAL_OVERRIDES: {
            "InsideTemp": {
                "minimum_delta": 0.5,
                "resend_interval_seconds": 3600,
                "include_fields": ["VehicleSpeed"],
            }
        }
    }
    policy = resolve_field_policies(entry(options=options), ANCIENT)["InsideTemp"]
    assert policy.minimum_delta is None
    assert policy.resend_interval_seconds is None
    assert policy.include_fields == ()
    # The interval is never gated: InsideTemp keeps its default, 30s.
    assert policy.interval_seconds == 30


def test_the_delta_floor_unlocks_delta_and_resend_but_not_include() -> None:
    evidence = FirmwareEvidence(proven_version="2024.44.32")
    options = {
        CONF_SIGNAL_OVERRIDES: {
            "InsideTemp": {
                "minimum_delta": 0.5,
                "resend_interval_seconds": 3600,
                "include_fields": ["VehicleSpeed"],
            }
        }
    }
    policy = resolve_field_policies(entry(options=options), evidence)["InsideTemp"]
    assert policy.minimum_delta == 0.5
    assert policy.resend_interval_seconds == 3600
    assert policy.include_fields == ()


def test_a_required_delta_is_dropped_without_evidence() -> None:
    """SelfDrivingMilesSinceReset never reports without minimum_delta >= 1.

    A fresh entry has proven nothing, so the resolver never gets to apply the
    required delta — the gate keys on proven evidence, not on the field's own
    ``min_firmware``. Pushing the field anyway would be a dead entity the
    vehicle silently never populates. Dropping it is self-healing: proven
    evidence is written to ``entry.data``, which triggers a re-push, so the
    signal reappears on its own once the car demonstrates support.
    """
    policies = resolve_field_policies(
        entry(options={CONF_SIGNAL_OVERRIDES: {"SelfDrivingMilesSinceReset": {}}})
    )
    assert "SelfDrivingMilesSinceReset" not in policies


def test_a_required_delta_appears_once_firmware_proves_support() -> None:
    evidence = FirmwareEvidence(proven_version="2025.44.25.5")
    policies = resolve_field_policies(
        entry(options={CONF_SIGNAL_OVERRIDES: {"SelfDrivingMilesSinceReset": {}}}),
        evidence,
    )
    assert policies["SelfDrivingMilesSinceReset"].minimum_delta == 1.0


def test_assume_support_defeats_the_gate() -> None:
    options = {
        CONF_ASSUME_FIRMWARE_SUPPORT: True,
        CONF_SIGNAL_OVERRIDES: {"InsideTemp": {"minimum_delta": 0.5}},
    }
    from custom_components.tesla_telemetry.firmware import evidence_from_entry

    e = entry(options=options)
    policy = resolve_field_policies(e, evidence_from_entry(e))["InsideTemp"]
    assert policy.minimum_delta == 0.5


def test_evidence_defaults_to_nothing_proven() -> None:
    """Called without evidence, resolution must gate, not open."""
    options = {CONF_SIGNAL_OVERRIDES: {"InsideTemp": {"minimum_delta": 0.5}}}
    assert resolve_field_policies(entry(options=options))["InsideTemp"].minimum_delta is None


# --- the compatibility wrapper ---------------------------------------------

def test_resolve_effective_intervals_still_returns_plain_ints() -> None:
    intervals = resolve_effective_intervals(entry())
    assert intervals == DEFAULT_INTERVALS_SECONDS


def test_signal_overrides_normalises_every_stored_shape() -> None:
    e = entry(
        options={
            CONF_SIGNAL_OVERRIDES: {
                "VehicleSpeed": 42,
                "Odometer": {"interval_seconds": 30, "minimum_delta": 0.1},
                "Hvil": {},
            }
        }
    )
    assert signal_overrides(e) == {
        "VehicleSpeed": {"interval_seconds": 42},
        "Odometer": {"interval_seconds": 30, "minimum_delta": 0.1},
        "Hvil": {},
    }


def test_active_preset_normalises() -> None:
    assert active_preset(entry()) == "default"
    assert active_preset(entry(options={CONF_INTERVAL_PRESET: "eco"})) == "eco"
    assert active_preset(entry(options={CONF_INTERVAL_PRESET: "bogus"})) == "default"
