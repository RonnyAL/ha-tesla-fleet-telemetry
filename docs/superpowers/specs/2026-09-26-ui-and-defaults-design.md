# UI and defaults (Phase 4)

Status: approved for planning.
Supersedes nothing. Builds on the Phase 2 metadata table and the Phase 3
generic entity path.

## Goal

Let a user shape the whole telemetry stream from the UI: pick a preset, tune any
of the ~270 signals individually, use the three per-field knobs Tesla added
after `interval_seconds`, and see what the selection costs before saving it.

## Scope

One plan. Five new modules, the options flow rewritten, one generator rule, two
new sensors and three compatibility fixes — larger than Phase 3 but a single
cohesive subsystem, the configuration surface. Nothing here can ship usefully on
its own: a preset with no UI to select it, or a per-field knob with no gate, is
not a smaller increment but a broken one.

## Non-goals

- Deciding the stream for the user. Presets are starting points, not policy.
- `prefer_typed`. Obsolete: since August 2025 vehicles ignore it and always send
  typed data.
- `delivery_policy: "latest"`. It requires an acknowledging fleet-telemetry
  server (>= 0.7.1); our receiver is nginx plus an HA view that never acks.
- Changing any entity's `unique_id`. Phase 4 adds two sensors and touches no
  existing one.
- Per-signal cost attribution as a sensor. The billing unit is a datum; we count
  data, and the per-signal breakdown already lives in the `Signals received`
  attributes.

## Primary sources

Every protocol claim below traces to one of these. Nothing here is inferred from
another integration's behaviour.

| Claim | Source |
|---|---|
| `minimum_delta` and `resend_interval_seconds` exist, firmware >= 2024.44.32 | developer.tesla.com Announcements, 2025-01-09 |
| `include_fields` exists, firmware >= 2026.26.6, Fleet Telemetry client 1.3.0 | developer.tesla.com Announcements, 2026-08-17 |
| `MilesSinceReset` and `SelfDrivingMilesSinceReset` may only include each other | developer.tesla.com Fleet Telemetry, `include_fields` |
| `prefer_typed` is ignored from August 2025 | developer.tesla.com Announcements, 2025-06-05 |
| `delivery_policy` needs server >= 0.7.1 | developer.tesla.com Fleet Telemetry |
| `Version` is the installed firmware, but returned the *available update* before 2024.44 | `signal_catalog/tesla_fields.json`, `Version` |
| `ChargerVoltage` delta recommended; Tesla defaults it to 0.3 from 2025.2.6 | `signal_catalog/tesla_fields.json`, `ChargerVoltage` |
| `Odometer` delta defaults to 0.1 from 2025.2.6 | `signal_catalog/tesla_fields.json`, `Odometer` |
| `SelfDrivingMilesSinceReset` requires `minimum_delta >= 1` | `signal_catalog/tesla_fields.json` |
| `Location` deltas are in metres, from 2025.2.6 | `signal_catalog/tesla_fields.json`, `Location` |
| VIN-level rejection reports `skipped_vehicles` / `unsupported_firmware` | developer.tesla.com Vehicle Endpoints |

**Deliberately absent:** what Tesla does with a per-field key the firmware does
not support. It is undocumented. The design does not assume an answer.

## The three constraints that shape everything

### Tesla's categories are wildly uneven

Charging has 56 signals, Powertrain 35, Vehicle State 32, Climate 29. Today's
options flow is one form with one number per signal. Adding three knobs per
signal would put roughly 1,080 fields in a single voluptuous schema. That is not
a layout problem, it is a dead form. The flow must split, and the split decides
the whole UI.

### `interval_seconds` is a rate ceiling, not a sample rate

Tesla pushes on change and no more than once per `interval_seconds`. A parked
car emits almost nothing regardless of how short the intervals are. Two
consequences run through this document:

- Shortening the interval of a rarely-changing signal is nearly free. This is
  what makes a category-wide preset safe.
