# UI and defaults Implementation Plan (Phase 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user shape the whole telemetry stream from the UI — presets by category, per-signal tuning of all four Tesla field keys, and a cost figure that states honestly whether it is a bound or a measurement.

**Architecture:** Four Home-Assistant-free modules (`firmware.py`, `presets.py`, `cost.py`, and a widened `signals.py`) do all the thinking and are unit-tested without the HA harness; a new `options_flow.py` owns a five-step menu-driven form. Resolution produces a `FieldPolicy` per signal through six ordered layers, the last of which strips any key the vehicle has not demonstrated it supports.

**Tech Stack:** Python 3.13 (CI) / 3.11 (dev box), Home Assistant custom integration, voluptuous, `pytest-homeassistant-custom-component`, ruff.

**Spec:** `docs/superpowers/specs/2026-09-26-ui-and-defaults-design.md`

## Global Constraints

- Domain stays `tesla_telemetry`. No existing entity `unique_id` changes.
- Never push to `upstream`. Branch is `phase4-ui-and-defaults`; PRs go into the fork's `main`.
- No secrets or personal data in code, tests or fixtures. VINs in tests are obvious placeholders (`5YJ3E1EA1PF000000`).
- `pytest` stays green and `ruff check .` stays clean after every task.
- `firmware.py`, `presets.py`, `cost.py` and `signals.py` import no Home Assistant at module scope. HA and voluptuous imports inside those modules are lazy, inside the function that needs them. This is an existing property of `signals.py` and the reason its tests are fast.
- `scripts/gen_signal_metadata.py --check` must pass. `signal_metadata.py` is generated — never hand-edit it.
- Firmware floors, verbatim: `minimum_delta` and `resend_interval_seconds` require `2024.44.32`; `include_fields` requires `2026.26.6`; `Version` means the installed firmware only from `2024.44`.
- A month is `2_592_000` seconds everywhere.
- `TelemetryFieldConfig.to_dict()` omits every key that is `None` or empty, so a default configuration serialises byte-identically to today's.

## Review Focus

Five inputs the spec implies but does not name. Each has a test, in the task that owns the code.

1. **A stored override names a signal no longer in the catalog** (proto bump, Tesla removal). Resolution must skip it, not `KeyError`. → Task 4.
2. **A stored preset name that no longer exists** (downgrade, rename). Must fall back to `default`, not crash the entry at setup. → Task 2.
3. **An interval stored as a string** (`"60"` from a YAML round trip or a form coercion). Must coerce exactly as today's `_coerce_interval` does, and a non-numeric value must be ignored rather than raise. → Task 4.
4. **`minimum_delta: 0`.** Zero is meaningless as a delta and must be treated as unset, or we push a key that does nothing and changes the fingerprint. → Task 4.
5. **`include_fields` naming a disabled or unknown signal.** Options can be edited in any order, so the dangling reference must be dropped at resolution *and* rejected at save time. Two different defences, two tests. → Task 4 (resolution) and Task 9 (validation).

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `custom_components/tesla_telemetry/firmware.py` | new — version parse/compare, floors, `FirmwareEvidence` | 1 |
| `custom_components/tesla_telemetry/presets.py` | new — per-category and legacy per-signal preset tables | 2 |
| `signal_catalog/overrides.py` | three delta fields on `Override`, five entries filled in | 3 |
| `scripts/signal_metadata/reconcile.py` | carry the delta fields; new drift rule | 3 |
| `scripts/signal_metadata/render.py` | render the delta fields into `SignalMeta` | 3 |
| `custom_components/tesla_telemetry/signals.py` | `FieldPolicy`, six-layer resolution, tolerant override reader | 4 |
| `custom_components/tesla_telemetry/tesla_api.py` | three optional keys on `TelemetryFieldConfig` | 5 |
| `custom_components/tesla_telemetry/services.py` | build the config from policies; fingerprint the whole policy | 5 |
| `custom_components/tesla_telemetry/cost.py` | new — monthly ceiling, floor, measured projection | 6 |
| `custom_components/tesla_telemetry/coordinator.py` | `started_at`; staleness from `resend_interval_seconds` | 6, 10 |
| `custom_components/tesla_telemetry/__init__.py` | evidence subscriber and persistence | 7 |
| `custom_components/tesla_telemetry/sensor.py` | `ProjectedMonthlyCostSensor` | 7 |
| `custom_components/tesla_telemetry/options_flow.py` | new — the five-step flow | 8, 9 |
| `custom_components/tesla_telemetry/config_flow.py` | options flow moves out | 8 |
| `custom_components/tesla_telemetry/strings.json`, `translations/en.json` | step and menu text | 8, 9 |
| `README.md` | the new options UI, presets, and the staleness limitation | 10 |

**There is no new `Firmware` sensor.** The spec asks for one; the generic entity path from Phase 3 already provides it the moment `Version` joins the default signal set, under unique_id `<vin>_version_telemetry`. Adding a curated sensor for the same signal would produce two entities for one datum and require claiming `Version`. Do not build one.

---

## Task 1: `firmware.py` — version comparison and evidence

**Files:**
- Create: `custom_components/tesla_telemetry/firmware.py`
- Create: `tests/test_firmware.py`

**Interfaces:**
- Consumes: `signal_metadata.SIGNALS` (for `min_firmware`).
- Produces:
  - `parse_version(text: str | None) -> tuple[int, ...] | None`
  - `at_least(version: str | None, floor: str) -> bool`
  - `FirmwareEvidence(reported_version: str | None = None, proven_version: str | None = None, assume_support: bool = False)` with `.supports(floor: str) -> bool`
  - `proof_from_signals(names: Iterable[str]) -> str | None`
  - `evidence_from_entry(entry: Any) -> FirmwareEvidence`
  - `FLOOR_VERSION_IS_FIRMWARE`, `FLOOR_MINIMUM_DELTA`, `FLOOR_RESEND_INTERVAL`, `FLOOR_INCLUDE_FIELDS`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_firmware.py`:

```python
"""Tests for firmware version comparison and support evidence.

The pending-update trap is the reason this module exists. Before firmware
2024.44 the `Version` signal reported the *available update*, not what was
installed, so a 2024.38 car with a 2024.44.32 update queued reports a number
above the floor. Evidence proven by receipt cannot lie that way: a car cannot
send a field its firmware does not have.
"""
from __future__ import annotations

import pytest

from custom_components.tesla_telemetry.firmware import (
    FLOOR_INCLUDE_FIELDS,
    FLOOR_MINIMUM_DELTA,
    FirmwareEvidence,
    at_least,
    evidence_from_entry,
    parse_version,
    proof_from_signals,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2024.44.32", (2024, 44, 32)),
        ("2025.44.25.5", (2025, 44, 25, 5)),
        ("2026.26.6", (2026, 26, 6)),
        ("2024.44.32 4c7a3b1e", (2024, 44, 32)),
        ("2024.44.32-rc1", (2024, 44, 32)),
        ("", None),
        (None, None),
        ("not a version", None),
        ("....", None),
    ],
)
def test_parse_version(text, expected) -> None:
    assert parse_version(text) == expected


def test_at_least_compares_numerically_not_lexically() -> None:
    """"2024.9" < "2024.44" is true numerically and false as strings."""
    assert at_least("2024.44", "2024.9")
    assert not at_least("2024.9", "2024.44")


def test_at_least_treats_a_shorter_version_as_zero_padded() -> None:
    assert at_least("2024.44.32", "2024.44")
    assert not at_least("2024.44", "2024.44.32")


def test_at_least_is_false_for_junk() -> None:
    assert not at_least(None, FLOOR_MINIMUM_DELTA)
    assert not at_least("unknown", FLOOR_MINIMUM_DELTA)


def test_proof_takes_the_highest_floor_among_received_signals() -> None:
    # HvacPower is floored at 2024.44.25, ChargerVoltage at 2024.44.32.
    assert proof_from_signals(["VehicleSpeed", "HvacPower"]) == "2024.44.25"
    assert proof_from_signals(["HvacPower", "ChargerVoltage"]) == "2024.44.32"


def test_proof_ignores_unknown_and_unfloored_signals() -> None:
    assert proof_from_signals(["VehicleSpeed"]) is None
    assert proof_from_signals(["NotASignal"]) is None
    assert proof_from_signals([]) is None


def test_proof_alone_establishes_support() -> None:
    evidence = FirmwareEvidence(proven_version="2024.44.32")
    assert evidence.supports(FLOOR_MINIMUM_DELTA)
    assert not evidence.supports(FLOOR_INCLUDE_FIELDS)


def test_the_pending_update_trap_does_not_establish_support() -> None:
    """A 2024.38 car with a 2024.44.32 update queued reports the update."""
    evidence = FirmwareEvidence(
        reported_version="2024.44.32", proven_version="2024.26"
    )
    assert not evidence.supports(FLOOR_MINIMUM_DELTA)


def test_a_reported_version_is_trusted_once_proof_reaches_2024_44() -> None:
    """Corroboration flips exactly the same claim from untrusted to trusted."""
    evidence = FirmwareEvidence(
        reported_version="2025.8.1", proven_version="2024.44.25"
    )
    assert evidence.supports(FLOOR_MINIMUM_DELTA)


def test_a_reported_version_alone_never_establishes_support() -> None:
    assert not FirmwareEvidence(reported_version="2026.30").supports(
        FLOOR_MINIMUM_DELTA
    )


def test_assume_support_bypasses_all_evidence() -> None:
    evidence = FirmwareEvidence(assume_support=True)
    assert evidence.supports(FLOOR_MINIMUM_DELTA)
    assert evidence.supports(FLOOR_INCLUDE_FIELDS)


def test_evidence_from_entry_reads_data_and_options() -> None:
    class Entry:
        data = {
            "firmware_evidence": {
                "reported": "2025.8.1",
                "proven": "2024.44.25",
            }
        }
        options = {"assume_firmware_support": True}

    evidence = evidence_from_entry(Entry())
    assert evidence.reported_version == "2025.8.1"
    assert evidence.proven_version == "2024.44.25"
    assert evidence.assume_support is True


