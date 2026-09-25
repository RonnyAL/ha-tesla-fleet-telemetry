# Generic entities for the whole signal catalog (Phase 3)

Status: approved, not yet implemented
Date: 2026-09-25

## Goal

Create entities for any signal a vehicle streams, without hand-written code
per signal. Today 62 signal-backed entities are declared by hand across `sensor.py`,
`binary_sensor.py`, `device_tracker.py` and `local_extras.py` (plus a few
accounting entities backed by no signal); the catalog has 251 signals. A generic path closes that gap and lets Phase 4 offer the whole
catalog in the options UI.

Remove `local_extras.py`, which the Phase 1 brief always described as a
stopgap.

## Non-goals

- The options UI, presets and cost estimation. That is Phase 4.
- Changing which signals are streamed by default. `DEFAULT_INTERVALS_SECONDS`
  is untouched; this phase changes how arriving data becomes entities, not
  what is requested from the car.
- Firmware gating at entity-creation time. See "What lazy creation subsumes".

## The two constraints that shape everything

Measured against the current code, not assumed:

**1. Half the existing entities would be renamed.** Of 62 hand-written
entities, **31 would get a different `unique_id`** under any uniform naming
rule, because the current names are editorial rather than mechanical:

- abbreviations in both directions — `HvacLeftTemperatureRequest` →
  `hvac_left_temp_request`, but `InsideTemp` → `inside_temperature`
- semantic renames — `SentryMode` → `sentry_armed`, `DoorState` → `lock`,
  `DriverSeatOccupied` → `user_present`, `MinutesToArrival` → `time_to_arrival`
- dropped suffixes — `GuestModeEnabled` → `guest_mode`,
  `LifetimeEnergyChargedKwh` → `lifetime_energy_charged`
- added prefixes — `ModuleTempMax` → `battery_module_temp_max`,
  `DiStatorTempF` → `motor_stator_temp_front`

No naming rule reproduces these. Changing a `unique_id` orphans that entity's
recorder history, which the project brief forbids.

**2. Genericizing an enum changes its reported state values.** `GearSensor`
reports `"P"`, `"R"`, `"N"`, `"D"`; the generic enum path produces `"p"`,
`"d"`. This is worse than a `unique_id` change: a migrated `unique_id`
preserves history, whereas a changed state value silently breaks dashboard
cards and any automation comparing `state == "P"`, and leaves a discontinuity
in the recorder that nothing can repair.

## Decisions

### Entity type comes from the arm Tesla sends, not from metadata

The metadata table says a signal *exists* and what Tesla *documents* it as. It
does not say this car sends it, or in which oneof arm. Only the arriving datum
knows that. Phase 1 demonstrated the cost of assuming otherwise: three signals
gated on firmware 2026.32 were enabled by default and would have produced
permanently-`unknown` entities on an older car.

Rejected alternative: pre-create entities for every configured signal using
metadata's `value_type`. Simpler, and wrong for exactly that reason.

### One uniform naming rule, reached by a one-time migration

`unique_id` is `{vin}_{snake(signal)}_telemetry` for every generic entity.
The 31 legacy names are rewritten once in `async_migrate_entry` rather than
aliased forever, so the convention stays uniform and new signals look like old
ones.

Rejected alternative: a permanent `{signal: historical_suffix}` alias table.
Nothing can go wrong at upgrade, but the naming is permanently inconsistent —
31 signals on a hand-made convention, 220 on the rule — and the table could
never be removed.

### Enum labels are an override, like units

Enum options default to the stripped, snake_cased member names. Where
`signal_catalog/overrides.py` supplies a label map, those values become both
the mapping and the `options` list, so Gear keeps `"P"`/`"R"`/`"N"`/`"D"` and
nothing user-visible changes. This reuses the mechanism Phase 2 established for
units rather than inventing a second one.

### Curated entities keep their signals

A signal claimed by a hand-written entity is excluded from the generic path
*and* from the migration. Three kinds stay hand-written:

- **fan-out** — one signal feeding several entities (`DoorState` → the per-door
  binary sensors; the window signals)
- **derived** — values no single signal carries (charging-active, the
  plugged-in cable veto, average battery temperature)
- **accounting** — not catalog signals at all (`SignalsReceived`,
  `EstimatedSignalCost`)

## Architecture

```
coordinator.async_publish(name, value)
        │  (existing per-signal dispatch, unchanged)
        └─► first-sight notification
                 │
        generic factory ──► signal in CLAIMED_SIGNALS? ──► ignore
                 │  no
                 ├─ boolean arm ──────► binary_sensor add-callback
                 ├─ location arm ─────► device_tracker add-callback
                 ├─ enum/numeric/str ─► sensor add-callback
                 └─ invalid ──────────► no entity yet; wait for a real datum
```

Home Assistant fixes platforms at setup time, but the entity type is only
known at runtime. Each platform's `async_setup_entry` therefore registers its
add-entities callback with a shared factory, which routes on first sight.

`CLAIMED_SIGNALS` is a frozenset naming every signal a hand-written entity
owns. A test derives the truth by introspection — collecting `_signal_name`
from every curated entity class the platforms instantiate — and asserts the
frozenset matches exactly. Adding a curated entity without claiming its signal
then fails CI instead of silently producing a duplicate entity for the same
data, and leaving a stale claim behind after deleting one fails too.

### Restore on restart

Lazy creation alone would leave every entity missing until its signal next
arrived — bad for slow signals like odometer, which may not move for hours. On
setup the factory reads `er.async_entries_for_config_entry`, and for each
generic-able signal whose expected `unique_id` is present, recreates the entity
before any datum arrives. `RestoreEntity` then supplies the last value.

