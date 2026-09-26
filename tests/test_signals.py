"""Tests for signal-config resolution (``signals.py``).

The critical guarantee is that an entry with no overrides resolves to *exactly*
today's ``DEFAULT_INTERVALS_SECONDS`` — i.e. adding UI configurability changed
nothing about what gets pushed by default. The rest cover the override, disable,
preset-layering, and options-form parse paths.

``signals.py`` imports only ``const.py`` (pure) at module load — its proto and
Home Assistant dependencies are lazy — so both are loaded directly by path under
a synthetic package, with no Home Assistant installed.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

# --- Load const.py + signals.py in isolation under a synthetic package -------
# signals.py uses ``from .const import ...``, so it needs a package context;
# we build one whose __path__ points at the integration dir without running its
# HA-importing __init__.py.
_PKG = "tesla_telemetry_isolated"
_DIR = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "tesla_telemetry"
)


def _load_isolated() -> tuple[ModuleType, ModuleType]:
    if f"{_PKG}.signals" in sys.modules:
        return sys.modules[f"{_PKG}.const"], sys.modules[f"{_PKG}.signals"]
    pkg = ModuleType(_PKG)
    pkg.__path__ = [str(_DIR)]
    sys.modules[_PKG] = pkg
    for name in ("const", "signals"):
        spec = importlib.util.spec_from_file_location(
            f"{_PKG}.{name}", _DIR / f"{name}.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"{_PKG}.{name}"] = module
        spec.loader.exec_module(module)
    return sys.modules[f"{_PKG}.const"], sys.modules[f"{_PKG}.signals"]


const, signals = _load_isolated()


def _entry(options: dict | None = None, data: dict | None = None) -> SimpleNamespace:
    """A minimal stand-in for a HA ConfigEntry (only .options/.data are read)."""
    return SimpleNamespace(options=options or {}, data=data or {})


# ---------------------------------------------------------------------------
# resolve_effective_intervals
# ---------------------------------------------------------------------------
def test_no_overrides_equals_current_default_config() -> None:
    """The headline guarantee: empty options ⇒ byte-for-byte today's config."""
    result = signals.resolve_effective_intervals(_entry())
    assert result == const.DEFAULT_INTERVALS_SECONDS
    assert result is not const.DEFAULT_INTERVALS_SECONDS  # must be a copy


def test_zero_disables_a_default_signal() -> None:
    entry = _entry({const.CONF_SIGNAL_OVERRIDES: {"Location": 0}})
    result = signals.resolve_effective_intervals(entry)
    assert "Location" not in result
    # Untouched signals are unaffected.
    assert result["VehicleSpeed"] == const.DEFAULT_INTERVALS_SECONDS["VehicleSpeed"]


def test_positive_override_changes_interval() -> None:
    entry = _entry({const.CONF_SIGNAL_OVERRIDES: {"VehicleSpeed": 2}})
    assert signals.resolve_effective_intervals(entry)["VehicleSpeed"] == 2


def test_added_catalog_signal_appears_enabled() -> None:
    entry = _entry({const.CONF_SIGNAL_OVERRIDES: {"Hvil": 60}})
    result = signals.resolve_effective_intervals(entry)
    assert result["Hvil"] == 60
    assert "Hvil" not in const.DEFAULT_INTERVALS_SECONDS  # truly an add


def test_high_rate_preset_layers_on_top() -> None:
    entry = _entry(data={const.CONF_INTERVAL_PRESET: const.INTERVAL_PRESET_HIGH_RATE})
    result = signals.resolve_effective_intervals(entry)
    assert result["Location"] == 1
    assert result["VehicleSpeed"] == 1


def test_bad_override_values_are_ignored() -> None:
    entry = _entry({const.CONF_SIGNAL_OVERRIDES: {"Location": "nope", "Soc": None}})
    result = signals.resolve_effective_intervals(entry)
    # Unparseable overrides fall through to the defaults.
    assert result["Location"] == const.DEFAULT_INTERVALS_SECONDS["Location"]
    assert result["Soc"] == const.DEFAULT_INTERVALS_SECONDS["Soc"]


# ---------------------------------------------------------------------------
# A bare string is iterable; naively iterating it explodes it into single
# characters. `resolve_field_policies`'s own include-hygiene layer happens to
# filter that garbage back out (single characters are never enabled signal
# names), which is why a mutation of the ``isinstance(raw_include, (list,
# tuple))`` guard in ``_normalise_override`` can still pass a test written
# against `resolve_field_policies`. This asserts the guard directly, one
# layer below hygiene, via `signal_overrides`.
# ---------------------------------------------------------------------------
def test_signal_overrides_rejects_a_bare_string_include_fields() -> None:
    entry = _entry(
        {const.CONF_SIGNAL_OVERRIDES: {"Odometer": {"include_fields": "VehicleSpeed"}}}
    )
    overrides = signals.signal_overrides(entry)
    assert overrides["Odometer"].get("include_fields", ()) == ()


# ---------------------------------------------------------------------------
# Category catalog invariant
# ---------------------------------------------------------------------------
def test_every_default_signal_is_in_exactly_one_category() -> None:
    counts: dict[str, int] = {}
    for sigs in const.SIGNAL_CATEGORIES.values():
        for sig in sigs:
            counts[sig] = counts.get(sig, 0) + 1
    # No signal appears in two categories.
    assert [s for s, c in counts.items() if c > 1] == []
    # Every default signal is placed in a category (so it's editable in the UI).
    assert set(const.DEFAULT_INTERVALS_SECONDS) - set(counts) == set()
    # And categories don't list phantom signals absent from the defaults.
    assert set(counts) - set(const.DEFAULT_INTERVALS_SECONDS) == set()



# ---------------------------------------------------------------------------
# Default-interval sanity for continuously-varying signals
# ---------------------------------------------------------------------------
# Push-on-change makes a tight ceiling free for a near-static signal, but these
# move continuously while the car drives or charges, so the ceiling binds on
# every interval and is what actually decides their volume. Pinned so a later
# edit has to be deliberate about tightening them.
CONTINUOUS_SIGNAL_FLOORS = {
    "Odometer": 300,  # also its pre-v0.4.0 upstream value (c377e5d^)
    "EnergyRemaining": 300,
    "ExpectedEnergyPercentAtTripArrival": 300,
    "LifetimeEnergyUsed": 3600,
    "LifetimeEnergyGainedRegen": 3600,
    "LifetimeEnergyChargedKwh": 3600,
}


def test_continuously_varying_signals_keep_a_long_ceiling() -> None:
    for signal, floor in CONTINUOUS_SIGNAL_FLOORS.items():
        actual = const.DEFAULT_INTERVALS_SECONDS.get(signal)
        assert actual is not None, f"{signal} is no longer a default signal"
        assert actual >= floor, (
            f"{signal} defaults to {actual}s, below the {floor}s floor: it "
            "changes continuously, so a tighter ceiling multiplies billed "
            "signals for every user"
        )