def test_evidence_from_entry_tolerates_a_bare_entry() -> None:
    class Entry:
        data: dict = {}
        options: dict = {}

    evidence = evidence_from_entry(Entry())
    assert evidence == FirmwareEvidence()
    assert not evidence.supports(FLOOR_MINIMUM_DELTA)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_firmware.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'custom_components.tesla_telemetry.firmware'`

- [ ] **Step 3: Write the implementation**

Create `custom_components/tesla_telemetry/firmware.py`:

```python
"""Which telemetry field keys this vehicle's firmware can honour.

Tesla documents a firmware floor for each of the per-field keys added after
`interval_seconds`, but does not document what a car does with a key it
predates. So nothing new is sent until the vehicle has demonstrated it can
support it, and this module decides what counts as a demonstration.

Two sources of evidence, with different trust:

* The `Version` signal is fast but can *overstate*. Before firmware 2024.44 it
  reported the available software update rather than the installed version, so
  an older car with an update queued reports a number above the floor.
* The highest documented floor among signals actually received can only
  *understate*. A vehicle cannot transmit a field its firmware does not have.

A reported version is therefore trusted only once receipt has proven the car is
at 2024.44 or later, which is precisely the version where `Version` starts
meaning the installed firmware.

No Home Assistant imports: this is pure comparison and is unit-tested without
the HA harness.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .const import CONF_ASSUME_FIRMWARE_SUPPORT, CONF_FIRMWARE_EVIDENCE
from .signal_metadata import SIGNALS

# The version at which `Version` began reporting installed firmware rather
# than the available update. Below it, a reported version means nothing.
FLOOR_VERSION_IS_FIRMWARE = "2024.44"

# Announced 2025-01-09: both keys arrived together.
FLOOR_MINIMUM_DELTA = "2024.44.32"
FLOOR_RESEND_INTERVAL = "2024.44.32"

# Announced 2026-08-17, Fleet Telemetry client 1.3.0.
FLOOR_INCLUDE_FIELDS = "2026.26.6"

# Leading dotted-numeric run. Real values carry suffixes ("2024.44.32 4c7a3b1e",
# "…-rc1") that must not defeat the comparison.
_VERSION_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)")


def parse_version(text: str | None) -> tuple[int, ...] | None:
    """The leading dotted-numeric run of a Tesla version string, or None."""
    if not text:
        return None
    match = _VERSION_RE.match(str(text))
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def at_least(version: str | None, floor: str) -> bool:
    """True when ``version`` is a parseable version >= ``floor``.

    Shorter versions compare as zero-padded, so "2024.44" < "2024.44.32".
    """
    parsed = parse_version(version)
    if parsed is None:
        return False
    target = parse_version(floor)
    if target is None:  # pragma: no cover — every floor here is a literal
        return False
    width = max(len(parsed), len(target))
    padded = parsed + (0,) * (width - len(parsed))
    goal = target + (0,) * (width - len(target))
    return padded >= goal


def proof_from_signals(names: Iterable[str]) -> str | None:
    """The highest documented firmware floor among signals actually received.

    Receipt is proof: the vehicle sent a field that only exists from this
    firmware onwards, so it is at least that version.
    """
    best: str | None = None
    for name in names:
        meta = SIGNALS.get(name)
        floor = getattr(meta, "min_firmware", None)
        if floor is None:
            continue
        if best is None or at_least(floor, best):
            best = floor
    return best


@dataclass(frozen=True, slots=True)
class FirmwareEvidence:
    """What this vehicle has shown about its firmware."""

    reported_version: str | None = None
    proven_version: str | None = None
    assume_support: bool = False

    def supports(self, floor: str) -> bool:
        """Whether a field key with this firmware floor may be sent."""
        if self.assume_support:
            return True
        if at_least(self.proven_version, floor):
            return True
        # A reported version is only meaningful once receipt has shown the car
        # is at the version where `Version` stopped meaning "available update".
        return at_least(self.reported_version, floor) and at_least(
            self.proven_version, FLOOR_VERSION_IS_FIRMWARE
        )


def evidence_from_entry(entry: Any) -> FirmwareEvidence:
    """Read stored evidence off a config entry.

    Duck-typed rather than typed against ``ConfigEntry`` so this module stays
    importable without Home Assistant.
    """
    data = getattr(entry, "data", None) or {}
    options = getattr(entry, "options", None) or {}
    stored = data.get(CONF_FIRMWARE_EVIDENCE) or {}
    return FirmwareEvidence(
        reported_version=stored.get("reported"),
        proven_version=stored.get("proven"),
        assume_support=bool(options.get(CONF_ASSUME_FIRMWARE_SUPPORT, False)),
    )
```

- [ ] **Step 4: Add the two new constants**

In `custom_components/tesla_telemetry/const.py`, directly after the
`CONF_CA_PEM` block:

```python
# Firmware support evidence, persisted on entry.data as
# ``{"reported": str | None, "proven": str | None}``. Stored on `data` rather
# than `options` deliberately: an entry update runs the options-change
# listener, which re-pushes when the fields fingerprint changed — so the moment
# proof unlocks a field key, the config carrying it reaches the car on its own.
CONF_FIRMWARE_EVIDENCE = "firmware_evidence"

# Per-entry escape hatch: send the newer per-field keys without waiting for the
# vehicle to demonstrate support. Off by default; the only way to bypass
# evidence.
CONF_ASSUME_FIRMWARE_SUPPORT = "assume_firmware_support"
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_firmware.py -q`
Expected: PASS (16 tests)

- [ ] **Step 6: Mutation-test the trap**

The pending-update test is the one that matters. Temporarily weaken
`supports` by deleting the `and at_least(self.proven_version, ...)` clause:

Run: `.venv/bin/pytest tests/test_firmware.py -q`
Expected: FAIL — `test_the_pending_update_trap_does_not_establish_support`

Restore the clause and confirm the suite passes again. A test that passes
either way tests nothing.

- [ ] **Step 7: Lint and commit**

```bash
.venv/bin/ruff check .
git add custom_components/tesla_telemetry/firmware.py custom_components/tesla_telemetry/const.py tests/test_firmware.py
git commit -m "Add firmware evidence and version comparison"
```

---

## Task 2: `presets.py` — per-category interval tables

**Files:**
- Create: `custom_components/tesla_telemetry/presets.py`
- Create: `tests/test_presets.py`

**Interfaces:**
- Consumes: `signal_metadata.SIGNALS` (for `category`).
- Produces:
  - `PRESET_DEFAULT`, `PRESET_ECO`, `PRESET_BALANCED`, `PRESET_LIVE`, `PRESET_HIGH_RATE`
  - `PRESETS: tuple[str, ...]`
  - `PRESET_CATEGORY_INTERVALS: dict[str, dict[str, int]]`
  - `PRESET_SIGNAL_INTERVALS: dict[str, dict[str, int]]`
  - `UNCATEGORISED: str`
  - `preset_interval(preset: str, signal: str) -> int | None`
  - `normalise_preset(name: Any) -> str`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_presets.py`:

```python
"""Tests for the per-category interval presets.

A preset retunes signals that are already enabled. It must never enable one:
"eco" that switched on all 56 Charging signals would be the opposite of its
name.
"""
from __future__ import annotations

from custom_components.tesla_telemetry.presets import (
    PRESET_BALANCED,
    PRESET_DEFAULT,
    PRESET_ECO,
    PRESET_HIGH_RATE,
    PRESET_LIVE,
    PRESETS,
    UNCATEGORISED,
    PRESET_CATEGORY_INTERVALS,
    normalise_preset,
    preset_interval,
)
from custom_components.tesla_telemetry.signal_metadata import SIGNALS


def test_default_retunes_nothing() -> None:
    """An entry that never chose a preset must resolve to today's config."""
    for signal in ("VehicleSpeed", "ChargerVoltage", "Odometer"):
        assert preset_interval(PRESET_DEFAULT, signal) is None


def test_every_category_in_the_catalog_has_an_entry_in_every_preset() -> None:
    """A category with no entry would silently fall through to the default."""
    categories = {m.category or UNCATEGORISED for m in SIGNALS.values()}
    for preset in (PRESET_ECO, PRESET_BALANCED, PRESET_LIVE):
        missing = categories - set(PRESET_CATEGORY_INTERVALS[preset])
        assert not missing, f"{preset} has no interval for {sorted(missing)}"


def test_presets_order_from_sparse_to_dense() -> None:
    """eco >= balanced >= live for every category, or the names are lies."""
    for category in PRESET_CATEGORY_INTERVALS[PRESET_ECO]:
        eco = PRESET_CATEGORY_INTERVALS[PRESET_ECO][category]
        balanced = PRESET_CATEGORY_INTERVALS[PRESET_BALANCED][category]
        live = PRESET_CATEGORY_INTERVALS[PRESET_LIVE][category]
        assert eco >= balanced >= live, category


def test_a_signal_resolves_through_its_documented_category() -> None:
    assert SIGNALS["VehicleSpeed"].category == "Driving"
    assert preset_interval(PRESET_LIVE, "VehicleSpeed") == 1
    assert preset_interval(PRESET_ECO, "VehicleSpeed") == 300


def test_an_uncategorised_signal_uses_the_uncategorised_entry() -> None:
    uncategorised = next(
        name for name, meta in SIGNALS.items() if meta.category is None
    )
    assert preset_interval(PRESET_BALANCED, uncategorised) == (
        PRESET_CATEGORY_INTERVALS[PRESET_BALANCED][UNCATEGORISED]
    )


def test_an_unknown_signal_has_no_preset_interval() -> None:
    """A stored override can name a signal a proto bump removed."""
    assert preset_interval(PRESET_LIVE, "NotASignal") is None


def test_high_rate_is_per_signal_and_touches_nothing_else() -> None:
    """The legacy preset keeps working for existing automations."""
    assert preset_interval(PRESET_HIGH_RATE, "Location") == 1
    assert preset_interval(PRESET_HIGH_RATE, "VehicleSpeed") == 1
    assert preset_interval(PRESET_HIGH_RATE, "ChargerVoltage") is None


def test_normalise_preset_falls_back_to_default() -> None:
    """A preset name from a newer version must not break an entry at setup."""
    assert normalise_preset("eco") == PRESET_ECO
    assert normalise_preset("no_such_preset") == PRESET_DEFAULT
    assert normalise_preset(None) == PRESET_DEFAULT
    assert normalise_preset(17) == PRESET_DEFAULT


