# Generic Entities Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create entities for any signal a vehicle streams, chosen at runtime from the oneof arm Tesla actually sends, without hand-written code per signal — and get there without orphaning any existing entity's history.

**Architecture:** Each platform registers its add-entities callback with a shared factory. The factory watches the coordinator for the first datum of each signal, skips signals claimed by curated entities, and routes the rest to `binary_sensor` / `device_tracker` / `sensor` by arm. On setup it recreates entities already in the entity registry so slow signals are not missing until they next change. A one-time `async_migrate_entry` rewrites the legacy `unique_id`s.

**Tech Stack:** Python 3.14, Home Assistant 2026.9, protobuf. pytest with `pytest-homeassistant-custom-component`.

**Spec:** `docs/superpowers/specs/2026-09-25-generic-entities-design.md`

## Global Constraints

- `unique_id` for every generic entity is exactly `{vin}_{snake(signal)}_telemetry`.
- A signal in `CLAIMED_SIGNALS` gets **no** generic entity and is **not** migrated.
- Entity type comes from the arm Tesla sends, never from `SignalMeta.value_type`.
- The platform is fixed at first datum and never changes afterwards.
- `min_firmware` and `semi_only` are **not** consulted in this phase.
- Enum members come from the live protobuf descriptor, never from metadata.
- Members meaning unknown/SNA map to `None` and never appear in `options`.
- `async_migrate_entry` must never raise: an exception there fails the whole config entry.
- `ruff check .` and `pytest` must pass. `signal_metadata.py` stays generated — never hand-edit it.
- Do not change `DEFAULT_INTERVALS_SECONDS`. This phase changes how data becomes entities, not what is requested.

## Review Focus

Failure modes the spec implies that no happy path exercises. Each has a test in the task that owns the code.

1. **A signal arrives in a different arm than the entity was created for** (sentry is boolean on old firmware, enum on new). Must keep the last value and not crash or flap. — Task 4
2. **An enum member the vendored proto does not know** (a newer car sends a higher number). Must yield `None`, not a raw integer or an `options` violation. — Task 4
3. **The migration runs when both old and new `unique_id` exist.** Must skip, not raise — `async_update_entity` raises on a used id, and that would fail the whole entry. — Task 6
4. **A datum arrives for a signal absent from `SIGNALS`** (proto newer than metadata). Must be ignored quietly, not raise KeyError inside the dispatcher. — Task 5
5. **Two data for the same new signal arrive back to back** before the first entity is added. Must create one entity, not two. — Task 5

---

## File Structure

| File | Responsibility |
|---|---|
| `custom_components/tesla_telemetry/generic/__init__.py` | package marker |
| `custom_components/tesla_telemetry/generic/naming.py` | `snake()`, `humanise()`, `generic_unique_id()` |
| `custom_components/tesla_telemetry/generic/routing.py` | arm → platform, and the enum label/option resolver |
| `custom_components/tesla_telemetry/generic/entities.py` | the three generic entity classes |
| `custom_components/tesla_telemetry/generic/factory.py` | callback registry, first-sight creation, restore-on-restart |
| `custom_components/tesla_telemetry/generic/claimed.py` | `CLAIMED_SIGNALS` |
| `custom_components/tesla_telemetry/migration.py` | `LEGACY_UNIQUE_IDS`, `async_migrate_unique_ids()` |
| `tests/fixtures/legacy_unique_ids.json` | captured evidence — **written in Task 1, never regenerated** |
| `signal_catalog/overrides.py` | gains an optional `enum_labels` field |

---

### Task 1: Capture the evidence, and decide the partition

This runs **before any hand-written entity is touched**. Once the old code is gone, the mapping it records is unrecoverable, and it is the only thing that can prove no user loses history.

**Files:**
- Create: `scripts/capture_legacy_unique_ids.py`
- Create: `tests/fixtures/legacy_unique_ids.json`
- Create: `custom_components/tesla_telemetry/generic/__init__.py`, `generic/claimed.py`
- Test: `tests/generic/__init__.py`, `tests/generic/test_claimed.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `generic.claimed.CLAIMED_SIGNALS: frozenset[str]`, and the fixture, a JSON object `{"<unique_id suffix>": {"signal": "<Name>", "platform": "<domain>"}}`.

- [ ] **Step 1: Write the capture script**

It instantiates the real platforms against a stub coordinator and records what each entity declares. Instantiating rather than parsing source is what catches fan-out: `DoorState` feeds several `DoorBinarySensor` instances from one class, which source-parsing misses.

```python
#!/usr/bin/env python3
"""Record every current entity's (unique_id suffix -> signal, platform).

Run once, before the hand-written entities are replaced. The output is
committed as tests/fixtures/legacy_unique_ids.json and is the evidence that
the migration loses nobody's history. It is never regenerated: after the old
code is gone there is nothing left to regenerate it from.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

REPO = Path(__file__).resolve().parents[1]
VIN = "CAPTUREVIN0000000"


async def _collect() -> dict[str, dict[str, str]]:
    from custom_components.tesla_telemetry import (
        binary_sensor as bs,
        device_tracker as dt,
        sensor as se,
    )

    coordinator = SimpleNamespace(
        vin=VIN,
        vehicle_name="Captured",
        device_info={},
        effective_intervals={},
        get=lambda name: None,
    )
    hass = MagicMock()
    hass.data = {"tesla_telemetry": {"entry": {"coordinator": coordinator}}}
    entry = SimpleNamespace(entry_id="entry", data={"vin": VIN}, options={})

    out: dict[str, dict[str, str]] = {}
    for module, domain in ((se, "sensor"), (bs, "binary_sensor"), (dt, "device_tracker")):
        captured: list[object] = []
        await module.async_setup_entry(hass, entry, lambda es, **kw: captured.extend(es))
        for entity in captured:
            uid = getattr(entity, "_attr_unique_id", None) or getattr(entity, "unique_id", None)
            if not uid or not uid.startswith(f"{VIN}_"):
                continue
            out[uid[len(VIN) + 1:]] = {
                "signal": getattr(entity, "_signal_name", "") or "",
                "platform": domain,
            }
    return out


def main() -> int:
    import sys

    sys.path.insert(0, str(REPO))
    data = asyncio.run(_collect())
    target = REPO / "tests/fixtures/legacy_unique_ids.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print(f"captured {len(data)} entities to {target.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run it and inspect the result**

Run: `python3 scripts/capture_legacy_unique_ids.py`
Expected: `captured NN entities to tests/fixtures/legacy_unique_ids.json` with NN ≥ 60.

If the platforms need more of the coordinator than the stub provides, extend
`SimpleNamespace` until it runs — do not narrow what is captured.

- [ ] **Step 3: Derive the partition and review it**

```bash
python3 - <<'PY'
import json, re, collections
from pathlib import Path
data = json.loads(Path("tests/fixtures/legacy_unique_ids.json").read_text())

def snake(n):
    n = re.sub(r'(?<=[a-z0-9])(?=[A-Z])', '_', n)
    return re.sub(r'(?<=[A-Z])(?=[A-Z][a-z])', '_', n).lower()

per_signal = collections.Counter(v["signal"] for v in data.values() if v["signal"])
print("FAN-OUT (>1 entity per signal) — must be claimed:")
for s, c in sorted(per_signal.items()):
    if c > 1:
        print(f"   {s:<28} {c} entities")
print()
print("RENAMED (generic rule differs) — candidates for the migration map:")
for suffix, v in sorted(data.items()):
    sig = v["signal"]
    if sig and snake(sig) + "_telemetry" != suffix:
        print(f"   {sig:<28} {suffix:<38} -> {snake(sig)}_telemetry")
print()
print("NO SIGNAL (accounting) — never generic, never migrated:")
for suffix, v in sorted(data.items()):
    if not v["signal"]:
        print(f"   {suffix}")
PY
```

Read the three lists and decide `CLAIMED_SIGNALS`. The rule: a signal is claimed when it feeds **more than one** entity (fan-out), or its entity derives a value no single signal carries, or transforms it in a way the generic path cannot (a minutes-to-timestamp conversion). Everything else becomes generic.

**Stop and show the three lists and the proposed `CLAIMED_SIGNALS` to your human partner before continuing.** This partition decides which entities migrate; a mistake is silent and expensive.

- [ ] **Step 4: Write the failing test**

```python
# tests/generic/test_claimed.py
"""CLAIMED_SIGNALS must match what the curated entities actually subscribe to.