- Signal count is a poor proxy for cost. Only continuously-changing signals
  (`ChargerVoltage`, `InsideTemp`) and anything given a
  `resend_interval_seconds` generate volume.

### An unsupported per-field key has undocumented consequences

Tesla documents VIN-level rejection but says nothing about a field key the
firmware cannot honour. The target environment has two cars of different
generations, so "it works on mine" is not evidence. Every new key is therefore
withheld until the car has *demonstrated* it can support it.

## Decisions

### A preset is a per-category interval, and it never enables a signal

A preset maps each of Tesla's categories to one interval, applied only to
signals that are already enabled. If a preset could enable signals, choosing
"eco" would switch on all 56 Charging signals — the opposite of its name.

`default` is empty, so an existing entry that has never chosen a preset resolves
to exactly today's configuration.

| Category | eco | balanced | live |
|---|---|---|---|
| Location | 300 | 30 | 1 |
| Driving | 300 | 30 | 1 |
| Charging | 600 | 60 | 10 |
| Powertrain | 600 | 60 | 5 |
| Safety | 300 | 60 | 10 |
| Climate | 900 | 120 | 30 |
| Vehicle State | 900 | 120 | 30 |
| Media | 1800 | 300 | 60 |
| Service | 3600 | 900 | 300 |
| Vehicle Configuration | 3600 | 3600 | 3600 |
| User Preference | 3600 | 3600 | 3600 |
| (uncategorised) | 900 | 120 | 60 |

Vehicle Configuration and User Preference hold static values — firmware version,
unit preferences — so their interval is long in every preset. Writing it out
rather than omitting it makes the intent legible.

`live` plus Charging at 10s is the one combination that costs real money,
because `ChargerVoltage` "changes frequently, even when not charging": 10s
forever is ~259,000 signals a month from one field. Firmware >= 2025.2.6 already
applies a 0.3 delta of its own; below that the user needs to set one. The cost
display exists to make this visible before saving, not after.

`high_rate` is kept as a fifth preset with its existing meaning (Location and
VehicleSpeed at 1s, nothing else), so automations already calling
`set_interval_preset` keep working.

### The preset lives in options, with one tolerant read of the old location

`CONF_INTERVAL_PRESET` is currently in `entry.data`, written by the
`set_interval_preset` service. Two stores for one setting produces the bug where
the service appears to do nothing because options hold a different preset. So:
options is the single source of truth, the service writes options, and
resolution falls back to `entry.data` for entries written by the old service.

### Firmware support must be demonstrated, not claimed

Two sources of evidence, with different trust:

- **`Version`** — fast, but only means installed firmware from 2024.44 up. Below
  that it reported the pending update, so a 2024.38 car with a 2024.44.32 update
  queued reports a number above the floor. It can overstate.
- **Proof by receipt** — the highest `min_firmware` among signals actually
  received. A car cannot send a field its firmware does not have, so this can
  only understate.

```
supports(floor) = proof >= floor
              or (version_claim >= floor and proof >= 2024.44)
              or assume_firmware_support
```

The middle clause is what makes `Version` usable at all: proof of 2024.44
establishes that `Version` means the installed firmware, and only then is the
claim trusted. Coverage is good — of the 81 default-on signals, 28 prove
>= 2024.44 and four (`ChargerVoltage`, `LocatedAtHome`, `LocatedAtWork`,
`LocatedAtFavorite`) prove >= 2024.44.32, which is the `minimum_delta` floor
exactly. Three default-on signals are floored at 2026.32, clearing
`include_fields`.

Evidence is passed into resolution as a parameter rather than read from the
entry, so the layering is testable without constructing an entry. Callers build
it from the stored value plus what the stream has shown: the all-signals
dispatcher topic that Phase 3 already wired for the generic factory gains one
more subscriber, which raises the stored proof when a sample arrives for a signal
whose documented floor is higher than what is stored. The coordinator's own
per-signal counter is since-restart only, which is why the accumulated proof has
to be persisted rather than recomputed.