def test_presets_tuple_matches_the_defined_names() -> None:
    assert set(PRESETS) == {
        PRESET_DEFAULT,
        PRESET_ECO,
        PRESET_BALANCED,
        PRESET_LIVE,
        PRESET_HIGH_RATE,
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_presets.py -q`
Expected: collection error, `No module named 'custom_components.tesla_telemetry.presets'`

- [ ] **Step 3: Write the implementation**

Create `custom_components/tesla_telemetry/presets.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_presets.py -q`
Expected: PASS (9 tests)

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff check .
git add custom_components/tesla_telemetry/presets.py tests/test_presets.py
git commit -m "Add per-category interval presets"
```

---

## Task 3: Minimum-delta metadata and the drift rule

Tesla states delta advice in prose, in five descriptions, in four different
senses. Parsing English is brittle, so the *values* are hand-entered with their
citation — the same treatment Phase 2 gave units — and the *detection* of a new
case is automated. A catalog field that mentions a minimum delta and has no
override entry fails `--check`.

**Files:**
- Modify: `signal_catalog/overrides.py` (the `Override` dataclass and five entries)
- Modify: `scripts/signal_metadata/reconcile.py` (`SignalRecord`, the new rule)
- Modify: `scripts/signal_metadata/render.py` (`SignalMeta` header and the emitter)
- Modify: `tests/signal_metadata/test_render.py` (three new fields in `RECORDS`)
- Test: `tests/signal_metadata/test_reconcile.py`
- Regenerate: `custom_components/tesla_telemetry/signal_metadata.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `SignalMeta.minimum_delta_required: float | None`,
  `SignalMeta.minimum_delta_default: float | None`,
  `SignalMeta.minimum_delta_recommended: bool`.

- [ ] **Step 1: Write the failing test for the drift rule**

Append to `tests/signal_metadata/test_reconcile.py`:

```python
def test_a_field_with_delta_advice_needs_an_override_entry() -> None:
    """Tesla adding delta advice to a new field must fail the gate, loudly.

    The values are hand-entered; only the detection is automatic. A silent
    miss would mean a field that needs a delta never gets one.
    """
    proto = ProtoField(
        ids={"ChargerVoltage": 5}, firmware={}, semi_only=frozenset()
    )
    nodes = [
        {
            "field_name": "ChargerVoltage",
            "category": "Charging",
            "type": "real",
            "proto_enum_name": "",
            "description": (
                "It is recommended to set minimum_delta, which is available "
                "on firmware version 2024.44.32 and later."
            ),
        }
    ]
    with pytest.raises(ReconcileError, match="minimum delta"):
        reconcile(proto, nodes, {})


def test_delta_advice_is_satisfied_by_any_of_the_three_attributes() -> None:
    """Required, Tesla's own default, and 'recommended' all count."""
    proto = ProtoField(
        ids={"ChargerVoltage": 5}, firmware={}, semi_only=frozenset()
    )
    nodes = [
        {
            "field_name": "ChargerVoltage",
            "category": "Charging",
            "type": "real",
            "proto_enum_name": "",
            "description": "It is recommended to set minimum_delta.",
        }
    ]
    for override in (
        Override(minimum_delta_required=1.0),
        Override(minimum_delta_default=0.3),
        Override(minimum_delta_recommended=True),
    ):
        records, _ = reconcile(proto, nodes, {"ChargerVoltage": override})
        assert len(records) == 1


def test_the_rule_matches_both_spellings() -> None:
    """Tesla writes both "minimum_delta" and "minimum delta"."""
    proto = ProtoField(ids={"InsideTemp": 7}, firmware={}, semi_only=frozenset())
    nodes = [
        {
            "field_name": "InsideTemp",
            "category": "Climate",
            "type": "real",
            "proto_enum_name": "",
            "description": "…setting a minimum delta is recommended.",
        }
    ]
    with pytest.raises(ReconcileError, match="minimum delta"):
        reconcile(proto, nodes, {})


def test_delta_attributes_reach_the_record() -> None:
    proto = ProtoField(
        ids={"SelfDrivingMilesSinceReset": 9}, firmware={}, semi_only=frozenset()
    )
    nodes = [
        {
            "field_name": "SelfDrivingMilesSinceReset",
            "category": "Safety",
            "type": "real",
            "proto_enum_name": "",
            "description": "This field requires minimum_delta to be set to >= 1.",
        }
    ]
    override = Override("mi", "distance", "total_increasing", minimum_delta_required=1.0)
    records, _ = reconcile(proto, nodes, {"SelfDrivingMilesSinceReset": override})
    assert records[0].minimum_delta_required == 1.0
    assert records[0].minimum_delta_default is None
    assert records[0].minimum_delta_recommended is False
```

Add `from signal_catalog.overrides import Override` to the test module's
imports if it is not already there.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/signal_metadata/test_reconcile.py -q`
Expected: FAIL — `TypeError: Override.__init__() got an unexpected keyword argument 'minimum_delta_required'`

- [ ] **Step 3: Extend the `Override` dataclass**

In `signal_catalog/overrides.py`, add three fields to `Override`, after
`enum_labels`:

```python
    # Minimum-delta advice, transcribed by hand from Tesla's field
    # descriptions. Four senses, three fields:
    #
    #   minimum_delta_required     the field does not report at all without it
    #   minimum_delta_default      the value the car already applies itself
    #   minimum_delta_recommended  Tesla advises one but sets no value
    #
    # The generator fails if a description mentions a minimum delta and none of
    # these is set, so a new case cannot pass unnoticed.
    minimum_delta_required: float | None = None
    minimum_delta_default: float | None = None
    minimum_delta_recommended: bool = False
```

- [ ] **Step 4: Transcribe the five entries**

Replace the existing lines and add the missing `Location` entry. Keep the
alphabetical placement the file already uses.

```python
    # "It is recommended to set minimum_delta... Beginning with firmware
    # version 2025.2.6, minimum_delta is set to 0.3 by default."
    'ChargerVoltage': Override(
        'V', 'voltage', 'measurement',  # teslemetry
        minimum_delta_default=0.3, minimum_delta_recommended=True,
    ),
    # "This field frequently changes in small increments and setting a
    # minimum delta is recommended." Tesla sets no value of its own.
    'InsideTemp': Override(
        '°C', 'temperature', 'measurement',  # teslemetry
        minimum_delta_recommended=True,
    ),
    # "Beginning with firmware version 2025.2.6, specifying minimum delta for
    # location values is possible. Changes in distance are measured in metres."
    # No unit/device_class: Tesla documents the type as Location, and the
    # reconciler rejects a unit on a non-numeric type.
    'Location': Override(minimum_delta_recommended=True),
    # "Beginning with firmware version 2025.2.6, the minimum delta for
    # Odometer is set to 0.1 by default."
    'Odometer': Override(
        'mi', 'distance', 'total_increasing',  # teslemetry
        minimum_delta_default=0.1,
    ),
    # "This field requires minimum_delta to be explicitly set to a value >= 1"
    # — without one it never reports at all.
    'SelfDrivingMilesSinceReset': Override(
        'mi', 'distance', 'total_increasing',  # teslemetry
        minimum_delta_required=1.0,
    ),
```

- [ ] **Step 5: Carry the fields through the reconciler**

In `scripts/signal_metadata/reconcile.py`, add `import re` at the top, add
three fields to the end of `SignalRecord`:

```python
    minimum_delta_required: float | None
    minimum_delta_default: float | None
    minimum_delta_recommended: bool
```

add the detection pattern beside `_NON_NUMERIC_TYPES`:

```python
# Tesla states delta advice in prose, in both spellings. The values are
# hand-entered in the overrides table; this only detects a field that has
# advice and no entry, so a new one cannot slip through unnoticed.
_DELTA_ADVICE_RE = re.compile(r"minimum[ _]delta", re.IGNORECASE)
```

inside the per-field loop, after the existing non-numeric check:

```python
        description = _clean(node.get("description")) or ""
        if _DELTA_ADVICE_RE.search(description):
            has_advice = override is not None and (
                override.minimum_delta_required is not None
                or override.minimum_delta_default is not None
                or override.minimum_delta_recommended
            )
            if not has_advice:
                raise ReconcileError(
                    f"{name}: Tesla's description mentions a minimum delta but "
                    f"the overrides table has no entry for it. Transcribe the "
                    f"value by hand into signal_catalog/overrides.py — set "
                    f"minimum_delta_required, minimum_delta_default, or "
                    f"minimum_delta_recommended."
                )
```

and pass them into `SignalRecord(...)`:

```python
                minimum_delta_required=(
                    override.minimum_delta_required if override else None
                ),
                minimum_delta_default=(
                    override.minimum_delta_default if override else None
                ),
                minimum_delta_recommended=(
                    override.minimum_delta_recommended if override else False
                ),
```

- [ ] **Step 6: Render the new fields**

In `scripts/signal_metadata/render.py`, add to the `SignalMeta` dataclass
inside `_HEADER`, after `documented: bool`:

```python
    minimum_delta_required: float | None
    minimum_delta_default: float | None
    minimum_delta_recommended: bool
```

extend the header docstring's second paragraph with one sentence:

```
`minimum_delta_*` transcribes Tesla's per-field delta advice: required means
the field does not report without one, default is the value the car already
applies itself, and recommended means Tesla advises one but names no value.
```

and add three lines to the emitter, after `documented=`:

```python
            f"        minimum_delta_required={record.minimum_delta_required!r},\n"
            f"        minimum_delta_default={record.minimum_delta_default!r},\n"
            f"        minimum_delta_recommended={record.minimum_delta_recommended!r},\n"
```

- [ ] **Step 7: Update the render test's fixtures**

In `tests/signal_metadata/test_render.py`, add the three keyword arguments to
both `SignalRecord(...)` constructions in `RECORDS`:

```python
        minimum_delta_required=None, minimum_delta_default=None,
        minimum_delta_recommended=False,
```

Both constructions already use keyword arguments, so nothing shifts. Do not
convert them to positional.

- [ ] **Step 8: Regenerate the metadata module**

```bash
.venv/bin/python scripts/gen_signal_metadata.py --offline
```

Expected: writes `signal_metadata.py`, still 251 signals. Confirm the five
entries carry their new values:

```bash
.venv/bin/python -c "
from custom_components.tesla_telemetry.signal_metadata import SIGNALS
for n in ('ChargerVoltage','InsideTemp','Location','Odometer','SelfDrivingMilesSinceReset'):
    m = SIGNALS[n]
    print(n, m.minimum_delta_required, m.minimum_delta_default, m.minimum_delta_recommended)
"
```

Expected exactly:

```
ChargerVoltage None 0.3 True
InsideTemp None None True
Location None None True
Odometer None 0.1 False
SelfDrivingMilesSinceReset 1.0 None False
```

- [ ] **Step 9: Run the full suite and the drift gate**

Run: `.venv/bin/pytest -q && .venv/bin/python scripts/gen_signal_metadata.py --check`
Expected: all pass; `--check` reports `signal_metadata.py is current (251 signals)`

- [ ] **Step 10: Mutation-test the gate**

Temporarily delete the `minimum_delta_default=0.3, minimum_delta_recommended=True,`
line from the `ChargerVoltage` override.

Run: `.venv/bin/python scripts/gen_signal_metadata.py --offline`
Expected: exits non-zero with `ChargerVoltage: Tesla's description mentions a minimum delta…`

Restore the line, re-run `--offline`, and confirm `git diff --stat` shows no
change to `signal_metadata.py`.

- [ ] **Step 11: Lint and commit**

```bash
.venv/bin/ruff check .
git add signal_catalog/overrides.py scripts/signal_metadata/ tests/signal_metadata/ custom_components/tesla_telemetry/signal_metadata.py
git commit -m "Carry Tesla's minimum-delta advice into the metadata table"
```

---

## Task 4: `FieldPolicy` and six-layer resolution

**Files:**
- Modify: `custom_components/tesla_telemetry/signals.py`
- Modify: `custom_components/tesla_telemetry/const.py`
- Test: `tests/test_field_policies.py` (new)
- Test: `tests/test_signals.py` (existing — must stay green untouched)

**Interfaces:**
- Consumes: `firmware.FirmwareEvidence`, `firmware.FLOOR_*`, `presets.preset_interval`, `presets.normalise_preset`, `SignalMeta.minimum_delta_required`.
- Produces:
  - `FieldPolicy(interval_seconds: int, minimum_delta: float | None = None, resend_interval_seconds: int | None = None, include_fields: tuple[str, ...] = ())`
  - `resolve_field_policies(entry: Any, evidence: FirmwareEvidence | None = None) -> dict[str, FieldPolicy]`
  - `signal_overrides(entry: Any) -> dict[str, dict[str, Any]]` — **shape changed**, now normalised dicts
  - `active_preset(entry: Any) -> str`
  - `resolve_effective_intervals(entry: Any) -> dict[str, int]` — unchanged signature

- [ ] **Step 1: Write the failing tests**

Create `tests/test_field_policies.py`:

```python
"""Tests for six-layer field policy resolution.

The layering is the whole design, so each layer gets a test that can only
pass if it sits in the right place relative to its neighbours.
"""
from __future__ import annotations

from types import SimpleNamespace

from custom_components.tesla_telemetry.const import (
    CONF_INTERVAL_PRESET,
    CONF_SIGNAL_OVERRIDES,
    DEFAULT_INTERVALS_SECONDS,
    DEFAULT_NEW_SIGNAL_INTERVAL,
)
from custom_components.tesla_telemetry.firmware import FirmwareEvidence
from custom_components.tesla_telemetry.signals import (
    FieldPolicy,
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
    # The interval is never gated.
    assert policy.interval_seconds > 0


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


def test_a_required_delta_survives_the_gate_on_any_car_that_can_send_it() -> None:
    """SelfDrivingMilesSinceReset needs 2025.44.25.5, far above the delta floor.

    Layer 5 can in principle strip what layer 4 required; it cannot happen for
    this field, and the implication is pinned rather than trusted.
    """
    from custom_components.tesla_telemetry.signal_metadata import SIGNALS
    from custom_components.tesla_telemetry.firmware import (
        FLOOR_MINIMUM_DELTA,
        at_least,
    )

    floor = SIGNALS["SelfDrivingMilesSinceReset"].min_firmware
    assert at_least(floor, FLOOR_MINIMUM_DELTA), (
        "a car able to send this field must also support minimum_delta"
    )
    evidence = FirmwareEvidence(proven_version=floor)
    policies = resolve_field_policies(
        entry(options={CONF_SIGNAL_OVERRIDES: {"SelfDrivingMilesSinceReset": {}}}),
        evidence,
    )
    assert policies["SelfDrivingMilesSinceReset"].minimum_delta == 1.0


def test_assume_support_defeats_the_gate() -> None:
    options = {
        "assume_firmware_support": True,
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_field_policies.py -q`
Expected: collection error, `ImportError: cannot import name 'FieldPolicy'`

- [ ] **Step 3: Replace the resolver section of `signals.py`**

Replace everything in `custom_components/tesla_telemetry/signals.py` from
`def _coerce_interval` down to (but not including) the
`# ---- Options-flow schema helpers` banner with:

```python
def _coerce_interval(value: Any) -> int | None:
    """Parse a form/stored value into a non-negative int, else ``None``."""
    try:
        interval = int(value)
    except (TypeError, ValueError):
        return None
    return interval if interval >= 0 else None


def _coerce_delta(value: Any) -> float | None:
    """Parse a minimum delta, else ``None``.

    Zero is meaningless as a delta — a field always changes by at least
    nothing — so it is read as "unset" rather than pushed as a key that does
    nothing but change the config fingerprint.
    """
    try:
        delta = float(value)
    except (TypeError, ValueError):
        return None
    return delta if delta > 0 else None


@dataclass(frozen=True, slots=True)
class FieldPolicy:
    """How one signal is streamed: everything Tesla accepts per field."""

    interval_seconds: int
    minimum_delta: float | None = None
    resend_interval_seconds: int | None = None
    include_fields: tuple[str, ...] = ()


def _normalise_override(value: Any) -> dict[str, Any] | None:
    """One stored override, in either shape, as a normalised dict.

    ``interval_seconds`` has three distinct states and the distinction is load
    bearing:

    =============  ==========================================================
    ``0``          disabled — removes a default-on signal from the config
    positive int   pinned — this exact interval, overriding any preset
    key absent     enabled, interval inherited from the preset or the default
    =============  ==========================================================

    Without the third state the signal picker and presets would fight: the
    picker would have to store a number, and a stored number is pinned, so
    every signal a user added would permanently ignore every preset.
    """
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        if "interval_seconds" in value:
            interval = _coerce_interval(value["interval_seconds"])
            # An unparseable interval drops to the inherit state rather than
            # discarding the whole override and its other keys.
            if interval is not None:
                result["interval_seconds"] = interval
        delta = _coerce_delta(value.get("minimum_delta"))
        if delta is not None:
            result["minimum_delta"] = delta
        resend = _coerce_interval(value.get("resend_interval_seconds"))
        if resend:
            result["resend_interval_seconds"] = resend
        include = tuple(
            name for name in (value.get("include_fields") or [])
            if isinstance(name, str)
        )
        if include:
            result["include_fields"] = include
        return result
    # The legacy shape: a bare interval.
    interval = _coerce_interval(value)
    if interval is None:
        return None
    return {"interval_seconds": interval}


def signal_overrides(entry: Any) -> dict[str, dict[str, Any]]:
    """The sanitised per-signal overrides stored on ``entry``.

    Home Assistant does not version ``entry.options``, so both the pre-Phase-4
    shape (``{signal: int}``) and the current one (``{signal: {...}}``) are
    accepted permanently. The dict form is written back on the next save.
    """
    options = getattr(entry, "options", None) or {}
    raw = options.get(CONF_SIGNAL_OVERRIDES) or {}
    result: dict[str, dict[str, Any]] = {}
    for name, value in raw.items():
        normalised = _normalise_override(value)
        if normalised is not None:
            result[name] = normalised
    return result


def active_preset(entry: Any) -> str:
    """The preset in force, from options, falling back to data.

    ``set_interval_preset`` used to write ``entry.data``. Options is now the
    single source of truth — two stores for one setting produces the bug where
    the service appears to do nothing — and the old location is still read so
    an entry written by the old service keeps its preset.
    """
    from .presets import normalise_preset

    options = getattr(entry, "options", None) or {}
    if CONF_INTERVAL_PRESET in options:
        return normalise_preset(options[CONF_INTERVAL_PRESET])
    data = getattr(entry, "data", None) or {}
    return normalise_preset(data.get(CONF_INTERVAL_PRESET))


def resolve_field_policies(
    entry: Any, evidence: Any | None = None
) -> dict[str, FieldPolicy]:
    """The ``{signal: FieldPolicy}`` map to push to Tesla for ``entry``.

    Six layers, lowest precedence first: built-in defaults, the preset's
    per-category interval, the user's per-signal overrides, Tesla's required
    deltas, include-field hygiene, and finally the firmware gate — which
    removes any key the vehicle has not demonstrated it can honour.

    ``evidence`` defaults to nothing proven, so a caller that forgets it gates
    rather than opens.
    """
    from .firmware import (
        FLOOR_INCLUDE_FIELDS,
        FLOOR_MINIMUM_DELTA,
        FLOOR_RESEND_INTERVAL,
        FirmwareEvidence,
    )
    from .presets import preset_interval
    from .signal_metadata import SIGNALS

    if evidence is None:
        evidence = FirmwareEvidence()

    overrides = signal_overrides(entry)
    preset = active_preset(entry)

    policies: dict[str, FieldPolicy] = {}
    for name in sorted(set(DEFAULT_INTERVALS_SECONDS) | set(overrides)):
        meta = SIGNALS.get(name)
        if meta is None:
            # A stored override can name a field a proto bump removed. Pushing
            # a name the car does not know is not something Tesla documents a
            # response to, so it is dropped.
            _LOGGER.debug("ignoring override for unknown signal %s", name)
            continue
        override = overrides.get(name, {})
        if override.get("interval_seconds") == 0:
            continue  # disabled

        interval = DEFAULT_INTERVALS_SECONDS.get(name)          # layer 1
        from_preset = preset_interval(preset, name)             # layer 2
        if from_preset is not None:
            interval = from_preset
        pinned = override.get("interval_seconds")               # layer 3
        if pinned:
            interval = pinned
        if interval is None:
            interval = DEFAULT_NEW_SIGNAL_INTERVAL
        interval = max(SIGNAL_INTERVAL_MIN, min(int(interval), SIGNAL_INTERVAL_MAX))

        delta = override.get("minimum_delta")                   # layer 4
        required = meta.minimum_delta_required
        if required is not None and (delta is None or delta < required):
            delta = required

        policies[name] = FieldPolicy(
            interval_seconds=interval,
            minimum_delta=delta,
            resend_interval_seconds=override.get("resend_interval_seconds"),
            include_fields=tuple(override.get("include_fields", ())),
        )

    enabled = set(policies)
    supports_delta = evidence.supports(FLOOR_MINIMUM_DELTA)
    supports_resend = evidence.supports(FLOOR_RESEND_INTERVAL)
    supports_include = evidence.supports(FLOOR_INCLUDE_FIELDS)

    for name, policy in list(policies.items()):
        # Options can be edited in any order, so an include can name a signal
        # that is disabled or gone by the time it resolves.
        include = tuple(
            target
            for target in policy.include_fields
            if target in enabled and target != name
        )
        policies[name] = FieldPolicy(
            interval_seconds=policy.interval_seconds,
            minimum_delta=policy.minimum_delta if supports_delta else None,
            resend_interval_seconds=(
                policy.resend_interval_seconds if supports_resend else None
            ),
            include_fields=include if supports_include else (),
        )
    return policies


def resolve_effective_intervals(entry: Any) -> dict[str, int]:
    """The ``{signal: interval}`` view of the resolved config.

    Kept for the coordinator's staleness map and the services module. The
    interval is never gated on firmware, so this needs no evidence.
    """
    return {
        name: policy.interval_seconds
        for name, policy in resolve_field_policies(entry).items()
    }
```

- [ ] **Step 4: Fix the module's imports and docstring**

At the top of `signals.py`, add to the existing imports:

```python
import logging
from collections.abc import Mapping
from dataclasses import dataclass
```

add `DEFAULT_NEW_SIGNAL_INTERVAL` and `SIGNAL_INTERVAL_MIN` to the existing
`from .const import (...)` block if absent, and add after the imports:

```python
_LOGGER = logging.getLogger(__name__)
```

Replace the module docstring's numbered list with:

```
  1. ``DEFAULT_INTERVALS_SECONDS`` — the built-in default set (const.py).
  2. The active preset's per-category interval (presets.py).
  3. ``entry.options[CONF_SIGNAL_OVERRIDES]`` — per-signal user overrides.
  4. Tesla's required minimum deltas, as a floor under the user's value.
  5. Include-field hygiene: a target must be an enabled, known signal.
  6. The firmware gate — any key the vehicle has not shown it supports is
     removed (firmware.py).
```

- [ ] **Step 5: Run the new tests and the existing ones**

Run: `.venv/bin/pytest tests/test_field_policies.py tests/test_signals.py -q`
Expected: PASS. `tests/test_signals.py` covers the options schema helpers and
must pass **unmodified** — if it does not, the override shape change has
leaked somewhere it should not have.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 7: Mutation-test the layering**

Swap layers 2 and 3 by moving the `pinned` block above the `from_preset`
block.

Run: `.venv/bin/pytest tests/test_field_policies.py -q`
Expected: FAIL — `test_a_pinned_interval_beats_the_preset`

Restore the order and confirm the suite passes.

- [ ] **Step 8: Lint and commit**

```bash
.venv/bin/ruff check .
git add custom_components/tesla_telemetry/signals.py custom_components/tesla_telemetry/const.py tests/test_field_policies.py
git commit -m "Resolve a full field policy per signal through six layers"
```

---

## Task 5: Serialise the policy, and fingerprint all of it

**Files:**
- Modify: `custom_components/tesla_telemetry/tesla_api.py:56-63`
- Modify: `custom_components/tesla_telemetry/services.py` (`_resolve_intervals`, `_build_telemetry_config`, `_fields_fingerprint`, `_stamp_last_sync`)
- Modify: `custom_components/tesla_telemetry/__init__.py:200` (the listener's comparison)
- Test: `tests/test_sync_fingerprint.py` (existing, extended)
- Test: `tests/test_telemetry_config_body.py` (new)

**Interfaces:**
- Consumes: `signals.resolve_field_policies`, `signals.FieldPolicy`, `firmware.evidence_from_entry`.
- Produces:
  - `TelemetryFieldConfig(interval_seconds, minimum_delta=None, resend_interval_seconds=None, include_fields=[])`
  - `services._resolve_policies(entry) -> dict[str, FieldPolicy]`
  - `services._config_fields(entry) -> dict[str, dict[str, Any]]` — the serialised body, and the thing that gets fingerprinted

- [ ] **Step 1: Write the failing tests**

Create `tests/test_telemetry_config_body.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_telemetry_config_body.py -q`
Expected: FAIL — `ImportError: cannot import name '_config_fields'`

- [ ] **Step 3: Widen `TelemetryFieldConfig`**

Replace `custom_components/tesla_telemetry/tesla_api.py:56-63` with:

```python
class TelemetryFieldConfig:
    """One field's streaming policy.

    `interval_seconds` is a rate ceiling: Tesla pushes on change, never faster
    than this. The other three arrived later and each has a firmware floor —
    `minimum_delta` and `resend_interval_seconds` from 2024.44.32,
    `include_fields` from 2026.26.6 — so they are omitted unless set, and the
    firmware gate in `signals.resolve_field_policies` decides whether they are
    ever set at all.
    """

    interval_seconds: int
    minimum_delta: float | None = None
    resend_interval_seconds: int | None = None
    include_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Only the keys that are actually configured.

        Omitting unset keys is what keeps a default configuration byte-
        identical to the pre-Phase-4 body, so an untouched entry on old
        firmware is never exposed to a key Tesla documents no behaviour for.
        """
        body: dict[str, Any] = {"interval_seconds": self.interval_seconds}
        if self.minimum_delta is not None:
            body["minimum_delta"] = self.minimum_delta
        if self.resend_interval_seconds is not None:
            body["resend_interval_seconds"] = self.resend_interval_seconds
        if self.include_fields:
            body["include_fields"] = list(self.include_fields)
        return body
```

- [ ] **Step 4: Build the body from policies**

In `custom_components/tesla_telemetry/services.py`, replace `_resolve_intervals`
and `_build_telemetry_config` with:

```python
def _resolve_policies(entry: ConfigEntry) -> dict[str, FieldPolicy]:
    """The resolved per-field policy for this entry, firmware gate included."""
    return resolve_field_policies(entry, evidence_from_entry(entry))


def _config_fields(entry: ConfigEntry) -> dict[str, dict[str, Any]]:
    """The serialised ``fields`` object, and the thing that is fingerprinted.

    Fingerprinting the serialised body rather than the intervals is what makes
    an edit to a minimum delta detectable: the comparison has to cover
    everything that is actually sent, or the options listener decides nothing
    changed and never re-pushes.
    """
    return {
        name: TelemetryFieldConfig(
            interval_seconds=policy.interval_seconds,
            minimum_delta=policy.minimum_delta,
            resend_interval_seconds=policy.resend_interval_seconds,
            include_fields=list(policy.include_fields),
        ).to_dict()
        for name, policy in _resolve_policies(entry).items()
    }


def _build_telemetry_config(entry: ConfigEntry, ca_pem: str) -> TelemetryConfig:
    return TelemetryConfig(
        hostname=entry.data[CONF_HOSTNAME],
        port=int(entry.data[CONF_PORT]),
        ca=ca_pem,
        fields={
            name: TelemetryFieldConfig(
                interval_seconds=policy.interval_seconds,
                minimum_delta=policy.minimum_delta,
                resend_interval_seconds=policy.resend_interval_seconds,
                include_fields=list(policy.include_fields),
            )
            for name, policy in _resolve_policies(entry).items()
        },
    )
```

Update the imports at the top of `services.py`:

```python
from .firmware import evidence_from_entry
from .signals import FieldPolicy, resolve_field_policies
```

Keep the existing `resolve_effective_intervals` import only if something else
still uses it; if nothing does, remove it and let ruff confirm.

- [ ] **Step 5: Widen the fingerprint**

Replace the body of `_fields_fingerprint`:

```python
def _fields_fingerprint(
    fields: Mapping[str, int | Mapping[str, Any]],
) -> str:
    """Stable digest of a resolved field config.

    Stored rather than the whole mapping: all anyone needs is an equality test
    against what the car was last told, and a digest keeps the config entry
    small.

    Accepts either a plain ``{signal: interval}`` mapping or the serialised
    per-field bodies. A bare int is normalised to ``{"interval_seconds": n}``,
    which digests identically to the old shape — so an entry stamped before
    Phase 4 is not seen as changed the first time it is compared.

    Serialised as JSON rather than joined with separators, so that no field
    name can be confused with the delimiters and produce a collision. Tesla's
    Field enum never contains one today, but a digest that silently treats two
    different configs as equal would present as "the car was never updated".
    """
    normalised = {
        name: ({"interval_seconds": value} if isinstance(value, int) else dict(value))
        for name, value in fields.items()
    }
    payload = json.dumps(
        sorted(normalised.items()), separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(payload.encode()).hexdigest()
```

Note: `sort_keys=True` makes the inner dicts deterministic. For the bare-int
case the output is unchanged, which is what the existing
`tests/test_sync_fingerprint.py::test_fingerprint_is_stable_and_order_independent`
pins — that test must keep passing untouched.

- [ ] **Step 6: Update the three call sites**

`_stamp_last_sync`'s parameter is now the serialised body. Change its
signature and docstring:

```python
def _stamp_last_sync(
    hass: HomeAssistant,
    entry: ConfigEntry,
    fields: Mapping[str, int | Mapping[str, Any]] | None = None,
) -> None:
```

and inside, `_fields_fingerprint(fields)` with the same `if fields is not None`
guard.

In `services.py`, both `_stamp_last_sync(hass, entry, _resolve_intervals(entry))`
calls become `_stamp_last_sync(hass, entry, _config_fields(entry))`.

In `custom_components/tesla_telemetry/__init__.py`, `_async_options_updated`
currently computes `new_intervals = resolve_effective_intervals(entry)` and
compares `_fields_fingerprint(new_intervals)`. Change it to:

```python
    from .services import _config_fields

    new_fields = _config_fields(entry)
    coordinator.effective_intervals = {
        name: body["interval_seconds"] for name, body in new_fields.items()
    }
```

with the comparison against `_fields_fingerprint(new_fields)` and the final
`_stamp_last_sync(hass, entry, new_fields)`.

- [ ] **Step 7: Run the tests**

Run: `.venv/bin/pytest tests/test_telemetry_config_body.py tests/test_sync_fingerprint.py -q`
Expected: PASS, with `test_sync_fingerprint.py` unmodified.

- [ ] **Step 8: Run the full suite and mutation-test the fingerprint**

Run: `.venv/bin/pytest -q`
Expected: PASS

Temporarily revert `_fields_fingerprint` to digest only
`{name: body["interval_seconds"]}`.

Run: `.venv/bin/pytest tests/test_telemetry_config_body.py -q`
Expected: FAIL — the three "changing only …" tests.

Restore it.

- [ ] **Step 9: Lint and commit**

```bash
.venv/bin/ruff check .
git add custom_components/tesla_telemetry/tesla_api.py custom_components/tesla_telemetry/services.py custom_components/tesla_telemetry/__init__.py tests/test_telemetry_config_body.py
git commit -m "Send and fingerprint the full per-field policy"
```

---

## Task 6: `cost.py` — bounds and a measured projection

**Files:**
- Create: `custom_components/tesla_telemetry/cost.py`
- Modify: `custom_components/tesla_telemetry/coordinator.py` (add `started_at`)
- Create: `tests/test_cost.py`

**Interfaces:**
- Consumes: `signals.FieldPolicy`.
- Produces:
  - `SECONDS_PER_MONTH: int`
  - `monthly_ceiling(policies: dict[str, FieldPolicy]) -> float`
  - `monthly_floor(policies: dict[str, FieldPolicy]) -> float`
  - `signals_to_cost(signals: float, rate_per_million: float) -> float`
  - `projected_monthly_signals(signals_since_start: int, uptime_seconds: float) -> float | None`
  - `TeslaTelemetryCoordinator.started_at: float`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cost.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_cost.py -q`
Expected: collection error, `No module named 'custom_components.tesla_telemetry.cost'`

- [ ] **Step 3: Write the implementation**

Create `custom_components/tesla_telemetry/cost.py`:

```python
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
```

- [ ] **Step 4: Give the coordinator a start timestamp**

In `custom_components/tesla_telemetry/coordinator.py.__init__`, directly after
`self.restored_signal_base = 0`:

```python
        # When this process began counting. The measured cost projection needs
        # a window, and `signals_since_start` alone does not carry one.
        self.started_at = time.time()
```

`time` is already imported.

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest tests/test_cost.py -q`
Expected: PASS (11 tests)

- [ ] **Step 6: Lint and commit**

```bash
.venv/bin/ruff check .
git add custom_components/tesla_telemetry/cost.py custom_components/tesla_telemetry/coordinator.py tests/test_cost.py
git commit -m "Add cost bounds and a measured monthly projection"
```

---

## Task 7: Wire up evidence, and the projected-cost sensor

**Files:**
- Modify: `custom_components/tesla_telemetry/const.py` (add `Version` to the defaults)
- Modify: `custom_components/tesla_telemetry/__init__.py` (the evidence subscriber)
- Modify: `custom_components/tesla_telemetry/sensor.py` (`ProjectedMonthlyCostSensor`)
- Create: `tests/test_firmware_evidence_wiring.py`

**Interfaces:**
- Consumes: `firmware.proof_from_signals`, `firmware.at_least`, `coordinator.all_signals_topic`, `coordinator.started_at`, `cost.projected_monthly_signals`, `cost.signals_to_cost`.
- Produces: `__init__._async_record_firmware_evidence(hass, entry, coordinator)`.

**Note:** there is deliberately **no** curated `Firmware` sensor. Adding
`Version` to the default set gives it a generic sensor via the Phase 3 factory,
at unique_id `<vin>_version_telemetry`. A curated one would be a second entity
for the same datum.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_firmware_evidence_wiring.py`:

```python
"""Tests for accumulating firmware evidence from the live stream.

The coordinator's per-signal counter is since-restart only, so proof has to be
persisted as it is earned rather than recomputed at setup.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from custom_components.tesla_telemetry.const import (
    CONF_FIRMWARE_EVIDENCE,
    DEFAULT_INTERVALS_SECONDS,
)
from custom_components.tesla_telemetry.firmware import (
    FLOOR_MINIMUM_DELTA,
    FirmwareEvidence,
    proof_from_signals,
)


def test_version_is_streamed_by_default() -> None:
    """Evidence needs the signal that carries the firmware version."""
    assert "Version" in DEFAULT_INTERVALS_SECONDS


def test_version_has_a_long_interval() -> None:
    """Firmware changes monthly; a tight ceiling would buy nothing."""
    assert DEFAULT_INTERVALS_SECONDS["Version"] >= 3600


def test_four_default_signals_prove_the_minimum_delta_floor() -> None:
    """The gate is only useful if proof is actually reachable.

    Receipt of any one of these establishes 2024.44.32 outright, with no
    version string involved.
    """
    provers = [
        name
        for name in DEFAULT_INTERVALS_SECONDS
        if proof_from_signals([name]) is not None
        and FirmwareEvidence(proven_version=proof_from_signals([name])).supports(
            FLOOR_MINIMUM_DELTA
        )
    ]
    assert "ChargerVoltage" in provers
    assert "LocatedAtHome" in provers
    assert len(provers) >= 4


@pytest.mark.parametrize(
    ("stored", "arriving", "expected"),
    [
        (None, "ChargerVoltage", "2024.44.32"),
        ("2024.26", "ChargerVoltage", "2024.44.32"),
        # Monotonic: a lower floor never lowers what was already proven.
        ("2026.32", "ChargerVoltage", "2026.32"),
        # A signal with no documented floor proves nothing.
        ("2024.26", "VehicleSpeed", "2024.26"),
    ],
)
def test_proof_only_ever_rises(stored, arriving, expected) -> None:
    from custom_components.tesla_telemetry.firmware import at_least

    incoming = proof_from_signals([arriving])
    best = stored
    if incoming is not None and (best is None or at_least(incoming, best)):
        best = incoming
    assert best == expected


async def test_a_sample_raises_the_stored_proof(hass) -> None:
    """End to end: publishing ChargerVoltage writes evidence to the entry."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.tesla_telemetry import (
        _async_record_firmware_evidence,
    )
    from custom_components.tesla_telemetry.const import DOMAIN
    from custom_components.tesla_telemetry.coordinator import (
        TeslaTelemetryCoordinator,
    )
    from custom_components.tesla_telemetry.proto import vehicle_data_pb2

    entry = MockConfigEntry(domain=DOMAIN, data={"vin": "5YJ3E1EA1PF000000"})
    entry.add_to_hass(hass)
    coordinator = TeslaTelemetryCoordinator(hass, "5YJ3E1EA1PF000000", "Test Car")

    unsub = _async_record_firmware_evidence(hass, entry, coordinator)

    value = vehicle_data_pb2.Value()
    value.double_value = 231.4
    coordinator.async_publish("ChargerVoltage", value)
    await hass.async_block_till_done()

    assert entry.data[CONF_FIRMWARE_EVIDENCE]["proven"] == "2024.44.32"
    unsub()


async def test_the_version_signal_is_recorded_as_the_reported_version(hass) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.tesla_telemetry import (
        _async_record_firmware_evidence,
    )
    from custom_components.tesla_telemetry.const import DOMAIN
    from custom_components.tesla_telemetry.coordinator import (
        TeslaTelemetryCoordinator,
    )
    from custom_components.tesla_telemetry.proto import vehicle_data_pb2

    entry = MockConfigEntry(domain=DOMAIN, data={"vin": "5YJ3E1EA1PF000000"})
    entry.add_to_hass(hass)
    coordinator = TeslaTelemetryCoordinator(hass, "5YJ3E1EA1PF000000", "Test Car")
    unsub = _async_record_firmware_evidence(hass, entry, coordinator)

    value = vehicle_data_pb2.Value()
    value.string_value = "2025.32.4 a1b2c3d"
    coordinator.async_publish("Version", value)
    await hass.async_block_till_done()

    assert entry.data[CONF_FIRMWARE_EVIDENCE]["reported"] == "2025.32.4 a1b2c3d"
    unsub()


async def test_an_unchanged_evidence_write_does_not_touch_the_entry(hass) -> None:
    """Evidence updates must be rare: every entry write runs the listener."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.tesla_telemetry import (
        _async_record_firmware_evidence,
    )
    from custom_components.tesla_telemetry.const import DOMAIN
    from custom_components.tesla_telemetry.coordinator import (
        TeslaTelemetryCoordinator,
    )
    from custom_components.tesla_telemetry.proto import vehicle_data_pb2

    entry = MockConfigEntry(domain=DOMAIN, data={"vin": "5YJ3E1EA1PF000000"})
    entry.add_to_hass(hass)
    coordinator = TeslaTelemetryCoordinator(hass, "5YJ3E1EA1PF000000", "Test Car")
    unsub = _async_record_firmware_evidence(hass, entry, coordinator)

    value = vehicle_data_pb2.Value()
    value.double_value = 231.4
    coordinator.async_publish("ChargerVoltage", value)
    await hass.async_block_till_done()
    first = dict(entry.data)

    coordinator.async_publish("ChargerVoltage", value)
    await hass.async_block_till_done()

    assert entry.data == first
    unsub()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_firmware_evidence_wiring.py -q`
Expected: FAIL — `test_version_is_streamed_by_default`, and an ImportError for
`_async_record_firmware_evidence`.

- [ ] **Step 3: Stream `Version` by default**

In `custom_components/tesla_telemetry/const.py`, add to
`DEFAULT_INTERVALS_SECONDS`, in the "Local additions" block:

```python
    # The installed firmware version. Push-on-change, and firmware moves
    # monthly, so a 6-hour ceiling costs roughly one signal a month. It earns
    # its place by telling the firmware gate which per-field keys this car can
    # honour — and it gets a sensor of its own through the generic path.
    #
    # Its category is Vehicle Configuration, so any preset retunes it to 3600.
    # That is not a regression: at roughly one change a month the ceiling never
    # binds, so both values cost the same nothing.
    "Version": 21600,
```

- [ ] **Step 4: Write the evidence subscriber**

In `custom_components/tesla_telemetry/__init__.py`, add:

```python
@callback
def _async_record_firmware_evidence(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: TeslaTelemetryCoordinator,
) -> Callable[[], None]:
    """Accumulate firmware evidence from the live stream.

    Two things are recorded. The `Version` signal is the vehicle's own claim,
    which is fast but can overstate — before firmware 2024.44 it reported the
    available update rather than the installed version. Receipt of any signal
    with a documented firmware floor is proof, which can only understate: a
    car cannot send a field its firmware does not have.

    Written to `entry.data`, which is deliberate. An entry update runs the
    options-change listener, and that re-pushes when the fields fingerprint
    changed — so the moment proof unlocks a key, the config carrying it reaches
    the car without the user touching anything. Writes are therefore kept rare:
    nothing happens unless the evidence actually changed.
    """

    @callback
    def _handle(name: str, sample: SignalSample) -> None:
        stored = dict(entry.data.get(CONF_FIRMWARE_EVIDENCE) or {})
        updated = dict(stored)

        if name == "Version":
            reported = value_as_string(sample.value)
            if reported:
                updated["reported"] = reported

        incoming = proof_from_signals([name])
        if incoming is not None:
            current = stored.get("proven")
            if current is None or at_least(incoming, current):
                updated["proven"] = incoming

        if updated == stored:
            return
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_FIRMWARE_EVIDENCE: updated}
        )
        _LOGGER.debug(
            "tesla_telemetry: firmware evidence for vin=%s is now %s",
            entry.data.get(CONF_VIN),
            updated,
        )

    return async_dispatcher_connect(hass, all_signals_topic(coordinator.vin), _handle)
```

Add the imports it needs:

```python
from collections.abc import Callable

from homeassistant.core import callback

from .const import CONF_FIRMWARE_EVIDENCE
from .coordinator import SignalSample
from .firmware import at_least, proof_from_signals
from .values import value_as_string
```

and register it in `async_setup_entry`, next to the existing generic-factory
subscription:

```python
    entry.async_on_unload(
        _async_record_firmware_evidence(hass, entry, coordinator)
    )
```

- [ ] **Step 5: Add the projected-cost sensor**

Append to `custom_components/tesla_telemetry/sensor.py`:

```python
class ProjectedMonthlyCostSensor(_SignalStatSensor):
    """What this vehicle's stream costs a month at the rate observed so far.

    A measurement, not a bound: it projects the rate actually seen since this
    process started. The options form shows the ceiling and floor instead,
    because those need no history and are available before anything arrives.

    Reports `unknown` rather than zero until there is a window to measure, so
    it never claims to have measured nothing.
    """

    _attr_name = "Projected monthly signal cost"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 2
    _attr_icon = "mdi:cash-clock"

    def __init__(
        self, coordinator: TeslaTelemetryCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = (
            f"{coordinator.vin}_projected_monthly_cost_telemetry"
        )

    @property
    def native_unit_of_measurement(self) -> str:
        return (self.hass.config.currency if self.hass else None) or "USD"

    @property
    def native_value(self) -> float | None:
        projected = projected_monthly_signals(
            self._coordinator.signals_since_start,
            time.time() - self._coordinator.started_at,
        )
        if projected is None:
            return None
        rate = self._entry.options.get(
            CONF_COST_PER_MILLION_SIGNALS, DEFAULT_COST_PER_MILLION_SIGNALS
        )
        return round(signals_to_cost(projected, rate), 2)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        window = time.time() - self._coordinator.started_at
        return {
            "measurement_window_hours": round(window / 3600, 2),
            "signals_in_window": self._coordinator.signals_since_start,
        }
```

Add `import time` and
`from .cost import projected_monthly_signals, signals_to_cost` to the imports,
and add the sensor to the list at `sensor.py:96`:

```python
            EstimatedSignalCostSensor(coordinator, entry),
            ProjectedMonthlyCostSensor(coordinator, entry),
```

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/pytest tests/test_firmware_evidence_wiring.py -q`
Expected: PASS (10 tests)

- [ ] **Step 7: Regenerate metadata and run everything**

Adding a default signal does not change generated metadata, but the drift gate
is cheap insurance:

Run: `.venv/bin/pytest -q && .venv/bin/python scripts/gen_signal_metadata.py --check`
Expected: PASS

- [ ] **Step 8: Lint and commit**

```bash
.venv/bin/ruff check .
git add custom_components/tesla_telemetry/const.py custom_components/tesla_telemetry/__init__.py custom_components/tesla_telemetry/sensor.py tests/test_firmware_evidence_wiring.py
git commit -m "Accumulate firmware evidence from the stream; add projected cost"
```

---

## Task 8: The options flow skeleton — menu, preset, cost, finish

**Files:**
- Create: `custom_components/tesla_telemetry/options_flow.py`
- Modify: `custom_components/tesla_telemetry/config_flow.py` (remove `TeslaTelemetryOptionsFlow`, import from the new module)
- Modify: `custom_components/tesla_telemetry/strings.json`, `translations/en.json`
- Modify: `tests/test_translations.py` (scan the new module too)
- Create: `tests/test_options_flow.py`

**Interfaces:**
- Consumes: `signals.signal_overrides`, `signals.active_preset`, `signals.resolve_field_policies`, `cost.monthly_ceiling`, `cost.monthly_floor`, `cost.signals_to_cost`, `firmware.evidence_from_entry`, `presets.PRESETS`.
- Produces: `options_flow.TeslaTelemetryOptionsFlow` with `async_step_init`, `async_step_preset`, `async_step_cost`, `async_step_finish`, and `self._working: dict[str, Any]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_options_flow.py`:

```python
"""Tests for the multi-step options flow.

Edits accumulate in memory and are written once, at `finish`. Abandoning the
flow must persist nothing — a half-applied telemetry config is worse than no
change, because it is pushed to the vehicle.
"""
from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tesla_telemetry.const import (
    CONF_COST_PER_MILLION_SIGNALS,
    CONF_INTERVAL_PRESET,
    CONF_SIGNAL_OVERRIDES,
    DOMAIN,
)

VIN = "5YJ3E1EA1PF000000"


@pytest.fixture
def entry(hass):
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"vin": VIN, "hostname": "telemetry.example.invalid", "port": 443},
        options={},
    )
    config_entry.add_to_hass(hass)
    return config_entry


async def test_init_shows_a_menu(hass, entry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "menu"
    assert set(result["menu_options"]) >= {
        "preset",
        "category",
        "signal",
        "cost",
        "finish",
    }


async def test_choosing_a_preset_and_finishing_stores_it(hass, entry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "preset"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_INTERVAL_PRESET: "eco"}
    )
    # Back at the menu.
    assert result["type"] == "menu"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )
    assert result["type"] == "create_entry"
    assert entry.options[CONF_INTERVAL_PRESET] == "eco"


async def test_abandoning_the_flow_writes_nothing(hass, entry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "preset"}
    )
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_INTERVAL_PRESET: "live"}
    )
    hass.config_entries.options.async_abort(result["flow_id"])
    assert entry.options == {}


async def test_the_cost_step_reports_both_bounds(hass, entry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "cost"}
    )
    placeholders = result["description_placeholders"]
    assert "ceiling" in placeholders
    assert "floor" in placeholders
    # No resend interval is configured by default, so nothing is guaranteed.
    assert placeholders["floor"].endswith("0.00")


async def test_the_cost_rate_round_trips(hass, entry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "cost"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_COST_PER_MILLION_SIGNALS: 8.5}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )
    assert entry.options[CONF_COST_PER_MILLION_SIGNALS] == 8.5


async def test_existing_options_survive_an_unrelated_edit(hass) -> None:
    """Editing the preset must not discard someone's per-signal overrides."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"vin": VIN, "hostname": "telemetry.example.invalid", "port": 443},
        options={CONF_SIGNAL_OVERRIDES: {"VehicleSpeed": 42}},
    )
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "preset"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_INTERVAL_PRESET: "balanced"}
    )
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )

    overrides = config_entry.options[CONF_SIGNAL_OVERRIDES]
    assert overrides["VehicleSpeed"] == {"interval_seconds": 42}