Derived by instantiating the real platforms rather than parsing source: one
class can produce several entities from one signal (DoorState feeds every
DoorBinarySensor), and source-parsing counts that as one.

A curated entity added without claiming its signal would silently get a
duplicate generic entity for the same data; a claim left behind after
deleting one would silently suppress a generic entity that should exist.
"""
from __future__ import annotations

import json
from pathlib import Path

from custom_components.tesla_telemetry.generic.claimed import CLAIMED_SIGNALS

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/legacy_unique_ids.json"


def test_every_claimed_signal_is_real() -> None:
    from custom_components.tesla_telemetry.signal_metadata import SIGNALS

    unknown = sorted(CLAIMED_SIGNALS - set(SIGNALS))
    assert not unknown, f"claimed signals that are not in the catalog: {unknown}"


def test_fan_out_signals_are_claimed() -> None:
    """A signal feeding more than one curated entity cannot be generic."""
    data = json.loads(FIXTURE.read_text())
    counts: dict[str, int] = {}
    for value in data.values():
        if value["signal"]:
            counts[value["signal"]] = counts.get(value["signal"], 0) + 1
    fan_out = {s for s, c in counts.items() if c > 1}
    missing = sorted(fan_out - CLAIMED_SIGNALS)
    assert not missing, f"fan-out signals not claimed: {missing}"
```

- [ ] **Step 5: Run test to verify it fails**

Run: `pytest tests/generic/test_claimed.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'custom_components.tesla_telemetry.generic'`

- [ ] **Step 6: Write `claimed.py` with the reviewed partition**

```python
# custom_components/tesla_telemetry/generic/claimed.py
"""Signals owned by hand-written entities.

A claimed signal gets no generic entity and is not migrated. Three reasons a
signal is claimed:

  fan-out     one signal feeding several entities — DoorState produces every
              DoorBinarySensor from one composite struct
  derived     a value no single signal carries, such as charging-active or
              the plugged-in cable veto
  transform   a conversion the generic path cannot express

Keep this in step with the curated entities: tests/generic/test_claimed.py
fails both when a claim is missing and when one is left behind.
"""
from __future__ import annotations

CLAIMED_SIGNALS: frozenset[str] = frozenset(
    {
        # ... the set agreed in Step 3, one entry per line with its reason ...
    }
)
```

> Step 3 produces this content and your human partner approves it. Transcribe
> exactly what was agreed — do not invent entries.

- [ ] **Step 7: Run test to verify it passes**

Run: `pytest tests/generic/test_claimed.py -v`
Expected: PASS (2 tests)

- [ ] **Step 8: Commit**

```bash
git add scripts/capture_legacy_unique_ids.py tests/fixtures/legacy_unique_ids.json \
        custom_components/tesla_telemetry/generic/ tests/generic/
git commit -m "Capture the pre-change entity unique_ids and claim curated signals"
```

---

### Task 2: Naming

**Files:**
- Create: `custom_components/tesla_telemetry/generic/naming.py`
- Test: `tests/generic/test_naming.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `snake(name: str) -> str`, `humanise(name: str) -> str`, `generic_unique_id(vin: str, signal: str) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/generic/test_naming.py
"""Naming rules for generic entities.

snake() decides every generic unique_id, so it is pinned hard: a change here
silently orphans history for every entity it touches.
"""
from __future__ import annotations

import pytest

from custom_components.tesla_telemetry.generic.naming import (
    generic_unique_id,
    humanise,
    snake,
)


@pytest.mark.parametrize(
    ("signal", "expected"),
    [
        ("VehicleSpeed", "vehicle_speed"),
        ("Soc", "soc"),
        ("DCChargingPower", "dc_charging_power"),      # leading acronym
        ("ACChargingEnergyIn", "ac_charging_energy_in"),
        ("TpmsPressureFl", "tpms_pressure_fl"),
        ("Hvil", "hvil"),
        ("DiStatorTempF", "di_stator_temp_f"),
        ("LifetimeEnergyChargedKwh", "lifetime_energy_charged_kwh"),
        ("GpsAccuracyMeters", "gps_accuracy_meters"),
    ],
)
def test_snake(signal: str, expected: str) -> None:
    assert snake(signal) == expected


def test_unique_id_shape() -> None:
    assert generic_unique_id("VIN123", "VehicleSpeed") == "VIN123_vehicle_speed_telemetry"


@pytest.mark.parametrize(
    ("signal", "expected"),
    [
        ("RemoteStartActive", "Remote start active"),
        ("VehicleSpeed", "Vehicle speed"),
        ("DCChargingPower", "DC charging power"),
    ],
)
def test_humanise(signal: str, expected: str) -> None:
    assert humanise(signal) == expected


def test_snake_is_stable_for_the_whole_catalog() -> None:
    """Two signals must never collide into one unique_id."""
    from custom_components.tesla_telemetry.signal_metadata import SIGNALS

    seen: dict[str, str] = {}
    for name in SIGNALS:
        key = snake(name)
        assert key not in seen, f"{name} and {seen[key]} both snake to {key!r}"
        seen[key] = name
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/generic/test_naming.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named '...generic.naming'`

- [ ] **Step 3: Write minimal implementation**

```python
# custom_components/tesla_telemetry/generic/naming.py
"""Naming rules for generic entities.

