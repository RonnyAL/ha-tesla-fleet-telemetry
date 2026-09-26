"""Named interval presets, applied per documented category.

A preset sets the interval of signals that are *already enabled*; it never
enables one. If it could, choosing "eco" would switch on all 56 Charging
signals, which is the opposite of what the name promises.

`default` is empty on purpose: an entry that has never chosen a preset must
resolve to exactly the configuration it had before presets existed.

Shortening an interval is nearly free for a signal that rarely changes —
`interval_seconds` is a rate ceiling and Tesla pushes on change — which is what
makes a category-wide setting safe. The exception is a continuously-changing
field: `ChargerVoltage` "changes frequently, even when not charging", so `live`
on Charging is the one combination that costs real money. Firmware >= 2025.2.6
applies a 0.3 delta of its own; below that the user needs to set one, which is
why the options form shows the cost ceiling before saving.

No Home Assistant imports.
"""
from __future__ import annotations

from typing import Any

from .signal_metadata import SIGNALS

PRESET_DEFAULT = "default"
PRESET_ECO = "eco"
PRESET_BALANCED = "balanced"
PRESET_LIVE = "live"
# Kept with its original meaning so automations already calling
# `set_interval_preset` keep working.
PRESET_HIGH_RATE = "high_rate"

PRESETS: tuple[str, ...] = (
    PRESET_DEFAULT,
    PRESET_ECO,
    PRESET_BALANCED,
    PRESET_LIVE,
    PRESET_HIGH_RATE,
)

# The table key for a signal Tesla does not file under any category.
UNCATEGORISED = "(uncategorised)"

# Seconds. Vehicle Configuration and User Preference hold static values —
# firmware version, unit preferences — so their interval is long in every
# preset; writing that out rather than omitting it makes the intent legible.
PRESET_CATEGORY_INTERVALS: dict[str, dict[str, int]] = {
    PRESET_DEFAULT: {},
    PRESET_ECO: {
        "Location": 300,
        "Driving": 300,
        "Charging": 600,
        "Powertrain": 600,
        "Safety": 300,
        "Climate": 900,
        "Vehicle State": 900,
        "Media": 1800,
        "Service": 3600,
        "Vehicle Configuration": 3600,
        "User Preference": 3600,
        UNCATEGORISED: 900,
    },
    PRESET_BALANCED: {
        "Location": 30,
        "Driving": 30,
        "Charging": 60,
        "Powertrain": 60,
        "Safety": 60,
        "Climate": 120,
        "Vehicle State": 120,
        "Media": 300,
        "Service": 900,
        "Vehicle Configuration": 3600,
        "User Preference": 3600,
        UNCATEGORISED: 120,
    },
    PRESET_LIVE: {
        "Location": 1,
        "Driving": 1,
        "Charging": 10,
        "Powertrain": 5,
        "Safety": 10,
        "Climate": 30,
        "Vehicle State": 30,
        "Media": 60,
        "Service": 300,
        "Vehicle Configuration": 3600,
        "User Preference": 3600,
        UNCATEGORISED: 60,
    },
    PRESET_HIGH_RATE: {},
}

# The legacy preset is per-signal rather than per-category: it rewrites exactly
# Location and VehicleSpeed to 1s and leaves every other signal alone.
PRESET_SIGNAL_INTERVALS: dict[str, dict[str, int]] = {
    PRESET_HIGH_RATE: {"Location": 1, "VehicleSpeed": 1},
}


def normalise_preset(name: Any) -> str:
    """A known preset name, or ``default``.

    An entry can carry a preset written by a newer version of the integration,
    or by a user editing storage. Falling back beats failing setup.
    """
    if isinstance(name, str) and name in PRESETS:
        return name
    return PRESET_DEFAULT


def preset_interval(preset: str, signal: str) -> int | None:
    """The interval this preset imposes on ``signal``, or None for no opinion.

    Per-signal entries win over per-category ones, which is what keeps the
    legacy `high_rate` preset narrow.
    """
    preset = normalise_preset(preset)
    by_signal = PRESET_SIGNAL_INTERVALS.get(preset, {})
    if signal in by_signal:
        return by_signal[signal]
    meta = SIGNALS.get(signal)
    if meta is None:
        return None
    category = meta.category or UNCATEGORISED
    return PRESET_CATEGORY_INTERVALS.get(preset, {}).get(category)