async def test_a_legacy_int_override_is_normalised_on_save(hass) -> None:
    """The dict form is written back, without changing what it means."""
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        data={"vin": VIN, "hostname": "telemetry.example.invalid", "port": 443},
        options={CONF_SIGNAL_OVERRIDES: {"VehicleSpeed": 42, "Odometer": 0}},
    )
    config_entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )

    assert config_entry.options[CONF_SIGNAL_OVERRIDES] == {
        "VehicleSpeed": {"interval_seconds": 42},
        "Odometer": {"interval_seconds": 0},
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_options_flow.py -q`
Expected: FAIL — the current single-form flow returns `form`, not `menu`.

- [ ] **Step 3: Create the options flow module**

Create `custom_components/tesla_telemetry/options_flow.py`:

```python
"""The per-vehicle telemetry options flow.

Lifted out of `config_flow.py`, which is OAuth and should not also own a
five-step form.

Tesla's categories are wildly uneven — Charging alone has 56 signals — and
Phase 4 adds three more keys per signal. A single form would be roughly a
thousand voluptuous fields, which is not a layout problem but a dead form. So
the flow splits along the seam that actually exists: bulk editing wants one
number per signal, per-field tuning wants four fields for one signal.

Every step accumulates into `self._working` and returns to the menu. Nothing
is written until `finish`, so abandoning the flow persists nothing — which
matters more here than in most flows, because saving pushes a new
configuration to the vehicle.
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlowResult, OptionsFlow
from homeassistant.helpers import selector

from .const import (
    CONF_ASSUME_FIRMWARE_SUPPORT,
    CONF_COST_PER_MILLION_SIGNALS,
    CONF_INTERVAL_PRESET,
    CONF_SIGNAL_OVERRIDES,
    DEFAULT_COST_PER_MILLION_SIGNALS,
)
from .cost import monthly_ceiling, monthly_floor, signals_to_cost
from .firmware import evidence_from_entry
from .presets import PRESETS
from .signals import active_preset, resolve_field_policies, signal_overrides

_LOGGER = logging.getLogger(__name__)

MENU_OPTIONS = ["preset", "category", "signal", "cost", "finish"]


class TeslaTelemetryOptionsFlow(OptionsFlow):
    """Menu-driven telemetry configuration."""

    def __init__(self) -> None:
        self._working: dict[str, Any] | None = None

    # -------------------- shared state --------------------
    @property
    def working(self) -> dict[str, Any]:
        """The options being edited, seeded from what is stored.

        Seeded lazily rather than in __init__ because `self.config_entry` is
        not available until Home Assistant has attached it to the flow.
        """
        if self._working is None:
            stored = dict(self.config_entry.options)
            # Normalise the override shape once, here, so every step below
            # works with dicts and the legacy bare-int form disappears the
            # first time anything is saved.
            stored[CONF_SIGNAL_OVERRIDES] = {
                name: dict(value)
                for name, value in signal_overrides(self.config_entry).items()
            }
            self._working = stored
        return self._working

    @property
    def overrides(self) -> dict[str, dict[str, Any]]:
        return self.working.setdefault(CONF_SIGNAL_OVERRIDES, {})

    def _currency(self) -> str:
        return (self.hass.config.currency if self.hass else None) or "USD"

    def _rate(self) -> float:
        return float(
            self.working.get(
                CONF_COST_PER_MILLION_SIGNALS, DEFAULT_COST_PER_MILLION_SIGNALS
            )
        )

    def _pending_policies(self) -> dict[str, Any]:
        """Resolve the edits in progress, without saving them."""

        class _Pending:
            data = self.config_entry.data
            options = self.working

        return resolve_field_policies(
            _Pending(), evidence_from_entry(self.config_entry)
        )

    # -------------------- steps --------------------
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=MENU_OPTIONS)

    async def async_step_preset(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self.working[CONF_INTERVAL_PRESET] = user_input[CONF_INTERVAL_PRESET]
            return await self.async_step_init()
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_INTERVAL_PRESET,
                    default=active_preset(self.config_entry),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=list(PRESETS),
                        mode=selector.SelectSelectorMode.LIST,
                        translation_key="interval_preset",
                    )
                )
            }
        )
        return self.async_show_form(step_id="preset", data_schema=schema)

    async def async_step_cost(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self.working[CONF_COST_PER_MILLION_SIGNALS] = float(
                user_input[CONF_COST_PER_MILLION_SIGNALS]
            )
            return await self.async_step_init()

        policies = self._pending_policies()
        rate = self._rate()
        currency = self._currency()
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_COST_PER_MILLION_SIGNALS, default=rate
                ): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Required(
                    CONF_ASSUME_FIRMWARE_SUPPORT,
                    default=bool(
                        self.working.get(CONF_ASSUME_FIRMWARE_SUPPORT, False)
                    ),
                ): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="cost",
            data_schema=schema,
            description_placeholders={
                "ceiling": f"{currency} "
                f"{signals_to_cost(monthly_ceiling(policies), rate):.2f}",
                "floor": f"{currency} "
                f"{signals_to_cost(monthly_floor(policies), rate):.2f}",
                "signals": str(len(policies)),
            },
        )

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Write everything at once.

        The entry's update listener re-pushes the telemetry config from here,
        so this is the only point at which an edit reaches the vehicle.
        """
        return self.async_create_entry(title="", data=self.working)
```

Note: `async_step_cost` also carries the `assume_firmware_support` toggle. It
lives here rather than in its own step because it is a single boolean and the
cost page is where a user is already looking at what the vehicle will send.

- [ ] **Step 4: Point `config_flow.py` at it**

Delete the `TeslaTelemetryOptionsFlow` class from `config_flow.py` and replace
the `async_get_options_flow` body with:

```python
    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> TeslaTelemetryOptionsFlow:
        return TeslaTelemetryOptionsFlow()
```

adding `from .options_flow import TeslaTelemetryOptionsFlow` to the imports and
removing the now-unused `build_options_schema` / `parse_options_input` import.
Leave those two functions in `signals.py` for now — Task 9 removes them.

- [ ] **Step 5: Add the strings**

In `custom_components/tesla_telemetry/strings.json`, replace the whole
`options` block with:

```json
  "options": {
    "step": {
      "init": {
        "title": "Telemetry configuration",
        "description": "Choose what this vehicle streams, how often, and what it costs.",
        "menu_options": {
          "preset": "Preset",
          "category": "Browse a category",
          "signal": "Find a signal",
          "cost": "Cost and advanced",
          "finish": "Save and close"
        }
      },
      "preset": {
        "title": "Preset",
        "description": "A preset sets an interval for each of Tesla's signal categories. It retunes signals that are already enabled and never switches new ones on. Your per-signal settings always win over the preset.",
        "data": {
          "interval_preset": "Preset"
        }
      },
      "cost": {
        "title": "Cost and advanced",
        "description": "Tesla bills per data point. Your selection of {signals} signals sends at most {ceiling} a month, assuming every signal changes at every opportunity — real usage is normally far below that. At least {floor} a month is guaranteed by the resend intervals you have set, which send even when nothing changes.",
        "data": {
          "cost_per_million_signals": "Cost per million signals",
          "assume_firmware_support": "Send newer field options without waiting for the car to confirm support"
        },
        "data_description": {
          "assume_firmware_support": "Minimum delta, resend interval and included fields need recent firmware. They are normally withheld until your car proves it supports them. Only turn this on if you know your firmware version."
        }
      }
    }
  },
```

Mirror the same block into `custom_components/tesla_telemetry/translations/en.json`.
`tests/test_translations.py::test_strings_and_en_have_the_same_keys` fails if
the two drift, so run it after editing.

Add the preset labels to the existing top-level `selector` block in **both**
files:

```json
    "interval_preset": {
      "options": {
        "default": "Default — the built-in intervals",
        "eco": "Eco — stream sparingly",
        "balanced": "Balanced",
        "live": "Live — fast updates while driving",
        "high_rate": "High rate (legacy) — location and speed at 1s"
      }
    }
```

- [ ] **Step 6: Widen the translation test to the new module**

`tests/test_translations.py` scans `config_flow.py` for `translation_key=` and
`async_abort(reason=...)`. The options flow now lives elsewhere, so those
scans must cover both files or a missing preset label passes unnoticed.

Replace both occurrences of

```python
    source = (_DIR / "config_flow.py").read_text(encoding="utf-8")
```

with

```python
    source = "\n".join(
        (_DIR / name).read_text(encoding="utf-8")
        for name in ("config_flow.py", "options_flow.py")
    )
```

and add a test:

```python
def test_every_preset_has_a_label() -> None:
    """A preset with no label renders as its raw name in the radio list."""
    presets = set(_load_presets().PRESETS)
    for path in (_STRINGS, _EN):
        options = _load(path)["selector"]["interval_preset"]["options"]
        missing = presets - set(options)
        assert not missing, f"{path.name}: presets without a label: {sorted(missing)}"
```

with a loader beside `_load_const`:

```python
def _load_presets() -> ModuleType:
    """Load presets.py under the synthetic package, with no Home Assistant."""
    _load_const()  # presets.py imports nothing from const, but this seeds _PKG
    name = f"{_PKG}.presets"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _DIR / "presets.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
```

If `presets.py`'s relative import of `signal_metadata` defeats the synthetic
package, import `PRESETS` directly from
`custom_components.tesla_telemetry.presets` instead — that module needs no
Home Assistant, so the isolation this file normally maintains is not at stake.

- [ ] **Step 7: Run the tests**

Run: `.venv/bin/pytest tests/test_options_flow.py tests/test_translations.py -q`
Expected: PASS

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS. `tests/test_signals.py` still exercises the old schema
helpers, which still exist.

- [ ] **Step 9: Lint and commit**

```bash
.venv/bin/ruff check .
git add custom_components/tesla_telemetry/options_flow.py custom_components/tesla_telemetry/config_flow.py custom_components/tesla_telemetry/strings.json custom_components/tesla_telemetry/translations/en.json tests/test_options_flow.py tests/test_translations.py
git commit -m "Split the options flow into a menu with preset and cost steps"
```

---

## Task 9: Category browsing, per-signal tuning, and include-field validation

**Files:**
- Modify: `custom_components/tesla_telemetry/options_flow.py`
- Modify: `custom_components/tesla_telemetry/signals.py` (delete `build_options_schema`, `parse_options_input`)
- Modify: `custom_components/tesla_telemetry/strings.json`, `translations/en.json`
- Modify: `tests/test_signals.py` (drop the schema-helper tests)
- Modify: `tests/test_options_flow.py` (extend)

**Interfaces:**
- Consumes: everything from Task 8, plus `signal_metadata.SIGNALS`, `presets.UNCATEGORISED`, `const.SIGNAL_INTERVAL_MAX`.
- Produces: `async_step_category`, `async_step_category_edit`, `async_step_signal`, `async_step_signal_edit`, `category_slug(category: str | None) -> str`, `validate_include_fields(...) -> str | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_options_flow.py`:

```python
async def test_browsing_a_category_lists_its_signals(hass, entry) -> None:
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "category"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"category": "driving"}
    )
    keys = {str(key) for key in result["data_schema"].schema}
    assert "VehicleSpeed" in keys
    # Driving only: a Charging signal must not appear here.
    assert "ChargerVoltage" not in keys