snake() decides every generic unique_id. Changing it orphans history for
every entity it touches, so it is pinned by tests against the whole catalog.
"""
from __future__ import annotations

import re

_LOWER_TO_UPPER = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_ACRONYM_END = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")

# Acronyms that read wrong when sentence-cased: "Dc charging power".
_ACRONYMS = {"dc", "ac", "gps", "hvac", "tpms", "soc", "vin", "bms", "hvil"}


def snake(name: str) -> str:
    """CamelCase signal name to snake_case."""
    return _ACRONYM_END.sub("_", _LOWER_TO_UPPER.sub("_", name)).lower()


def humanise(name: str) -> str:
    """A readable entity name. has_entity_name prefixes the device name."""
    words = snake(name).split("_")
    out = [w.upper() if w in _ACRONYMS else w for w in words]
    first = out[0]
    return " ".join([first if first.isupper() else first.capitalize(), *out[1:]])


def generic_unique_id(vin: str, signal: str) -> str:
    return f"{vin}_{snake(signal)}_telemetry"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/generic/test_naming.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
git add custom_components/tesla_telemetry/generic/naming.py tests/generic/test_naming.py
git commit -m "Add naming rules for generic entities"
```

---

### Task 3: Arm routing

**Files:**
- Create: `custom_components/tesla_telemetry/generic/routing.py`
- Test: `tests/generic/test_routing.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `platform_for(value) -> str | None` returning `"sensor"`, `"binary_sensor"`, `"device_tracker"` or `None`; `BOOLEAN_ARM`, `LOCATION_ARM`, `NUMERIC_ARMS`.

- [ ] **Step 1: Write the failing test**

```python
# tests/generic/test_routing.py
"""Which platform a datum belongs to.

Decided from the oneof arm Tesla actually sent, never from metadata: the
catalog says a signal exists and what Tesla documents it as, not that this
car sends it or in which arm.
"""
from __future__ import annotations

import pytest

pb = pytest.importorskip("custom_components.tesla_telemetry.proto.vehicle_data_pb2")

from custom_components.tesla_telemetry.generic.routing import platform_for  # noqa: E402


def test_boolean_becomes_a_binary_sensor() -> None:
    assert platform_for(pb.Value(boolean_value=True)) == "binary_sensor"


def test_location_becomes_a_device_tracker() -> None:
    value = pb.Value(location_value=pb.LocationValue(latitude=1.0, longitude=2.0))
    assert platform_for(value) == "device_tracker"


@pytest.mark.parametrize(
    "value",
    [
        pb.Value(shift_state_value=pb.ShiftStateP),
        pb.Value(double_value=12.5),
        pb.Value(int_value=3),
        pb.Value(long_value=4),
        pb.Value(float_value=1.5),
        pb.Value(string_value="2026.32"),
    ],
)
def test_everything_else_becomes_a_sensor(value: object) -> None:
    assert platform_for(value) == "sensor"


def test_invalid_creates_nothing() -> None:
    """An invalid first datum carries no type information, so it cannot
    decide a platform. Wait for a real one."""
    assert platform_for(pb.Value(invalid=True)) is None


def test_empty_value_creates_nothing() -> None:
    assert platform_for(pb.Value()) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/generic/test_routing.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named '...generic.routing'`

- [ ] **Step 3: Write minimal implementation**

```python
# custom_components/tesla_telemetry/generic/routing.py
"""Choose an entity platform from the oneof arm Tesla sent.

The catalog metadata says a signal exists and what Tesla documents it as. It
does not say this car sends it, or in which arm — only the arriving datum
knows that. Phase 1 showed the cost of assuming otherwise: three signals
gated on firmware 2026.32 were enabled by default and would have produced
permanently-unknown entities on an older car.
"""
from __future__ import annotations

from typing import Any

BOOLEAN_ARM = "boolean_value"
LOCATION_ARM = "location_value"
NUMERIC_ARMS = frozenset({"int_value", "long_value", "float_value", "double_value"})

SENSOR = "sensor"
BINARY_SENSOR = "binary_sensor"
DEVICE_TRACKER = "device_tracker"


def platform_for(value: Any) -> str | None:
    """The platform this datum belongs to, or None if it cannot decide."""
    if value.HasField("invalid"):
        return None
    arm = value.WhichOneof("value")
    if arm is None:
        return None
    if arm == BOOLEAN_ARM:
        return BINARY_SENSOR
    if arm == LOCATION_ARM:
        return DEVICE_TRACKER
    # Enum, numeric and string all present as a sensor. There are 42 enum
    # arms in the Value message, so this is the catch-all rather than a list.
    return SENSOR
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/generic/test_routing.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add custom_components/tesla_telemetry/generic/routing.py tests/generic/test_routing.py
git commit -m "Add arm-based platform routing for generic entities"
```

---

### Task 4: The generic entity classes, and enum labels

**Files:**
- Create: `custom_components/tesla_telemetry/generic/entities.py`
- Modify: `signal_catalog/overrides.py` (add `enum_labels`), then regenerate
- Modify: `scripts/signal_metadata/reconcile.py`, `render.py` (carry `enum_labels`)
- Test: `tests/generic/test_entities.py`

**Interfaces:**
- Consumes: `naming.generic_unique_id`, `naming.humanise`, `SignalMeta`.
- Produces: `GenericSensor`, `GenericBinarySensor`, `GenericTracker`, each `(coordinator, signal: str, meta: SignalMeta)`; `enum_options(value, meta) -> list[str] | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/generic/test_entities.py
"""The generic entity classes.

Built on real protobuf Value messages rather than mocks, so a proto bump that
renames or renumbers an enum fails here.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pb = pytest.importorskip("custom_components.tesla_telemetry.proto.vehicle_data_pb2")

from custom_components.tesla_telemetry.generic.entities import (  # noqa: E402
    GenericBinarySensor,
    GenericSensor,
)
from custom_components.tesla_telemetry.signal_metadata import SignalMeta  # noqa: E402

VIN = "TESTVIN0000000001"


def _coordinator():
    return SimpleNamespace(vin=VIN, device_info={}, get=lambda name: None)


def _meta(**kw) -> SignalMeta:
    base = dict(
        field_id=1, category=None, value_type=None, enum_name=None, unit=None,
        device_class=None, state_class=None, enum_labels=None,
        min_firmware=None, semi_only=False, documented=True,
    )
    base.update(kw)
    return SignalMeta(**base)


def _sample(value):
    return SimpleNamespace(value=value, received_at=0.0, payload_created_at=None)