Evidence is stored on `entry.data` and is monotonic. Storing it there is load
bearing beyond persistence: an entry update runs the existing options-change
listener, which re-pushes when the fields fingerprint changed — so the moment
proof unlocks a key, the config carrying it reaches the car on its own. A
vehicle that downgrades firmware keeps the higher stored proof; the key is then
sent to a car that may ignore it, which is the same undocumented state as any
other unsupported key and no worse than before the downgrade.

`assume_firmware_support` is an explicit per-entry escape hatch for a user who
knows their car. It is off by default and it is the only way to bypass evidence.

### A withheld value is stored and disclosed, never dropped

When a floor is unmet the form still accepts the number, stores it, and shows
that it is withheld until the car confirms. Accepting a value and silently not
sending it is the worse failure: nothing in the UI would ever explain why the
setting had no effect. Once proof arrives the value starts being pushed without
the user revisiting the form.

### Required deltas come from the override table, with a generator check

Five catalog fields carry delta advice in prose, of four distinct kinds:

| Field | Kind | Value |
|---|---|---|
| `SelfDrivingMilesSinceReset` | required, or the field never reports | `>= 1` |
| `ChargerVoltage` | Tesla's own default from 2025.2.6 | `0.3` |
| `Odometer` | Tesla's own default from 2025.2.6 | `0.1` |
| `InsideTemp` | recommended | none set by Tesla |
| `Location` | supported, measured in metres from 2025.2.6 | none set by Tesla |

Extracting these by regex from English descriptions is brittle, so the values
are hand-maintained in `signal_catalog/overrides.py` with their citation — the
same treatment Phase 2 gave units. The generator gains one rule: **a catalog
field whose description mentions a minimum delta and has no override entry fails
`--check`.** Values stay human; detection of new cases is automatic.

Only the *required* kind is applied during resolution, as a floor beneath the
user's own value. Layer 5 can in principle strip a delta that layer 4 just
required, which would leave `SelfDrivingMilesSinceReset` configured but dead.
It cannot happen in practice and the reason is worth recording: that field needs
firmware 2025.44.25.5 on HW4, far above `minimum_delta`'s 2024.44.32 floor, so a
car able to send it at all necessarily supports the key. A test pins the
implication rather than trusting it. Tesla's own defaults are informational — the car applies them
whether we send them or not — and are surfaced in the per-signal form so a user
does not think a blank field means no delta. The form also states the delta's
unit, which is the signal's own unit except for `Location`, where Tesla measures
it in metres.

### Cost is reported as a bound, and bounds are never called estimates

Three numbers, each labelled for what it is. A month is 2,592,000 seconds.

```
over every enabled signal s:

ceiling = Σ (1 + len(s.include_fields)) × 2_592_000 / s.interval_seconds
floor   = Σ (1 + len(s.include_fields)) × 2_592_000 / s.resend_interval_seconds
             for the signals that have a resend interval; 0 for the rest
```

- **Ceiling** — at most this many data points a month, assuming every signal
  changes at every opportunity. Shown in the options form; available with no
  history.
- **Floor** — a `resend_interval_seconds` is a commitment to send even when
  nothing changed, so this is the volume that cannot be avoided. Over signals
  that have one; zero otherwise.
- **Measured** — a new `Projected monthly signal cost` sensor, from the observed
  rate since this process started, labelled with the window it covers.

The `(1 + len(include_fields))` multiplier is **an inference, not a documented
fact**: Tesla bills per data point, and an included field arrives as an extra
data point in the payload. It is flagged here because it is the one arithmetic
claim in this document without a source. It errs upward, inside a number already
labelled a ceiling, so being wrong costs a conservative display and nothing
else.

## Architecture