async def test_editing_a_category_pins_only_what_changed(hass, entry) -> None:
    """An untouched row must not become a pinned override."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "category"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"category": "driving"}
    )
    submitted = {
        str(key): key.default() for key in result["data_schema"].schema
    }
    submitted["VehicleSpeed"] = 7
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], submitted
    )
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )

    overrides = entry.options[CONF_SIGNAL_OVERRIDES]
    assert overrides["VehicleSpeed"] == {"interval_seconds": 7}
    assert "Gear" not in overrides


async def test_tuning_one_signal_stores_all_four_knobs(hass, entry) -> None:
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "firmware_evidence": {"proven": "2026.32"}}
    )
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "signal"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"signal": "InsideTemp"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "interval_seconds": 120,
            "minimum_delta": 0.5,
            "resend_interval_seconds": 3600,
            "include_fields": [],
        },
    )
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )
    stored = entry.options[CONF_SIGNAL_OVERRIDES]["InsideTemp"]
    assert stored["interval_seconds"] == 120
    assert stored["minimum_delta"] == 0.5
    assert stored["resend_interval_seconds"] == 3600


async def test_include_fields_rejects_a_disabled_target(hass, entry) -> None:
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, "firmware_evidence": {"proven": "2026.32"}},
        options={CONF_SIGNAL_OVERRIDES: {"Hvil": {"interval_seconds": 0}}},
    )
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "signal"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"signal": "Odometer"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"interval_seconds": 300, "include_fields": ["Hvil"]},
    )
    assert result["type"] == "form"
    assert result["errors"] == {"include_fields": "include_disabled"}


async def test_include_fields_enforces_the_miles_pair_rule(hass, entry) -> None:
    """Tesla: these two may only be included by each other."""
    from custom_components.tesla_telemetry.options_flow import (
        validate_include_fields,
    )

    enabled = {"MilesSinceReset", "SelfDrivingMilesSinceReset", "Odometer"}
    assert (
        validate_include_fields(
            "MilesSinceReset", ["SelfDrivingMilesSinceReset"], enabled
        )
        is None
    )
    assert (
        validate_include_fields("Odometer", ["MilesSinceReset"], enabled)
        == "include_miles_pair"
    )
    assert (
        validate_include_fields(
            "MilesSinceReset", ["Odometer"], enabled
        )
        == "include_miles_pair"
    )


async def test_a_signal_cannot_include_itself(hass) -> None:
    from custom_components.tesla_telemetry.options_flow import (
        validate_include_fields,
    )

    assert (
        validate_include_fields("Odometer", ["Odometer"], {"Odometer"})
        == "include_self"
    )


async def test_include_fields_accepts_a_valid_selection(hass) -> None:
    from custom_components.tesla_telemetry.options_flow import (
        validate_include_fields,
    )

    assert (
        validate_include_fields(
            "Odometer", ["VehicleSpeed"], {"Odometer", "VehicleSpeed"}
        )
        is None
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_options_flow.py -q`
Expected: FAIL — `Unknown step category`, and an ImportError for
`validate_include_fields`.

- [ ] **Step 3: Add the category steps**

Append to `options_flow.py`:

```python
def category_slug(category: str | None) -> str:
    """A translation-safe key for one of Tesla's category names."""
    if category is None:
        return "uncategorised"
    return category.lower().replace(" ", "_")