def test_numeric_sensor_carries_metadata() -> None:
    meta = _meta(unit="mph", device_class="speed", state_class="measurement")
    entity = GenericSensor(_coordinator(), "VehicleSpeed", meta)
    assert entity.unique_id == f"{VIN}_vehicle_speed_telemetry"
    assert entity.name == "Vehicle speed"
    assert entity.native_unit_of_measurement == "mph"
    entity._handle(_sample(pb.Value(double_value=42.5)))
    assert entity.native_value == 42.5


def test_enum_sensor_uses_snake_case_by_default() -> None:
    entity = GenericSensor(_coordinator(), "Gear", _meta(enum_name="ShiftState"))
    entity._handle(_sample(pb.Value(shift_state_value=pb.ShiftStateD)))
    assert entity.native_value == "d"


def test_enum_labels_override_the_default() -> None:
    """What keeps Gear reporting "P" instead of "p"."""
    meta = _meta(enum_name="ShiftState",
                 enum_labels={"ShiftStateP": "P", "ShiftStateD": "D"})
    entity = GenericSensor(_coordinator(), "Gear", meta)
    entity._handle(_sample(pb.Value(shift_state_value=pb.ShiftStateP)))
    assert entity.native_value == "P"
    assert entity.options is not None
    assert "P" in entity.options and "p" not in entity.options


def test_unknown_enum_member_is_none_not_an_option_violation() -> None:
    """Review Focus 2: a member the vendored proto does not know."""
    entity = GenericSensor(_coordinator(), "Gear", _meta(enum_name="ShiftState"))
    entity._handle(_sample(pb.Value(shift_state_value=pb.ShiftStateUnknown)))
    assert entity.native_value is None
    assert "unknown" not in (entity.options or [])


def test_invalid_makes_an_existing_entity_unavailable() -> None:
    entity = GenericSensor(_coordinator(), "VehicleSpeed", _meta(unit="mph"))
    entity._handle(_sample(pb.Value(double_value=10.0)))
    assert entity.available is True
    entity._handle(_sample(pb.Value(invalid=True)))
    assert entity.available is False


def test_a_datum_in_an_unrepresentable_arm_keeps_the_last_value() -> None:
    """Review Focus 1: sentry is boolean on old firmware, enum on new.

    The platform is fixed at creation — changing it would lose history — so an
    arm the entity cannot represent must leave the last value alone rather
    than crash or flap to unavailable.
    """
    entity = GenericBinarySensor(_coordinator(), "SentryMode", _meta())
    entity._handle(_sample(pb.Value(boolean_value=True)))
    assert entity.is_on is True
    entity._handle(_sample(pb.Value(string_value="nonsense")))
    assert entity.is_on is True
    assert entity.available is True


def test_binary_sensor_reads_a_boolean() -> None:
    entity = GenericBinarySensor(_coordinator(), "Locked", _meta())
    entity._handle(_sample(pb.Value(boolean_value=False)))
    assert entity.is_on is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/generic/test_entities.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named '...generic.entities'`

- [ ] **Step 3: Add `enum_labels` to the metadata pipeline**

`signal_catalog/overrides.py` — extend the dataclass:

```python
@dataclass(frozen=True, slots=True)
class Override:
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    # Display labels for an enum signal, keyed by the proto member name.
    # Without one, members are stripped and snake_cased. With one, these
    # values are both the mapping and the HA `options` list — which is what
    # keeps Gear reporting "P" rather than "p" after genericization.
    enum_labels: dict[str, str] | None = None
```

Add the Gear entry:

```python
    'Gear': Override(None, 'enum', None, {
        "ShiftStateP": "P",
        "ShiftStateR": "R",
        "ShiftStateN": "N",
        "ShiftStateD": "D",
    }),
```

`scripts/signal_metadata/reconcile.py` — add the field to `SignalRecord` after
`state_class`, and populate it:

```python
    enum_labels: dict[str, str] | None
```
```python
                enum_labels=override.enum_labels if override else None,
```

`scripts/signal_metadata/render.py` — add to the dataclass in `_HEADER` after
`state_class`, and to the per-record block:

```python
    enum_labels: dict[str, str] | None
```
```python
            f"        enum_labels={record.enum_labels!r},\n"
```

- [ ] **Step 4: Regenerate and confirm the pipeline still passes**

Run: `python3 scripts/gen_signal_metadata.py --offline && pytest tests/signal_metadata/ -q`
Expected: `wrote 251 signals`, all signal_metadata tests PASS.

- [ ] **Step 5: Write the entity classes**

```python
# custom_components/tesla_telemetry/generic/entities.py
"""Entity classes for signals with no hand-written entity.

The platform is fixed when the entity is created and never changes: migrating
an entity between platforms would lose its history, which is the thing this
phase is most careful about. Each class therefore accepts any arm it can
meaningfully represent and ignores the rest.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.device_tracker import TrackerEntity
from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.restore_state import RestoreEntity

from ..coordinator import SignalSample, signal_dispatcher_topic
from ..values import value_as_bool, value_as_float, value_as_string
from .naming import generic_unique_id, humanise
from .routing import BOOLEAN_ARM, NUMERIC_ARMS

_LOGGER = logging.getLogger(__name__)

# Enum members that mean "no reading", never displayed and never an option.
_NULL_MEMBERS = ("unknown", "sna", "invalid")


def _member_name(value: Any) -> str | None:
    """The proto enum member name for this datum, or None."""
    arm = value.WhichOneof("value")
    if arm is None:
        return None
    field = value.DESCRIPTOR.fields_by_name.get(arm)
    if field is None or field.enum_type is None:
        return None
    member = field.enum_type.values_by_number.get(int(getattr(value, arm)))
    return member.name if member else None


def _strip_prefix(enum_type: Any) -> str:
    import os.path

    names = [v.name for v in enum_type.values]
    return os.path.commonprefix(names) if len(names) > 1 else ""


def _label(member: str, enum_type: Any, labels: dict[str, str] | None) -> str | None:
    if labels and member in labels:
        return labels[member]
    short = member[len(_strip_prefix(enum_type)):] or member
    if short.lower() in _NULL_MEMBERS:
        return None
    from .naming import snake

    return snake(short)


def enum_options(value: Any, meta: Any) -> list[str] | None:
    """Every state this enum can report, for SensorDeviceClass.ENUM.

    Read from the live protobuf descriptor rather than from metadata, so a
    proto bump cannot put the options out of step with the bindings actually
    decoding the stream.
    """
    arm = value.WhichOneof("value")
    if arm is None:
        return None
    field = value.DESCRIPTOR.fields_by_name.get(arm)
    if field is None or field.enum_type is None:
        return None
    labels = getattr(meta, "enum_labels", None)
    out = []
    for member in field.enum_type.values:
        label = _label(member.name, field.enum_type, labels)
        if label is not None and label not in out:
            out.append(label)
    return out


class _GenericEntity:
    """Marker: lets tests tell generic entities from curated ones."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, coordinator: Any, signal: str, meta: Any) -> None:
        self._coordinator = coordinator
        self._signal_name = signal
        self._meta = meta
        self._attr_device_info = coordinator.device_info
        self._attr_unique_id = generic_unique_id(coordinator.vin, signal)
        self._attr_name = humanise(signal)

    async def async_added_to_hass(self) -> None:
        sample = self._coordinator.get(self._signal_name)
        if sample is not None:
            self._handle(sample)
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_dispatcher_topic(self._coordinator.vin, self._signal_name),
                self._on_sample,
            )
        )

    @callback
    def _on_sample(self, sample: SignalSample) -> None:
        self._handle(sample)
        if self.hass is not None:
            self.async_write_ha_state()

    def _handle(self, sample: SignalSample) -> None:
        raise NotImplementedError


