"""Tests for the cost bounds.

`interval_seconds` is a rate ceiling and Tesla pushes on change, so a parked
car emits almost nothing. None of these numbers is an estimate: two are
bounds and one is a measurement, and the UI labels them that way.
"""
from __future__ import annotations

import pytest

from custom_components.tesla_telemetry.cost import (
    SECONDS_PER_MONTH,
    monthly_ceiling,
    monthly_floor,
    projected_monthly_signals,
    signals_to_cost,
)
from custom_components.tesla_telemetry.signals import FieldPolicy


def test_a_month_is_thirty_days() -> None:
    assert SECONDS_PER_MONTH == 2_592_000


def test_the_ceiling_is_one_emission_per_interval() -> None:
    policies = {"A": FieldPolicy(interval_seconds=60)}
    assert monthly_ceiling(policies) == SECONDS_PER_MONTH / 60


def test_the_ceiling_sums_over_signals() -> None:
    policies = {
        "A": FieldPolicy(interval_seconds=60),
        "B": FieldPolicy(interval_seconds=30),
    }
    expected = SECONDS_PER_MONTH / 60 + SECONDS_PER_MONTH / 30
    assert monthly_ceiling(policies) == expected


def test_included_fields_multiply_the_payload() -> None:
    """Each included field is another billed data point per publication."""
    plain = {"A": FieldPolicy(interval_seconds=60)}
    with_two = {
        "A": FieldPolicy(interval_seconds=60, include_fields=("B", "C"))
    }
    assert monthly_ceiling(with_two) == 3 * monthly_ceiling(plain)


def test_the_floor_counts_only_signals_with_a_resend() -> None:
    policies = {
        "A": FieldPolicy(interval_seconds=60),
        "B": FieldPolicy(interval_seconds=60, resend_interval_seconds=3600),
    }
    assert monthly_floor(policies) == SECONDS_PER_MONTH / 3600


def test_the_floor_is_zero_without_any_resend() -> None:
    """Nothing is guaranteed: a signal that never changes is never sent."""
    assert monthly_floor({"A": FieldPolicy(interval_seconds=1)}) == 0


def test_the_floor_never_exceeds_the_ceiling() -> None:
    policies = {
        "A": FieldPolicy(interval_seconds=60, resend_interval_seconds=3600),
        "B": FieldPolicy(interval_seconds=10, resend_interval_seconds=10),
    }
    assert monthly_floor(policies) <= monthly_ceiling(policies)


def test_bounds_of_an_empty_config_are_zero() -> None:
    assert monthly_ceiling({}) == 0
    assert monthly_floor({}) == 0


def test_signals_to_cost_uses_the_rate_per_million() -> None:
    assert signals_to_cost(1_000_000, 6.0) == pytest.approx(6.0)
    assert signals_to_cost(150_000, 1_000_000 / 150_000) == pytest.approx(1.0)


def test_the_projection_scales_the_observed_rate_to_a_month() -> None:
    # 100 signals in an hour -> 720 hours in a month.
    assert projected_monthly_signals(100, 3600) == pytest.approx(72_000)


def test_the_projection_needs_a_positive_window() -> None:
    """A fresh process has no rate to project, and must not divide by zero."""
    assert projected_monthly_signals(0, 0) is None
    assert projected_monthly_signals(10, -5) is None