`signals.py` is 235 lines doing three jobs. Phase 4 would take it past 700, so
it splits along the seams already there.

| Module | Responsibility | HA imports |
|---|---|---|
| `signals.py` | catalog, `FieldPolicy`, layered resolution | none (lazy only) |
| `presets.py` | the per-category interval tables | none |
| `firmware.py` | version parse/compare, floors, evidence | none |
| `cost.py` | ceiling, floor, measured projection | none |
| `options_flow.py` | the multi-step options flow | yes |

The first four stay free of Home Assistant imports so they unit-test without the
HA harness, which is how `signals.py` is already written. `options_flow.py`
takes `TeslaTelemetryOptionsFlow` out of `config_flow.py`, which is 410 lines of
OAuth and should not also own a five-step form.

### The resolution model

```python
@dataclass(frozen=True, slots=True)
class FieldPolicy:
    interval_seconds: int
    minimum_delta: float | None = None
    resend_interval_seconds: int | None = None
    include_fields: tuple[str, ...] = ()
```

`resolve_field_policies(entry, evidence) -> dict[str, FieldPolicy]`, six layers,
lowest precedence first:

| # | Layer | Effect |
|---|---|---|
| 1 | `DEFAULT_INTERVALS_SECONDS` | the 81 built-in signals and their intervals |
| 2 | preset category interval | replaces the interval of enabled signals in that category |
| 3 | per-signal user overrides | any of the four keys; see the three states below |
| 4 | required deltas | raises `minimum_delta` to the documented floor if lower or unset |
| 5 | firmware gate | removes keys whose floor is not established |
| 6 | — | result is pushed; `effective_intervals` remains a derived view |

`interval_seconds` in a stored override has three distinct states, and the
distinction is load bearing:

| Stored | Meaning |
|---|---|
| `0` | disabled — removes a default-on signal from the config |
| a positive int | pinned — this exact interval, overriding any preset |
| absent or `null` | enabled, interval inherited from the preset or the default |

Without the third state, presets and the signal picker would fight: the picker
currently stores `DEFAULT_NEW_SIGNAL_INTERVAL` (60) for a newly added signal,
which as a pinned value would make every added signal permanently ignore every
preset. The picker therefore stores the inherit state, not 60.

`resolve_effective_intervals` stays as a thin wrapper over layer 6 so the
coordinator's four call sites and the staleness check keep working unchanged.

### `TelemetryFieldConfig` grows three optional keys

```python
@dataclass(slots=True)
class TelemetryFieldConfig:
    interval_seconds: int
    minimum_delta: float | None = None
    resend_interval_seconds: int | None = None
    include_fields: list[str] = field(default_factory=list)
```

`to_dict` omits every key that is `None` or empty. A default configuration
therefore serialises byte-identically to today's, which is what keeps an
untouched entry safe on old firmware.

### The options flow

```
init (menu) ─┬─ preset     default / eco / balanced / live / high_rate
             ├─ category → one of 12, with signal counts → one number per signal
             ├─ signal   → searchable dropdown over the catalog → four knobs
             ├─ cost       rate, plus the ceiling and floor for the selection
             └─ finish     write entry.options
```

Edits accumulate in `self._working` and are written once at `finish`, so
abandoning the flow persists nothing. HA's dropdown selector filters as the user
types, which is the search requirement met without a custom widget.

The bulk category form carries one number per signal, as today. The three
advanced knobs appear only on the per-signal step. That boundary is not
arbitrary: bulk editing wants one number per row, per-field tuning wants four
rows for one signal, and Tesla documents delta advice for exactly five fields —
so the four-knob form is somewhere a user goes deliberately.

`include_fields` is a multi-select over other catalog signals, validated on
submit:

- `MilesSinceReset` and `SelfDrivingMilesSinceReset` may only include each
  other. Documented.