class GenericSensor(_GenericEntity, RestoreSensor):
    """Numeric, enum or string signal.

    RestoreSensor, not RestoreEntity: it supplies SensorEntity as well, and
    without SensorEntity this is not a valid sensor at all.
    """

    def __init__(self, coordinator: Any, signal: str, meta: Any) -> None:
        super().__init__(coordinator, signal, meta)
        self._attr_native_unit_of_measurement = meta.unit
        if meta.device_class:
            try:
                self._attr_device_class = SensorDeviceClass(meta.device_class)
            except ValueError:
                _LOGGER.debug("unknown device_class %r for %s", meta.device_class, signal)
        if meta.state_class:
            try:
                self._attr_state_class = SensorStateClass(meta.state_class)
            except ValueError:
                _LOGGER.debug("unknown state_class %r for %s", meta.state_class, signal)

    # native_value, options and available come from SensorEntity, which
    # already reads the matching _attr_ fields.

    def _handle(self, sample: SignalSample) -> None:
        value = sample.value
        if value.HasField("invalid"):
            self._attr_available = False
            return
        self._attr_available = True
        arm = value.WhichOneof("value")
        if arm is None:
            return
        member = _member_name(value)
        if member is not None:
            field = value.DESCRIPTOR.fields_by_name[arm]
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_state_class = None
            self._attr_options = enum_options(value, self._meta)
            self._attr_native_value = _label(
                member, field.enum_type, getattr(self._meta, "enum_labels", None)
            )
            return
        if arm in NUMERIC_ARMS:
            self._attr_native_value = value_as_float(value)
            return
        self._attr_native_value = value_as_string(value)


class GenericBinarySensor(_GenericEntity, BinarySensorEntity, RestoreEntity):
    """Boolean signal."""

    def __init__(self, coordinator: Any, signal: str, meta: Any) -> None:
        super().__init__(coordinator, signal, meta)
        if meta.device_class:
            try:
                self._attr_device_class = BinarySensorDeviceClass(meta.device_class)
            except ValueError:
                _LOGGER.debug("unknown device_class %r for %s", meta.device_class, signal)

    # is_on and available come from BinarySensorEntity.

    def _handle(self, sample: SignalSample) -> None:
        value = sample.value
        if value.HasField("invalid"):
            self._attr_available = False
            return
        self._attr_available = True
        if value.WhichOneof("value") == BOOLEAN_ARM:
            self._attr_is_on = value_as_bool(value)
            return
        member = _member_name(value)
        if member is not None:
            short = member[len(_strip_prefix(
                value.DESCRIPTOR.fields_by_name[value.WhichOneof("value")].enum_type
            )):].lower()
            if short in ("on", "true", "active", "enabled"):
                self._attr_is_on = True
            elif short in ("off", "false", "inactive", "disabled"):
                self._attr_is_on = False
            return
        # An arm this entity cannot represent. Keep the last value: flapping
        # to unavailable on a type change is worse than a stale reading.
        _LOGGER.debug(
            "%s: ignoring datum in arm %s, entity is a binary_sensor",
            self._signal_name,
            value.WhichOneof("value"),
        )


class GenericTracker(_GenericEntity, TrackerEntity, RestoreEntity):
    """Location signal. Kept minimal: latitude/longitude only."""

    _attr_latitude: float | None = None
    _attr_longitude: float | None = None

    # latitude and longitude come from TrackerEntity.

    def _handle(self, sample: SignalSample) -> None:
        value = sample.value
        if value.HasField("invalid"):
            self._attr_available = False
            return
        self._attr_available = True
        if value.WhichOneof("value") == "location_value":
            self._attr_latitude = value.location_value.latitude
            self._attr_longitude = value.location_value.longitude
```

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/generic/test_entities.py -v`
Expected: PASS (7 tests)

- [ ] **Step 7: Run the whole suite and the linter**

Run: `pytest -q && ruff check .`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add custom_components/tesla_telemetry/generic/entities.py \
        tests/generic/test_entities.py signal_catalog/overrides.py \
        scripts/signal_metadata/ custom_components/tesla_telemetry/signal_metadata.py
git commit -m "Add generic entity classes and enum label overrides"
```

---

### Task 5: The factory

**Files:**
- Create: `custom_components/tesla_telemetry/generic/factory.py`
- Modify: `custom_components/tesla_telemetry/coordinator.py` (first-sight notification)
- Test: `tests/generic/test_factory.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `GenericEntityFactory(hass, entry, coordinator)` with `register_platform(domain, add_entities)`, `async_restore_known()`, `handle_sample(signal, sample)`; `ALL_SIGNAL_TOPIC` on the coordinator.

- [ ] **Step 1: Write the failing test**