def _catalog_by_category() -> dict[str, list[str]]:
    """Every catalog signal, grouped by its documented category slug."""
    from .signal_metadata import SIGNALS

    grouped: dict[str, list[str]] = {}
    for name, meta in SIGNALS.items():
        grouped.setdefault(category_slug(meta.category), []).append(name)
    for names in grouped.values():
        names.sort()
    return grouped
```

and these methods to the class:

```python
    async def async_step_category(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        grouped = _catalog_by_category()
        if user_input is not None:
            self._category = user_input["category"]
            return await self.async_step_category_edit()
        options = [
            selector.SelectOptionDict(
                value=slug, label=f"{slug.replace('_', ' ').title()} ({len(names)})"
            )
            for slug, names in sorted(grouped.items())
        ]
        schema = vol.Schema(
            {
                vol.Required("category"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )
        return self.async_show_form(step_id="category", data_schema=schema)

    async def async_step_category_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """One interval per signal in the chosen category. 0 disables.

        Only values that differ from what the row was rendered with are
        stored, so scrolling past a category does not silently pin 56
        intervals and make every future preset a no-op for them.
        """
        names = _catalog_by_category().get(self._category, [])
        effective = self._pending_policies()
        rendered = {
            name: (
                effective[name].interval_seconds if name in effective else 0
            )
            for name in names
        }

        if user_input is not None:
            for name in names:
                submitted = user_input.get(name)
                if submitted is None or int(submitted) == rendered[name]:
                    continue
                self.overrides.setdefault(name, {})["interval_seconds"] = int(
                    submitted
                )
            return await self.async_step_init()

        schema = vol.Schema(
            {
                vol.Optional(name, default=rendered[name]): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=SIGNAL_INTERVAL_MAX)
                )
                for name in names
            }
        )
        return self.async_show_form(
            step_id="category_edit",
            data_schema=schema,
            description_placeholders={
                "category": self._category.replace("_", " ").title()
            },
        )
```

Add `self._category: str = ""` to `__init__` and
`SIGNAL_INTERVAL_MAX` to the `const` import.

- [ ] **Step 4: Add the per-signal steps and validation**

Append to `options_flow.py`:

```python
# Tesla: "MilesSinceReset and SelfDrivingMilesSinceReset may only be included
# by each other."
_MILES_PAIR = frozenset({"MilesSinceReset", "SelfDrivingMilesSinceReset"})


def validate_include_fields(
    signal: str, targets: list[str], enabled: set[str]
) -> str | None:
    """An error key for an invalid include selection, or None.

    Three rules. Two are Tesla's; the third — that a target must itself be
    enabled — is ours, because whether an unconfigured field can be
    piggybacked is undocumented and we stay inside what is known.
    """
    for target in targets:
        if target == signal:
            return "include_self"
        if target not in enabled:
            return "include_disabled"
    if (signal in _MILES_PAIR) != bool(_MILES_PAIR & set(targets)):
        return "include_miles_pair"
    if signal in _MILES_PAIR and set(targets) - _MILES_PAIR:
        return "include_miles_pair"
    return None
```

and these methods:

```python
    async def async_step_signal(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        from .signal_metadata import SIGNALS

        if user_input is not None:
            self._signal = user_input["signal"]
            return await self.async_step_signal_edit()
        # A plain dropdown: Home Assistant's frontend filters it as the user
        # types, which is the search this form needs over ~270 entries.
        schema = vol.Schema(
            {
                vol.Required("signal"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=sorted(SIGNALS),
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        custom_value=False,
                    )
                )
            }
        )
        return self.async_show_form(step_id="signal", data_schema=schema)

    async def async_step_signal_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        from .signal_metadata import SIGNALS

        name = self._signal
        meta = SIGNALS[name]
        policies = self._pending_policies()
        current = policies.get(name)
        evidence = evidence_from_entry(self.config_entry)

        if user_input is not None:
            targets = list(user_input.get("include_fields") or [])
            error = validate_include_fields(name, targets, set(policies))
            if error is not None:
                return self.async_show_form(
                    step_id="signal_edit",
                    data_schema=self._signal_schema(name, user_input),
                    errors={"include_fields": error},
                    description_placeholders=self._signal_placeholders(
                        name, meta, evidence
                    ),
                )
            stored: dict[str, Any] = {
                "interval_seconds": int(user_input["interval_seconds"])
            }
            delta = user_input.get("minimum_delta")
            if delta:
                stored["minimum_delta"] = float(delta)
            resend = user_input.get("resend_interval_seconds")
            if resend:
                stored["resend_interval_seconds"] = int(resend)
            if targets:
                stored["include_fields"] = targets
            self.overrides[name] = stored
            return await self.async_step_init()

        defaults = {
            "interval_seconds": current.interval_seconds if current else 0,
            "minimum_delta": (current.minimum_delta if current else None) or 0,
            "resend_interval_seconds": (
                current.resend_interval_seconds if current else None
            )
            or 0,
            "include_fields": list(current.include_fields) if current else [],
        }
        return self.async_show_form(
            step_id="signal_edit",
            data_schema=self._signal_schema(name, defaults),
            description_placeholders=self._signal_placeholders(
                name, meta, evidence
            ),
        )

    def _signal_schema(self, name: str, defaults: dict[str, Any]) -> vol.Schema:
        from .signal_metadata import SIGNALS

        return vol.Schema(
            {
                vol.Required(
                    "interval_seconds",
                    default=int(defaults.get("interval_seconds") or 0),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=SIGNAL_INTERVAL_MAX)),
                vol.Optional(
                    "minimum_delta",
                    default=float(defaults.get("minimum_delta") or 0),
                ): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Optional(
                    "resend_interval_seconds",
                    default=int(defaults.get("resend_interval_seconds") or 0),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=SIGNAL_INTERVAL_MAX)),
                vol.Optional(
                    "include_fields",
                    default=list(defaults.get("include_fields") or []),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[s for s in sorted(SIGNALS) if s != name],
                        multiple=True,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        custom_value=False,
                    )
                ),
            }
        )

    def _signal_placeholders(
        self, name: str, meta: Any, evidence: Any
    ) -> dict[str, str]:
        """Explain the delta's unit, Tesla's own default, and any gating.

        A blank delta field does not mean "no delta" — the car applies one of
        its own to ChargerVoltage and Odometer — and a value typed while the
        floor is unmet is stored but withheld. Both need saying, here, or the
        setting looks broken.
        """
        from .firmware import FLOOR_INCLUDE_FIELDS, FLOOR_MINIMUM_DELTA

        # Tesla measures location deltas in metres; everything else uses the
        # signal's own unit.
        unit = "m" if meta.value_type == "Location" else (meta.unit or "")
        notes: list[str] = []
        if meta.minimum_delta_required is not None:
            notes.append(
                f"Tesla requires a minimum delta of at least "
                f"{meta.minimum_delta_required} for this field; without one it "
                f"never reports."
            )
        if meta.minimum_delta_default is not None:
            notes.append(
                f"The car already applies a default minimum delta of "
                f"{meta.minimum_delta_default} on recent firmware."
            )
        elif meta.minimum_delta_recommended:
            notes.append("Tesla recommends setting a minimum delta for this field.")
        if not evidence.supports(FLOOR_MINIMUM_DELTA):
            notes.append(
                "Your car has not yet confirmed firmware "
                f"{FLOOR_MINIMUM_DELTA}, so minimum delta and resend interval "
                "are stored but not sent until it does."
            )
        if not evidence.supports(FLOOR_INCLUDE_FIELDS):
            notes.append(
                "Included fields need firmware "
                f"{FLOOR_INCLUDE_FIELDS}, which your car has not confirmed."
            )
        return {
            "signal": name,
            "unit": unit or "—",
            "notes": " ".join(notes) or "No special requirements.",
        }