- An included field must itself be enabled in the configuration. **This is our
  rule, not Tesla's** — whether an unconfigured field can be piggybacked is
  undocumented, so we require the configured case and stay inside what is known.
- A signal may not include itself.

## Compatibility

### Stored options change shape

`entry.options[CONF_SIGNAL_OVERRIDES]` goes from `{signal: int}` to
`{signal: {interval_seconds: int, ...}}`. The key name is unchanged, so no
migration of the enable/disable state is needed. HA does not version options, so
the reader accepts both shapes permanently: a bare int is read as
`{"interval_seconds": n}`, and the dict form is written back on the next save.
Tests pin both directions.

### The fields fingerprint must cover the whole policy

`_fields_fingerprint` currently digests `{signal: interval}`. Left alone, an edit
that changes only a `minimum_delta` compares equal to what was last pushed and
never reaches the car. It must digest the full serialised field config. This is
the same failure the Phase 1 fingerprint had, in a new place.

### Staleness stays as it is, and is documented as unsound

`STALE_INTERVAL_MULTIPLIER × interval_seconds` assumes a signal arrives at least
every interval. Push-on-change means it does not: an unchanged signal is never
resent, so a healthy entity can read `unavailable`.

`resend_interval_seconds` is the first thing that makes the question answerable,
and where one is set, staleness is computed from it instead. Where none is set,
today's behaviour is kept deliberately rather than removed, because it is also
the only indication users currently have that a car has gone offline. The
limitation is documented in the README with a pointer to setting a resend
interval on signals whose freshness matters.

## Testing

Pure-function tests, no HA harness:

- Layer order: a preset loses to a per-signal override; a preset never enables a
  disabled signal; `default` resolves identically to no preset at all.
- Legacy option shapes: a bare int resolves the same as the dict form, and a
  round trip through save normalises it without changing meaning.
- Required deltas: enabling `SelfDrivingMilesSinceReset` with no delta yields
  `minimum_delta >= 1`; a user value above the floor is left alone.
- Firmware gate: keys are stripped when the floor is unmet and present when met.
- **The pending-update trap, named explicitly:** a car reporting `Version`
  `2024.44.32` with proof only of `2024.38` resolves to *unsupported*. Add the
  corroborating proof and it flips to supported.
- Version parsing: `2025.44.25.5`, `2026.26.6`, a trailing git hash, and junk
  (which must not raise).
- Cost: ceiling and floor arithmetic including the `include_fields` multiplier;
  measured projection against an injected clock.
- `to_dict` omits unset keys, so a default config serialises exactly as today.

Through HA's flow harness:

- menu → category edit → `finish` writes the expected options and nothing else.
- Abandoning the flow after an edit writes nothing.
- `include_fields` validation rejects each of the three invalid cases with an
  error the form can show.
- The fingerprint changes when only a `minimum_delta` changed, and the
  options-change listener re-pushes.

Generator:

- A catalog field mentioning a minimum delta with no override entry fails
  `gen_signal_metadata.py --check`. Mutation-tested by removing an entry.

## Risks

**An unsupported key reaches a car anyway.** Mitigated by requiring proof, but
`assume_firmware_support` and a firmware downgrade both defeat it by design. The
consequence is undocumented. If it turns out Tesla rejects the whole config, the
symptom is a failed push visible in the log and in the telemetry-errors service
from Phase 1 — recoverable by clearing the setting. This risk is the reason
nothing is sent by default.

**The preset tables are judgement, not measurement.** They are starting points
and the numbers will be wrong for someone. They are one table in one module,
changeable without touching resolution.

**`live` on Charging is expensive on firmware below 2025.2.6**, where Tesla
applies no `ChargerVoltage` delta of its own and the car may also be too old to
accept one. The cost ceiling shown before saving is the mitigation; the README
names the combination.

**The measured projection is short-window early on.** It reports from process
start, so shortly after a restart it is noisy. It is labelled with its window,
and the ceiling and floor are available immediately and need no history.