```python
# tests/generic/test_factory.py
"""First-sight entity creation.

The factory is the only thing that turns an arriving datum into an entity, so
its skip rules matter as much as its create rules: a claimed signal must not
get a duplicate, an unknown signal must not raise inside the dispatcher, and
a burst of data for one new signal must produce exactly one entity.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pb = pytest.importorskip("custom_components.tesla_telemetry.proto.vehicle_data_pb2")

from custom_components.tesla_telemetry.generic.factory import (  # noqa: E402
    GenericEntityFactory,
)

VIN = "TESTVIN0000000001"


def _sample(value):
    return SimpleNamespace(value=value, received_at=0.0, payload_created_at=None)


def _factory():
    coordinator = SimpleNamespace(vin=VIN, device_info={}, get=lambda n: None)
    created: dict[str, list] = {"sensor": [], "binary_sensor": [], "device_tracker": []}
    factory = GenericEntityFactory(
        hass=SimpleNamespace(), entry=SimpleNamespace(entry_id="e"), coordinator=coordinator
    )
    for domain in created:
        factory.register_platform(domain, lambda es, d=domain: created[d].extend(es))
    return factory, created


def test_creates_a_sensor_for_a_numeric_signal() -> None:
    factory, created = _factory()
    factory.handle_sample("VehicleSpeed", _sample(pb.Value(double_value=10.0)))
    assert len(created["sensor"]) == 1
    assert created["sensor"][0].unique_id == f"{VIN}_vehicle_speed_telemetry"


def test_creates_a_binary_sensor_for_a_boolean_signal() -> None:
    factory, created = _factory()
    factory.handle_sample("Locked", _sample(pb.Value(boolean_value=True)))
    assert len(created["binary_sensor"]) == 1


def test_a_claimed_signal_gets_no_generic_entity() -> None:
    """Otherwise the user sees two entities for the same data."""
    from custom_components.tesla_telemetry.generic.claimed import CLAIMED_SIGNALS

    claimed = next(iter(CLAIMED_SIGNALS))
    factory, created = _factory()
    factory.handle_sample(claimed, _sample(pb.Value(double_value=1.0)))
    assert not any(created.values())


def test_repeated_data_create_one_entity() -> None:
    """Review Focus 5: a burst before the first entity is added."""
    factory, created = _factory()
    for _ in range(5):
        factory.handle_sample("VehicleSpeed", _sample(pb.Value(double_value=10.0)))
    assert len(created["sensor"]) == 1


def test_a_signal_absent_from_the_catalog_is_ignored() -> None:
    """Review Focus 4: proto newer than metadata. Must not raise inside the
    dispatcher, which would break every other subscriber on that callback."""
    factory, created = _factory()
    factory.handle_sample("NotARealSignal", _sample(pb.Value(double_value=1.0)))
    assert not any(created.values())


def test_an_invalid_first_datum_creates_nothing() -> None:
    factory, created = _factory()
    factory.handle_sample("VehicleSpeed", _sample(pb.Value(invalid=True)))
    assert not any(created.values())
    factory.handle_sample("VehicleSpeed", _sample(pb.Value(double_value=5.0)))
    assert len(created["sensor"]) == 1


async def test_restore_recreates_a_known_entity(hass) -> None:
    """Without this, a slow signal has no entity until it next changes.

    Odometer can sit unchanged for hours; a user restarting Home Assistant
    would find the entity simply missing rather than showing its last value.
    """
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.tesla_telemetry.const import DOMAIN
    from custom_components.tesla_telemetry.generic.naming import generic_unique_id

    entry = MockConfigEntry(domain=DOMAIN, unique_id=VIN, data={"vin": VIN}, version=3)
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor", DOMAIN, generic_unique_id(VIN, "Odometer"), config_entry=entry
    )

    coordinator = SimpleNamespace(vin=VIN, device_info={}, get=lambda n: None)
    created: list = []
    factory = GenericEntityFactory(hass=hass, entry=entry, coordinator=coordinator)
    factory.register_platform("sensor", created.extend)

    factory.async_restore_known(registry)

    assert [e.unique_id for e in created] == [generic_unique_id(VIN, "Odometer")]


async def test_restore_does_not_duplicate_an_already_created_entity(hass) -> None:
    """Restore runs at setup and data can arrive immediately after."""
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.tesla_telemetry.const import DOMAIN
    from custom_components.tesla_telemetry.generic.naming import generic_unique_id

    entry = MockConfigEntry(domain=DOMAIN, unique_id=VIN, data={"vin": VIN}, version=3)
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor", DOMAIN, generic_unique_id(VIN, "Odometer"), config_entry=entry
    )

    coordinator = SimpleNamespace(vin=VIN, device_info={}, get=lambda n: None)
    created: list = []
    factory = GenericEntityFactory(hass=hass, entry=entry, coordinator=coordinator)
    factory.register_platform("sensor", created.extend)

    factory.async_restore_known(registry)
    factory.handle_sample("Odometer", _sample(pb.Value(double_value=1234.0)))

    assert len(created) == 1


def test_a_platform_that_never_registered_drops_its_entities() -> None:
    """Platforms register independently; a datum arriving before one has set
    up must not raise."""
    coordinator = SimpleNamespace(vin=VIN, device_info={}, get=lambda n: None)
    factory = GenericEntityFactory(
        hass=SimpleNamespace(), entry=SimpleNamespace(entry_id="e"), coordinator=coordinator
    )
    factory.handle_sample("VehicleSpeed", _sample(pb.Value(double_value=1.0)))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/generic/test_factory.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named '...generic.factory'`

- [ ] **Step 3: Add the first-sight notification to the coordinator**

`coordinator.py` — add near `signal_dispatcher_topic`:

```python
def all_signals_topic(vin: str) -> str:
    """Dispatcher topic carrying every sample, for the generic factory."""
    return f"{DOMAIN}.{vin}.*"
```

and in `async_publish`, after the existing per-signal dispatch:

```python
        async_dispatcher_send(
            self.hass, all_signals_topic(self.vin), name, sample
        )
```

- [ ] **Step 4: Write the factory**

```python
# custom_components/tesla_telemetry/generic/factory.py
"""Create entities for signals with no hand-written entity.

Home Assistant fixes platforms at setup time, but the entity type is only
known when a datum arrives. Each platform therefore registers its
add-entities callback here, and this routes on first sight.
"""
from __future__ import annotations

import logging
from typing import Any

from ..signal_metadata import SIGNALS
from .claimed import CLAIMED_SIGNALS
from .entities import GenericBinarySensor, GenericSensor, GenericTracker
from .naming import generic_unique_id
from .routing import BINARY_SENSOR, DEVICE_TRACKER, SENSOR, platform_for

_LOGGER = logging.getLogger(__name__)

_CLASSES = {
    SENSOR: GenericSensor,
    BINARY_SENSOR: GenericBinarySensor,
    DEVICE_TRACKER: GenericTracker,
}


class GenericEntityFactory:
    """One per config entry. Owns every generic entity for that vehicle."""

    def __init__(self, hass: Any, entry: Any, coordinator: Any) -> None:
        self._hass = hass
        self._entry = entry
        self._coordinator = coordinator
        self._add_entities: dict[str, Any] = {}
        self._created: set[str] = set()

    def register_platform(self, domain: str, add_entities: Any) -> None:
        self._add_entities[domain] = add_entities

    def handle_sample(self, signal: str, sample: Any) -> None:
        """Create the entity for `signal` if this is the first usable datum."""
        if signal in self._created or signal in CLAIMED_SIGNALS:
            return
        meta = SIGNALS.get(signal)
        if meta is None:
            # The vendored proto is ahead of the metadata table. Ignore rather
            # than raise: this runs inside a dispatcher callback, and an
            # exception here would break every other subscriber on it.
            _LOGGER.debug("no metadata for signal %s; no generic entity", signal)
            return
        domain = platform_for(sample.value)
        if domain is None:
            return          # invalid or empty: wait for a real datum
        add_entities = self._add_entities.get(domain)
        if add_entities is None:
            _LOGGER.debug("platform %s not set up yet; dropping %s", domain, signal)
            return
        # Mark before adding: async_add_entities can re-enter, and a burst of
        # data for one new signal must not produce two entities.
        self._created.add(signal)
        add_entities([_CLASSES[domain](self._coordinator, signal, meta)])

    def async_restore_known(self, registry: Any) -> None:
        """Recreate entities already in the registry, before any datum.

        Without this, a slow signal such as odometer would have no entity
        until it next changed, which can be hours.
        """
        from homeassistant.helpers import entity_registry as er

        known = {
            entry.unique_id: entry
            for entry in er.async_entries_for_config_entry(
                registry, self._entry.entry_id
            )
        }
        for signal in SIGNALS:
            if signal in CLAIMED_SIGNALS or signal in self._created:
                continue
            entry = known.get(generic_unique_id(self._coordinator.vin, signal))
            if entry is None:
                continue
            add_entities = self._add_entities.get(entry.domain)
            cls = _CLASSES.get(entry.domain)
            if add_entities is None or cls is None:
                continue
            self._created.add(signal)
            add_entities([cls(self._coordinator, signal, SIGNALS[signal])])
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/generic/test_factory.py -v`
Expected: PASS (9 tests)