Signals are identified by computing the expected `unique_id` for each entry in
`SIGNALS` minus `CLAIMED_SIGNALS` and looking it up, rather than by parsing
names out of registry entries. The metadata table stays the authority.

## The entity model

| Arm Tesla sends | Platform | Attributes from metadata |
|---|---|---|
| `boolean_value` | `binary_sensor` | `device_class` |
| `location_value` | `device_tracker` | — |
| enum arm | `sensor` | `device_class: enum` + `options` |
| numeric arm | `sensor` | `unit`, `device_class`, `state_class` |
| `string_value` | `sensor` | none |
| `invalid` | — | no entity created; an existing one becomes unavailable |

Names are humanised from the signal (`RemoteStartActive` → "Remote start
active") with `has_entity_name`, so the device name prefixes them. Tesla's
descriptions are full sentences and make poor entity names.

Generic entities attach to the same vehicle device as the curated ones, via
the existing `device_info` on the shared base classes. A user sees one device
per VIN, with curated and generic entities side by side.

### When a signal changes arm

The platform is fixed by the first datum and never changes: an entity cannot
migrate from `binary_sensor` to `sensor` without losing its history, which is
the thing this phase is most careful about.

This is not hypothetical. Sentry mode arrives as a plain boolean on older
firmware and as an enum on newer, which is why the curated `_sentry_armed`
already accepts both. Each platform's extractor therefore accepts any arm it
can meaningfully represent — a binary sensor takes a boolean arm, or an enum
whose label maps to on/off. A datum in an arm the entity cannot represent
leaves the last value in place and logs once at debug; it does not go
unavailable, because flapping on a type change is worse than a stale reading.

Enum members come from the live protobuf descriptor, as Phase 2 decided, so a
proto bump cannot put them out of step with the bindings decoding the stream.
Members meaning unknown or SNA map to `None` rather than becoming options, so
Home Assistant never sees a state outside its own `options` list.

The value-extraction helpers currently split between `values.py` and
`local_extras.py` consolidate into one module as `local_extras.py` is removed.

### What lazy creation subsumes

`min_firmware` and `semi_only` are **not** used in this phase. Creating an
entity only when its signal actually arrives already guarantees we never build
one for a signal this car cannot produce, which subsumes firmware and
vehicle-class gating entirely. Those two fields serve Phase 4, where the
options UI can warn before enabling something a given car will never send.

## The migration

`VERSION` goes 2 → 3. `async_migrate_entry` rewrites the affected `unique_id`s
once; Home Assistant runs it before `async_setup_entry`, so it always completes
before a generic entity could claim a target id. The ordering is structural.

```python
for old_suffix, new_suffix in LEGACY_UNIQUE_IDS.items():
    old = f"{vin}_{old_suffix}"
    new = f"{vin}_{new_suffix}"
    entity_id = registry.async_get_entity_id(domain, DOMAIN, old)
    if entity_id is None:
        continue                      # user never had this entity
    if registry.async_get_entity_id(domain, DOMAIN, new):
        continue                      # already migrated, or a collision
    registry.async_update_entity(entity_id, new_unique_id=new)
```

Both guards are load-bearing. `async_update_entity` raises when the target
`unique_id` is in use, and an exception escaping the migration fails the whole
config entry — leaving a user with a dead integration rather than a few stale
names.

Every right-hand side must equal `snake(signal) + "_telemetry"`; a test asserts
that, so the map cannot drift from the naming rule.

### Capture the evidence before deleting anything

The map is **generated, then frozen**. The first implementation task captures
every current `(signal → unique_id suffix)` pair into a committed fixture
*before* any hand-written entity is removed. Once the old code is gone that
evidence is unrecoverable, and the fixture is the only thing that can prove the
migration is complete and correct.

The final map is a subset of the 31, determined by which signals end up in
`CLAIMED_SIGNALS`. That partition is reviewed as its own step rather than
guessed here.

## Testing

- **Routing**: one test per oneof arm, asserting the platform chosen, built on
  real protobuf `Value` messages rather than mocks.
- **`invalid`**: no entity is created from an invalid first datum; an existing
  entity becomes unavailable and does not report a stale value.
- **Claim precedence**: a claimed signal produces no generic entity, and
  `CLAIMED_SIGNALS` matches what the hand-written platforms subscribe to.
- **Enum labels**: Gear reports `"P"` through the generic path; unknown/SNA
  members yield `None` and never appear in `options`.
- **Migration**: renames a legacy id; is idempotent; skips when the target
  exists; skips when the entity was never created; does not raise when the
  registry is empty.
- **Restore**: an entity present in the registry is recreated at setup before
  any datum arrives, and its value is restored.
- **No orphans**: for every pair in the captured fixture, the entity's
  `unique_id` after migration is either unchanged or in `LEGACY_UNIQUE_IDS`.
  This is the test that proves no user loses history.

## Risks

- **Downgrade produces duplicates.** A user who downgrades after migrating gets
  two sets of entities, because the old code recreates the old `unique_id`s
  while the registry holds the new ones. Inherent to any `unique_id` migration;
  documented in the release notes rather than designed away.
- **A partition mistake is silent.** If a signal is wrongly excluded from
  `CLAIMED_SIGNALS`, a curated entity and a generic entity both consume it, and
  the user sees a duplicate. The `CLAIMED_SIGNALS` test is the defence.
- **The captured fixture is one-shot.** If it is wrong at capture time, nothing
  later can detect it. It is reviewed before the deletions land.