```

Add `self._signal: str = ""` to `__init__`.

- [ ] **Step 5: Add the strings for the four new steps**

Add to the `options.step` block in **both** `strings.json` and
`translations/en.json`:

```json
      "category": {
        "title": "Browse a category",
        "description": "Pick one of Tesla's signal categories."
      },
      "category_edit": {
        "title": "{category}",
        "description": "Minimum seconds between updates for each signal. 0 turns a signal off. Tesla only sends a value when it changes, so a short interval on a rarely-changing signal costs almost nothing."
      },
      "signal": {
        "title": "Find a signal",
        "description": "Start typing to search the whole catalogue."
      },
      "signal_edit": {
        "title": "{signal}",
        "description": "Delta is measured in {unit}. {notes}",
        "data": {
          "interval_seconds": "Minimum seconds between updates (0 turns it off)",
          "minimum_delta": "Minimum change before an update is sent (0 for none)",
          "resend_interval_seconds": "Resend even when unchanged, every N seconds (0 for never)",
          "include_fields": "Also send these fields in the same payload"
        }
      }
```

and an `error` block in the `options` object:

```json
    "error": {
      "include_self": "A signal cannot include itself.",
      "include_disabled": "An included field must itself be enabled.",
      "include_miles_pair": "MilesSinceReset and SelfDrivingMilesSinceReset may only be included by each other."
    }