- [ ] **Step 6: Commit**

```bash
git add custom_components/tesla_telemetry/generic/factory.py \
        custom_components/tesla_telemetry/coordinator.py tests/generic/test_factory.py
git commit -m "Add the generic entity factory with first-sight creation"
```

---

### Task 6: The migration

**Files:**
- Create: `custom_components/tesla_telemetry/migration.py`
- Modify: `custom_components/tesla_telemetry/config_flow.py` (`VERSION = 3`)
- Modify: `custom_components/tesla_telemetry/__init__.py` (`async_migrate_entry`)
- Test: `tests/generic/test_migration.py`

**Interfaces:**
- Consumes: the fixture from Task 1, `naming.snake`.
- Produces: `LEGACY_UNIQUE_IDS: dict[str, str]`, `async_migrate_unique_ids(hass, entry) -> int`.

- [ ] **Step 1: Generate the map from the fixture**

```bash
python3 - <<'PY'
import json, re
from pathlib import Path
data = json.loads(Path("tests/fixtures/legacy_unique_ids.json").read_text())
from custom_components.tesla_telemetry.generic.claimed import CLAIMED_SIGNALS
import sys; sys.path.insert(0, ".")
from custom_components.tesla_telemetry.generic.naming import snake

for suffix, v in sorted(data.items()):
    sig = v["signal"]
    if not sig or sig in CLAIMED_SIGNALS:
        continue
    new = f"{snake(sig)}_telemetry"
    if new != suffix:
        print(f'    "{suffix}": "{new}",   # {sig}')
PY
```

Paste the output into `LEGACY_UNIQUE_IDS` in Step 3.

- [ ] **Step 2: Write the failing test**

```python
# tests/generic/test_migration.py
"""The v2 -> v3 unique_id migration.

Half the hand-written entities were named editorially rather than
mechanically, so a uniform generic rule renames them. Renaming a unique_id
orphans that entity's recorder history, so the registry is rewritten once
instead.

async_migrate_entry must never raise: an exception there fails the whole
config entry, leaving a user with a dead integration rather than a few stale
names.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tesla_telemetry.const import DOMAIN
from custom_components.tesla_telemetry.generic.naming import snake
from custom_components.tesla_telemetry.migration import (
    LEGACY_UNIQUE_IDS,
    async_migrate_unique_ids,
)

VIN = "TESTVIN0000000001"
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/legacy_unique_ids.json"


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=VIN, data={"vin": VIN}, version=2)
    entry.add_to_hass(hass)
    return entry


def test_every_target_matches_the_naming_rule() -> None:
    """The map cannot drift from snake(signal) + '_telemetry'."""
    data = json.loads(FIXTURE.read_text())
    for old, new in LEGACY_UNIQUE_IDS.items():
        signal = data[old]["signal"]
        assert new == f"{snake(signal)}_telemetry", f"{old}: {new} is not the rule"


def test_no_claimed_signal_is_migrated() -> None:
    from custom_components.tesla_telemetry.generic.claimed import CLAIMED_SIGNALS

    data = json.loads(FIXTURE.read_text())
    for old in LEGACY_UNIQUE_IDS:
        assert data[old]["signal"] not in CLAIMED_SIGNALS


async def test_renames_a_legacy_entity(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    registry = er.async_get(hass)
    old, new = next(iter(LEGACY_UNIQUE_IDS.items()))
    created = registry.async_get_or_create(
        "sensor", DOMAIN, f"{VIN}_{old}", config_entry=entry
    )

    assert await async_migrate_unique_ids(hass, entry) >= 1

    assert registry.async_get(created.entity_id).unique_id == f"{VIN}_{new}"


async def test_is_idempotent(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    registry = er.async_get(hass)
    old, new = next(iter(LEGACY_UNIQUE_IDS.items()))
    registry.async_get_or_create("sensor", DOMAIN, f"{VIN}_{old}", config_entry=entry)

    first = await async_migrate_unique_ids(hass, entry)
    second = await async_migrate_unique_ids(hass, entry)
    assert first >= 1
    assert second == 0


async def test_skips_when_the_target_already_exists(hass: HomeAssistant) -> None:
    """Review Focus 3: async_update_entity raises on a used unique_id, and
    that exception would fail the whole config entry."""
    entry = _entry(hass)
    registry = er.async_get(hass)
    old, new = next(iter(LEGACY_UNIQUE_IDS.items()))
    registry.async_get_or_create("sensor", DOMAIN, f"{VIN}_{old}", config_entry=entry)
    registry.async_get_or_create("sensor", DOMAIN, f"{VIN}_{new}", config_entry=entry)

    migrated = await async_migrate_unique_ids(hass, entry)   # must not raise

    assert migrated == 0
    assert registry.async_get_entity_id("sensor", DOMAIN, f"{VIN}_{old}") is not None


async def test_an_empty_registry_is_fine(hass: HomeAssistant) -> None:
    entry = _entry(hass)
    assert await async_migrate_unique_ids(hass, entry) == 0
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/generic/test_migration.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named '...migration'`

- [ ] **Step 4: Write the migration**

