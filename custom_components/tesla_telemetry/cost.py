"""What the configured stream costs: two bounds and one measurement.

Tesla bills per data point. `interval_seconds` is a rate ceiling and the
vehicle pushes on change, so the naive "sum of 1/interval" is not an estimate
of anything — for a parked car it can overstate reality by an order of
magnitude. It is a genuine upper bound, and it is labelled as one.

`resend_interval_seconds` is the opposite: a commitment to send even when
nothing has changed. That makes a *lower* bound possible, and the lower bound
is usually the more decision-relevant number, because it is the part of the
bill that cannot be avoided by driving less.

No Home Assistant imports.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from .signals import FieldPolicy

# Tesla bills monthly; 30 days is the convention used throughout.
SECONDS_PER_MONTH = 2_592_000


def _payload_size(policy: FieldPolicy) -> int:
    """Data points per publication: the field itself, plus any it carries.

    The multiplier is an inference rather than a documented fact — Tesla bills
    per data point and an included field arrives as an extra one in the same
    payload. It errs upward inside a number already labelled a ceiling.
    """
    return 1 + len(policy.include_fields)


def monthly_ceiling(policies: dict[str, FieldPolicy]) -> float:
    """At most this many data points a month.

    Assumes every signal changes at every opportunity, which is the worst
    case and almost never the real one.
    """
    return sum(
        _payload_size(policy) * SECONDS_PER_MONTH / policy.interval_seconds
        for policy in policies.values()
        if policy.interval_seconds > 0
    )


def monthly_floor(policies: dict[str, FieldPolicy]) -> float:
    """At least this many data points a month.

    Only signals with a resend interval contribute: everything else is sent
    solely on change, and a signal that never changes is never sent at all.
    """
    return sum(
        _payload_size(policy) * SECONDS_PER_MONTH / policy.resend_interval_seconds
        for policy in policies.values()
        if policy.resend_interval_seconds
    )


def signals_to_cost(signals: float, rate_per_million: float) -> float:
    """Convert a data-point count into money at the configured rate."""
    return signals * rate_per_million / 1_000_000


def projected_monthly_signals(
    signals_since_start: int, uptime_seconds: float
) -> float | None:
    """The observed rate scaled to a month, or None if there is no window yet.

    Returns None rather than zero for a fresh process: "no measurement" and
    "measured nothing" are different claims and the sensor must not make the
    second one.
    """
    if uptime_seconds <= 0:
        return None
    return signals_since_start * SECONDS_PER_MONTH / uptime_seconds
