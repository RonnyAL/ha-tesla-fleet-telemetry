# Changelog

This is a fork of [johnbr/ha-tesla-fleet-telemetry](https://github.com/johnbr/ha-tesla-fleet-telemetry),
diverged at upstream v0.6.2. Version numbers here are independent of
upstream's from v1.0.0 onward: the two projects had both reached 0.6.2, and
continuing to count in the same space would have produced tags that named
different software.

## v1.0.0

The first release of the fork as its own project. Four phases of work on top
of v0.6.2, plus the fix that makes the fork installable at all.

### Fixed — the fork pointed at upstream

The README's HACS instructions gave **upstream's** repository URL, so anyone
following them installed johnbr's integration rather than this one. The
manifest's `documentation`, `issue_tracker` and `codeowners` were upstream's
too, which sent every "Report an issue" click to a maintainer who has nothing
to do with this code. All four now name this repository, and a test keeps
them that way.

Attribution is unchanged: this remains an MIT fork of johnbr's work, credited
in the README.

### Added — configure the stream from the UI

- **Presets by category.** Named presets (`eco`, `balanced`, `live`, plus the
  legacy `high_rate`) map each of Tesla's 12 documented signal categories to
  an interval. A preset retunes signals that are already enabled and never
  switches new ones on. `default` changes nothing, so an entry that never
  picks one behaves exactly as before.
- **All four of Tesla's per-field options.** `interval_seconds` as before,
  plus `minimum_delta`, `resend_interval_seconds` and `include_fields`.
- **A menu-driven options flow** that stays usable across ~250 signals:
  browse a category for bulk interval edits, or search the whole catalog to
  tune one signal's four knobs. Edits are written once, at the end, so
  abandoning the flow changes nothing.
- **Cost as bounds.** Because `interval_seconds` is a rate ceiling and Tesla
  pushes on change, the sum of `1/interval` is not an estimate of anything —
  but it is a true upper bound, and a resend interval makes a genuine lower
  bound possible. Both are shown before you save. A new
  `Projected monthly signal cost` sensor reports the measured rate since the
  last restart, and reads `unknown` until it has five minutes of data rather
  than publishing a spike.
- **`SelfDrivingMilesSinceReset` now works.** Tesla documents that it never
  reports without `minimum_delta >= 1`; that floor is now applied for you.

### Added — firmware gating

Tesla documents nothing about what a vehicle does with a per-field option its
firmware predates, so nothing new is sent until the car demonstrates support.
Evidence comes from two sources with deliberately different trust: the
`Version` signal is fast but can overstate (before firmware 2024.44 it
reported the *available update*, not the installed version), while receipt of
any signal with a documented firmware floor can only understate, since a car
cannot transmit a field its firmware lacks.

A value you enter for an unsupported option is **stored and shown as
withheld**, never discarded, and starts being sent on its own once the car
proves support.

Floors, from Tesla's announcements: `minimum_delta` and
`resend_interval_seconds` need firmware 2024.44.32; `include_fields` needs
2026.26.6.

### Added — an entity for every signal

Every signal in Tesla's catalog now gets an entity the first time the car
actually sends it, with the platform chosen from the protobuf arm the vehicle
really used rather than from documentation. Entities are restored from the
registry on restart, so a slow signal like the odometer does not read
`unknown` until it next changes.

A one-time registry migration renames 25 legacy unique_ids onto the uniform
rule. **No entity history is lost** — verified against a capture of the
pre-change registry.

### Added — generated signal metadata

`signal_metadata.py` is generated from three sources: the vendored proto's
`Field` enum, Tesla's published catalog, and a hand-maintained table of units
and device classes. 251 signals. A weekly workflow opens a pull request when
Tesla's catalog changes, and CI fails if the committed table drifts from its
inputs.

### Fixed — from the stabilization phase

- Token exchange moved to `fleet-auth.prd.vn.cloud.tesla.com`, per Tesla's
  2025-07-21 announcement.
- `vehicle_location` added to the OAuth scopes; without it Tesla returns 403
  for any configuration containing location fields.
- A `ca_pem` override is now persisted, so a private CA survives the daily
  auto-resync instead of silently reverting after a week.
- A reauth flow, so a scope change no longer requires deleting and re-adding
  the entry — which would have orphaned every entity.
- EU region selection.
- The README's nginx example was wrong: vehicles always open their WebSocket
  at `/`, not at the integration's path.
- A `get_telemetry_errors` service, using the partner endpoint Tesla actually
  documents for it.

### Changed — behaviour on upgrade

The interval preset used to be applied last and unconditionally, so
`high_rate` force-enabled Location and VehicleSpeed and overrode per-signal
settings. It is now a lower layer that any per-signal override beats. This
only affects a `high_rate` user who also set an explicit override on those
two signals.

Existing options are read in both their old and new shapes, an existing
preset is read from its old location, and the configuration pushed to a
vehicle is unchanged for an entry nobody has edited — apart from `Version`,
which is now streamed so the firmware gate has something to learn from.