```python
# custom_components/tesla_telemetry/migration.py
"""One-time unique_id migration, v2 -> v3.

Half the hand-written entities were named editorially rather than
mechanically — abbreviations in both directions, semantic renames, dropped
suffixes — so the uniform generic rule renames them. Renaming a unique_id
orphans that entity's recorder history, so the registry is rewritten once
here instead of aliasing the old names forever.

This map is migration data. Nothing reads it at runtime, and once every entry
has migrated it can be deleted.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.helpers import entity_registry as er

from .const import CONF_VIN, DOMAIN

_LOGGER = logging.getLogger(__name__)

# old unique_id suffix -> new unique_id suffix
LEGACY_UNIQUE_IDS: dict[str, str] = {
    # ... generated in Step 1, one line per rename with its signal name ...
}


async def async_migrate_unique_ids(hass: Any, entry: Any) -> int:
    """Rewrite legacy unique_ids for this entry. Returns how many changed.

    Never raises. async_update_entity raises when the target unique_id is in
    use, and an exception escaping a migration fails the whole config entry —
    a dead integration is far worse than a few stale entity names.
    """
    registry = er.async_get(hass)
    vin = entry.data.get(CONF_VIN)
    if not vin:
        return 0

    migrated = 0
    for old_suffix, new_suffix in LEGACY_UNIQUE_IDS.items():
        old = f"{vin}_{old_suffix}"
        new = f"{vin}_{new_suffix}"
        for domain in ("sensor", "binary_sensor", "device_tracker"):
            entity_id = registry.async_get_entity_id(domain, DOMAIN, old)
            if entity_id is None:
                continue
            if registry.async_get_entity_id(domain, DOMAIN, new) is not None:
                _LOGGER.debug("%s already exists; leaving %s alone", new, old)
                continue
            try:
                registry.async_update_entity(entity_id, new_unique_id=new)
            except ValueError as err:
                _LOGGER.warning("could not migrate %s to %s: %s", old, new, err)
                continue
            migrated += 1
    if migrated:
        _LOGGER.info(
            "tesla_telemetry: migrated %d entity unique_id(s) for vin=%s", migrated, vin
        )
    return migrated
```

- [ ] **Step 5: Wire it into `__init__.py`**

```python
async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate an older config entry.

    Runs before async_setup_entry, so the unique_id rewrite always completes
    before a generic entity could claim one of the target ids.
    """
    if entry.version < 3:
        await async_migrate_unique_ids(hass, entry)
        hass.config_entries.async_update_entry(entry, version=3)
    return True
```

and set `VERSION = 3` in `config_flow.py`.

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/generic/test_migration.py -v`
Expected: PASS (6 tests)

- [ ] **Step 7: Commit**

```bash
git add custom_components/tesla_telemetry/migration.py \
        custom_components/tesla_telemetry/__init__.py \
        custom_components/tesla_telemetry/config_flow.py tests/generic/test_migration.py
git commit -m "Migrate legacy entity unique_ids to the generic naming rule"
```

---

### Task 7: Wire the platforms and remove `local_extras.py`

**Files:**
- Modify: `sensor.py`, `binary_sensor.py`, `device_tracker.py` (register with the factory)
- Modify: `__init__.py` (create the factory, subscribe it, restore)
- Modify: `const.py` (drop the `local_extras` import block)
- Delete: `custom_components/tesla_telemetry/local_extras.py`
- Modify: `values.py` (absorb the helpers local_extras owned)
- Test: `tests/generic/test_no_orphans.py`

**Interfaces:**
- Consumes: everything above.
- Produces: nothing new.

- [ ] **Step 1: Write the failing test — the one that proves nobody loses history**

```python
# tests/generic/test_no_orphans.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/generic/test_no_orphans.py -v`
Expected: FAIL on `test_local_extras_is_gone` (the file still exists). `test_no_entity_is_orphaned` should already pass — if it does not, `LEGACY_UNIQUE_IDS` is incomplete; fix that before continuing.

- [ ] **Step 3: Move the helpers local_extras owns into `values.py`**

Move `value_as_short_enum`, `value_as_clock_time`, `_number_or_enum`, `_hvac_running`, `_sentry_armed` and `_enum_prefix` from `local_extras.py` into `values.py`, keeping their docstrings — several record behaviour decided in Phase 1 (an Unknown HVAC or sentry enum reports `off`, not `unknown`). Add them to `__all__`.

Run: `pytest tests/test_local_extras_values.py -q` after updating its imports.
Expected: PASS — the same tests, now against `values.py`.

- [ ] **Step 4: Register each platform with the factory**

At the end of each platform's `async_setup_entry`:

```python
    factory = hass.data[DOMAIN][entry.entry_id]["generic_factory"]
    factory.register_platform("sensor", async_add_entities)   # or the platform's own domain
```

and delete the `local_extras` import and its `async_add_entities(...)` call.

- [ ] **Step 5: Create and subscribe the factory in `__init__.py`**

In `async_setup_entry`, before forwarding platforms:

```python
    factory = GenericEntityFactory(hass, entry, coordinator)
    domain_data[entry.entry_id]["generic_factory"] = factory
```

and after `async_forward_entry_setups`:

```python
    factory.async_restore_known(er.async_get(hass))
    entry.async_on_unload(
        async_dispatcher_connect(
            hass, all_signals_topic(vin), factory.handle_sample
        )
    )
```

- [ ] **Step 6: Remove `local_extras.py` and its hook in `const.py`**

Delete the file, and delete the `from .local_extras import ...` block plus the
`DEFAULT_INTERVALS_SECONDS.update(...)` / `SIGNAL_CATEGORIES[...].extend(...)`
loop that followed it.

**`DEFAULT_INTERVALS_SECONDS` must keep all 81 entries.** Those signals were
chosen deliberately in Phase 1 and are unrelated to how entities are built —
inline them into `const.py` rather than dropping them.

Run: `python3 -c "import sys; sys.path.insert(0,'.'); from custom_components.tesla_telemetry.const import DEFAULT_INTERVALS_SECONDS as D; print(len(D))"`
Expected: `81`

- [ ] **Step 7: Document the downgrade risk in the README**

Add to the Troubleshooting section:

```markdown
* **Duplicate entities after downgrading.** This version renames some entity
  unique_ids once, so that entities created before it keep their history under
  the new naming. Downgrading afterwards recreates the old ids alongside the
  new ones, giving you two of some entities. Delete the stale ones from
  **Settings → Devices & Services → Entities**, or upgrade again.
```

- [ ] **Step 8: Run everything**

Run: `pytest -q && ruff check . && python3 scripts/gen_signal_metadata.py --check`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "Route uncurated signals through the generic factory; drop local_extras"
```

---

## Final verification

- [ ] `pytest` — all pass
- [ ] `ruff check .` — All checks passed
- [ ] `python3 scripts/gen_signal_metadata.py --check` — exit 0
- [ ] `DEFAULT_INTERVALS_SECONDS` still has 81 entries
- [ ] `local_extras.py` is gone and nothing imports it: `grep -rn local_extras custom_components/ tests/` is empty
- [ ] The integration imports: `python3 -c "import custom_components.tesla_telemetry.generic.factory"`
- [ ] `tests/fixtures/legacy_unique_ids.json` is committed and unmodified since Task 1: `git log --oneline -- tests/fixtures/legacy_unique_ids.json` shows exactly one commit
