# Signal metadata + catalog automation (Phase 2)

Status: approved, not yet implemented
Date: 2026-09-24

## Goal

Produce a single generated table describing every signal in Tesla's telemetry
catalog — id, category, type, enum name, unit, device/state class, firmware
floor — so that Phase 3 can create entities for the whole catalog without
hand-written code per signal, and Phase 4 can group and price them in the
options UI.

Keep it current with an automation that opens a pull request in **this fork**
when Tesla's published catalog changes.

## Non-goals

- Regenerating `vehicle_data_pb2.py`. Proto bumps stay manual: they are rare,
  they change entity behaviour, and they need the pinned protoc 25.3 plus a
  compatibility smoke-test across `protobuf>=4.25,<8`. The automation
  *detects* proto drift and reports it; it does not act on it.
- Creating entities. That is Phase 3. This phase produces data only.
- Inferring units from prose. See "Units".
- Touching `const.py`. Its hand-curated `SIGNAL_CATEGORIES` (7 groups, driving
  today's options flow) and `DEFAULT_INTERVALS_SECONDS` stay exactly as they
  are. The generated `category` comes from Tesla's own taxonomy (11 groups) and
  is additional data; reconciling the two belongs to Phase 4, where the options
  UI is rebuilt. Nothing in this phase changes runtime behaviour — after it
  lands, the integration behaves identically and simply has a table available.

## Decisions

### The metadata is a generated Python module, not a runtime JSON read

Evidence, gathered 2026-09-24:

- This integration ships **no** runtime-read data files. Every static table is
  Python (`const.py`, `values.py`). The only non-`.py` files are
  framework-required ones plus the vendored `.proto` schemas, which are not
  read at runtime — the imported artifact is the generated `vehicle_data_pb2.py`.
- Across all bundled Home Assistant integrations there are **zero** uses of
  `importlib.resources` or `pkgutil.get_data`. The 684 `.json` files are all
  `icons.json`, which the *framework* loads. (Core itself does read
  `generated/integrations.json` in `loader.py`, so this is a convention for
  integrations, not a prohibition.)
- `teslemetry` — the hand-maintained equivalent of what we are generating —
  ships no data files at all. Its signal metadata is a ~2000-line `sensor.py`
  of `EntityDescription` tuples.

`homeassistant/generated/` is the precedent to copy: committed generated
Python, flat data, with a fixed header naming the regeneration command.

### Build-time inputs are not shipped to users

The vendored docs snapshot is a build-time input; nothing at runtime reads it.
Shipping it would add ~56 KB to every HACS install and invite edits to a
generated-from file. The `.proto` files are inside the package for a specific
reason — someone debugging the wire format needs them beside the bindings, as
`proto/README.md` explains — and a docs snapshot has no equivalent claim.

Principle: **ship what runtime needs; keep build-time artifacts out of the
package.**

### The generator has no heuristics

Units come only from the hand-maintained override table, never from parsing
descriptions. `DCChargingPower`'s description mentions both watts and
kilowatts; if the generator inferred from text, a Tesla copy-edit could
silently change a unit and with it the meaning of every recorded value. The
44 descriptions that do state a unit are a one-time research aid for writing
the overrides by hand, not a runtime input.

This leaves the generator a deterministic merge of three inputs.

## Layout

```
signal_catalog/                  # build-time only, NOT shipped
  tesla_fields.json              #   vendored upstream snapshot — the reviewable diff
  overrides.py                   #   hand-maintained units / device / state classes
  README.md                      #   provenance + regeneration, mirroring proto/README.md
scripts/
  gen_signal_metadata.py         # the generator: no HA, no protobuf runtime
custom_components/tesla_telemetry/
  signal_metadata.py             # GENERATED, shipped, imported at runtime
```

`gen_signal_metadata.py` parses the `.proto` as text rather than importing the
generated bindings, so it runs in a bare CI job and is testable without Home
Assistant or protobuf installed.

`signal_catalog/` is an ordinary package with an `__init__.py`, imported from
the repo root. `pytest.ini` already puts the root on `sys.path` via
`pythonpath = .` (added in Phase 1), so tests and the generator resolve it the
same way.

## Data sources

### 1. The vendored proto

`proto/schemas/vehicle_data.proto`, `enum Field`. Authoritative for a signal's
**existence and id** — a name absent from this enum can never arrive on the
wire. 269 values today, excluding `Unknown`.

Two comment styles carry firmware floors, and both must be parsed:

- range blocks — `// fields 260-269 are first available in firmware version 2026.32`
  (7 of them, covering 91 fields)
- per-field trailing — `// Requires firmware version 2024.26 or later` (7 fields)

A third trailing form, `// Semi-truck only` (13 fields), marks signals no
consumer vehicle reports.

### 2. Tesla's published catalog

The Available Data page is Gatsby-rendered; `VehicleSpeed` does not appear in
its HTML. The data is reachable as structured JSON:

1. `GET /docs/page-data/fleet-api/fleet-telemetry/available-data/page-data.json`
2. read `staticQueryHashes`
3. `GET /docs/page-data/sq/d/<hash>.json` for each, and take the one containing
   `data.allFleetStreamingFieldsCsv.nodes`

Records look like:

```json
{"category": "Driving", "type": "real", "field_name": "VehicleSpeed",
 "proto_enum_name": "", "description": "The speed of the vehicle is miles per hour.",
 "vehicle_data_equivalent": "drive_state.speed"}
```

239 entries; 11 categories; `type` in
{real, boolean, enum, integer, string, Location, time, timestamp};
`proto_enum_name` populated for 46 enum signals.

**The hash changes whenever Tesla rebuilds the site, so it must always be
discovered and never pinned.** The discovery chain is the fragile part of this
design and is the thing most likely to need maintenance.

### 3. `signal_catalog/overrides.py`

Hand-maintained. Units and device/state classes as **plain strings**
(`"kWh"`, `"energy"`, `"measurement"`), not Home Assistant enum members, so the
generator stays free of HA imports. Phase 3 maps those strings to
`UnitOfEnergy.KILO_WATT_HOUR` and friends where HA is already imported.

Of 116 numeric signals: 81 have a unit and device class in `teslemetry`
(Apache-2.0 — each such entry carries an attribution comment), 9 more state a
unit in their description, and the remaining 26 are genuinely dimensionless
(`ChargerPhases`, `SeatHeaterLeft`, `MediaAudioVolume`, `NumBrickVoltageMax`).
For those, no unit is the correct answer rather than a gap.

## The generated module

```python
"""Automatically generated file.

To update, run python3 scripts/gen_signal_metadata.py
"""

@dataclass(frozen=True, slots=True)
class SignalMeta:
    field_id: int
    category: str | None
    value_type: str | None      # real|boolean|enum|integer|string|Location|time|timestamp
    enum_name: str | None
    unit: str | None
    device_class: str | None
    state_class: str | None
    min_firmware: str | None    # "2026.32"
    semi_only: bool
    documented: bool

SIGNALS: dict[str, SignalMeta] = { ... }
```

Sorted by `field_id`, so Tesla's additions append at the end and diffs stay
minimal.

`value_type` is a **hint, not authority**. Phase 3 determines the entity type
from the `Value` oneof arm the vehicle actually sends, per the roadmap. The
documented type drives the options UI and sanity-checks overrides.

`enum_name` is the enum's *name* only. The member list is deliberately not
baked in: Phase 3 reads it from the live protobuf descriptor, which
`local_extras.value_as_short_enum` already does via
`field.enum_type.values_by_number`. Copying the members into generated
metadata would create a second source of truth that a proto bump could put out
of step with the bindings actually decoding the stream.

## Reconciliation rules

| Case | Count on 2026-09-24 | Rule |
|---|---|---|
| in proto and in docs | 239 | merge; `documented=True` |
| proto only, `Deprecated_*` / `Experimental_*` / `Unknown` | 18 | excluded — placeholders, not signals |
| proto only, real signal | 12 | emitted; `documented=False`, `category=None`, `value_type=None` |
| **docs only** | 0 | excluded, and reported prominently — the vendored proto is behind |
| override naming a signal in neither | — | hard error |

The proto-only case is not hypothetical: five signals already shipped as
defaults have no docs entry — `LifetimeEnergyChargedKwh`,
`LifetimeEnergyGainedRegen`, `NominalFullPackEnergyKwh`, `RemoteStartActive`,
`ScheduledDepartureTime`. "Known to the proto but undocumented" is a normal
state, not an error.

The docs-only case is the proto-drift detector. A signal Tesla documents but
our proto lacks means the vendored proto needs bumping; the refresh PR says so
in its body without touching the bindings.

### Fail-closed

The generator exits non-zero and writes nothing when:

- discovery fails at any step, or no static query contains the node
- the snapshot parses but holds implausibly few signals (< 200)
- an override names a signal in neither source
- an override contradicts a documented type (e.g. a unit on a `boolean`)

A failed fetch must never render as "Tesla deleted 239 signals".

## Automation

Two mechanisms, answering different questions.

### Drift check, on every pull request

Hermetic, no network. Runs the generator against the committed inputs in
`--check` mode and fails if `signal_metadata.py` differs. Catches a hand-edited
generated file, or an `overrides.py` change without regeneration. Same
guarantee HA core gets from hassfest validating its `generated/` tree.

### Weekly refresh, scheduled

The only thing that touches the network. Fetches via discovery, regenerates,
and when the snapshot or the generated module changed, opens a pull request on
a **fixed branch** (`chore/signal-catalog-refresh`) so it updates in place
rather than accumulating one PR a week.

Two deliberate details:

- **`gh pr create --repo ${{ github.repository }}`, always explicit.** In a
  fork `gh` defaults to the *parent*. This is the one part of the design that
  could violate the never-touch-upstream ground rule, so it is pinned
  structurally rather than by care.
- **The workflow runs the test suite itself and reports the result in the PR
  body.** A pull request opened with `GITHUB_TOKEN` does not trigger other
  workflows — GitHub's anti-recursion rule — so the PR would otherwise arrive
  with no checks. Better to state that in the body than to look green by
  omission.

No third-party actions; `gh` is preinstalled on runners. Permissions:
`contents: write`, `pull-requests: write`.

## Testing

All offline, against small fixtures — a trimmed proto and a trimmed snapshot.

- one test per reconciliation case, including the two that must fail (stale
  override, docs-only signal)
- both firmware comment styles, and `Semi-truck only`
- fail-closed paths: discovery failure, truncated snapshot, implausible count,
  contradictory override
- determinism: generating twice yields byte-identical output
- `--check` mode itself, which is what PR CI runs
- a unit-coverage test asserting every signal we ship as a default either has a
  unit or is on an explicit dimensionless list, so a new default cannot quietly
  ship unitless

The live discovery chain gets **one** test behind an opt-in marker, run only in
the scheduled job. Tesla's site must not be able to turn an unrelated pull
request red.

## What later phases consume

- **Phase 3** — `value_type` and `enum_name` as hints, `unit` / `device_class` /
  `state_class` for numeric sensors, `min_firmware` and `semi_only` to avoid
  creating entities a given car will never populate. This is the declarative
  answer to the problem hit in Phase 1, where three signals gated on firmware
  2026.32 were enabled by default.
- **Phase 4** — `category` for the options-flow sections, and the signal count
  for the cost estimate.

## Risks

- **The discovery chain is undocumented and unsupported.** Tesla could change
  its site framework and break it. Mitigation: fail closed and loudly; the
  vendored snapshot means a broken fetch never degrades the shipped metadata.
- **`teslemetry` attribution.** Apache-2.0 requires attribution; each derived
  override entry carries a comment, and `signal_catalog/README.md` states the
  provenance and licence.
- **A weekly PR is only useful if it gets read.** If it proves noisy, the
  schedule drops to monthly rather than the check being weakened.