```

- [ ] **Step 6: Delete the superseded schema helpers**

Remove `build_options_schema` and `parse_options_input` from `signals.py`,
along with the `# ---- Options-flow schema helpers` banner comment and the
`_curated_signals` / `additional_signals` helpers **if nothing else imports
them** — check with:

```bash
grep -rn "build_options_schema\|parse_options_input\|additional_signals\|_curated_signals" --include=*.py . | grep -v "^./.venv"
```

Delete the corresponding tests from `tests/test_signals.py`. Keep every test
in that file that covers `all_catalog_signals` or interval resolution.

- [ ] **Step 7: Run the tests**

Run: `.venv/bin/pytest tests/test_options_flow.py tests/test_signals.py tests/test_translations.py -q`
Expected: PASS

- [ ] **Step 8: Run the full suite and mutation-test the validation**

Run: `.venv/bin/pytest -q`
Expected: PASS

Temporarily make `validate_include_fields` return `None` unconditionally.

Run: `.venv/bin/pytest tests/test_options_flow.py -q`
Expected: FAIL — the three include-field tests.

Restore it.

- [ ] **Step 9: Lint and commit**

```bash
.venv/bin/ruff check .
git add custom_components/tesla_telemetry/options_flow.py custom_components/tesla_telemetry/signals.py custom_components/tesla_telemetry/strings.json custom_components/tesla_telemetry/translations/en.json tests/
git commit -m "Add category browsing and per-signal tuning to the options flow"
```

---

## Task 10: Staleness, the preset service, and the docs

**Files:**
- Modify: `custom_components/tesla_telemetry/coordinator.py` (`is_stale`)
- Modify: `custom_components/tesla_telemetry/services.py` (`set_interval_preset` writes options)
- Modify: `README.md`
- Test: `tests/test_staleness.py` (new)

**Interfaces:**
- Consumes: `signals.resolve_field_policies`.
- Produces: `TeslaTelemetryCoordinator.resend_intervals: dict[str, int]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_staleness.py`:

```python
"""Tests for staleness, which resend intervals make answerable.

`STALE_INTERVAL_MULTIPLIER x interval_seconds` assumes a signal arrives at
least every interval. Push-on-change means it does not: an unchanged signal is
never resent, so a healthy entity can read `unavailable`. Today's behaviour is
kept deliberately — it is also the only sign a user gets that a car has gone
offline — but where a resend interval exists, it is the sound basis and is
used instead.
"""
from __future__ import annotations

from custom_components.tesla_telemetry.const import STALE_INTERVAL_MULTIPLIER
from custom_components.tesla_telemetry.coordinator import (
    TeslaTelemetryCoordinator,
)
from custom_components.tesla_telemetry.proto import vehicle_data_pb2

VIN = "5YJ3E1EA1PF000000"


def _coordinator(hass) -> TeslaTelemetryCoordinator:
    return TeslaTelemetryCoordinator(hass, VIN, "Test Car")


def _publish(coordinator, name: str) -> None:
    value = vehicle_data_pb2.Value()
    value.double_value = 1.0
    coordinator.async_publish(name, value)


async def test_a_fresh_sample_is_not_stale(hass) -> None:
    coordinator = _coordinator(hass)
    coordinator.effective_intervals = {"Odometer": 300}
    _publish(coordinator, "Odometer")
    assert not coordinator.is_stale("Odometer")


async def test_the_interval_basis_is_unchanged_without_a_resend(hass) -> None:
    coordinator = _coordinator(hass)
    coordinator.effective_intervals = {"Odometer": 300}
    _publish(coordinator, "Odometer")
    later = coordinator.get("Odometer").received_at + 300 * STALE_INTERVAL_MULTIPLIER + 1
    assert coordinator.is_stale("Odometer", now=later)


async def test_a_resend_interval_becomes_the_basis(hass) -> None:
    """With a resend the car really does send, so the question is answerable."""
    coordinator = _coordinator(hass)
    coordinator.effective_intervals = {"Odometer": 300}
    coordinator.resend_intervals = {"Odometer": 3600}
    _publish(coordinator, "Odometer")
    received = coordinator.get("Odometer").received_at

    # Well past the interval basis, nowhere near the resend basis.
    assert not coordinator.is_stale(
        "Odometer", now=received + 300 * STALE_INTERVAL_MULTIPLIER + 1
    )
    assert coordinator.is_stale(
        "Odometer", now=received + 3600 * STALE_INTERVAL_MULTIPLIER + 1
    )


async def test_a_signal_never_seen_is_stale(hass) -> None:
    assert _coordinator(hass).is_stale("Odometer")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_staleness.py -q`
Expected: FAIL — `test_a_resend_interval_becomes_the_basis`
(`AttributeError: 'TeslaTelemetryCoordinator' object has no attribute 'resend_intervals'`)

- [ ] **Step 3: Use the resend interval where one exists**

In `coordinator.py.__init__`, beside `effective_intervals`:

```python
        # Resend intervals, where configured. A resend is a commitment to send
        # even when nothing changed, which is the only thing that makes
        # staleness a sound question: without one, an unchanged signal is never
        # resent and a perfectly healthy entity looks stale.
        self.resend_intervals: dict[str, int] = {}
```

and in `is_stale`, replace the interval lookup:

```python
        interval = self.resend_intervals.get(name) or self.effective_intervals.get(
            name, 60
        )
```

- [ ] **Step 4: Populate it wherever `effective_intervals` is set**

In `__init__.py.async_setup_entry`, after
`coordinator.effective_intervals = resolve_effective_intervals(entry)`:

```python
    policies = resolve_field_policies(entry, evidence_from_entry(entry))
    coordinator.effective_intervals = {
        name: policy.interval_seconds for name, policy in policies.items()
    }
    coordinator.resend_intervals = {
        name: policy.resend_interval_seconds
        for name, policy in policies.items()
        if policy.resend_interval_seconds
    }
```

replacing the single `resolve_effective_intervals` call. Do the same in
`_async_options_updated`, where Task 5 already computes `new_fields`:

```python
    coordinator.resend_intervals = {
        name: body["resend_interval_seconds"]
        for name, body in new_fields.items()
        if "resend_interval_seconds" in body
    }
```

- [ ] **Step 5: Make `set_interval_preset` write options**

In `services.py`, the preset handler currently writes `entry.data`. Two stores
for one setting is the bug where the service appears to do nothing because
options hold a different preset. Write options instead:

```python
    hass.config_entries.async_update_entry(
        entry,
        options={**entry.options, CONF_INTERVAL_PRESET: preset},
    )
```

`signals.active_preset` still reads `entry.data` as a fallback, so an entry
last written by the old service keeps its preset until something saves.

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/pytest tests/test_staleness.py -q && .venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 7: Update the README**

Add a `### Telemetry options` section after the existing configuration
section, covering:

- The five-step options menu and what each step is for.
- The preset table from the spec, as a markdown table, with one sentence that
  a preset retunes enabled signals and never enables new ones.
- That `interval_seconds` is a ceiling and Tesla pushes on change, so a short
  interval on a rarely-changing signal costs almost nothing — and that
  `ChargerVoltage` under the `live` preset is the exception worth knowing
  about.
- The three newer field options, their firmware floors verbatim
  (`minimum_delta` and `resend_interval_seconds` need 2024.44.32,
  `include_fields` needs 2026.26.6), and that they are withheld until the car
  proves support, with `assume_firmware_support` as the override.
- The cost figures: the ceiling and floor in the form are bounds, the
  `Projected monthly signal cost` sensor is a measurement over the time since
  the last restart, and `Estimated signal cost` remains the lifetime total.
- **The staleness limitation**, in its own short paragraph: entities can read
  `unavailable` for a signal that simply has not changed, and setting a resend
  interval on signals whose freshness matters is the fix.

- [ ] **Step 8: Full verification**

```bash
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/python scripts/gen_signal_metadata.py --check
```

Expected: all three pass.

- [ ] **Step 9: Commit**

```bash
git add custom_components/tesla_telemetry/coordinator.py custom_components/tesla_telemetry/__init__.py custom_components/tesla_telemetry/services.py README.md tests/test_staleness.py
git commit -m "Base staleness on resend intervals; document the options UI"
```
