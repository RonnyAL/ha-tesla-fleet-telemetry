# CLAUDE.md — ha-tesla-fleet-telemetry (RonnyAL fork)

## What this is

A fork of [johnbr/ha-tesla-fleet-telemetry](https://github.com/johnbr/ha-tesla-fleet-telemetry)
(MIT), a Home Assistant custom integration that receives Tesla Fleet Telemetry
streams directly inside HA. nginx terminates the vehicle's mTLS and forwards the
WebSocket to an HA view; the integration decodes the protobuf stream into entities.

Goal of the fork: fix the bugs we hit, then let users stream **any** signal from
Tesla's catalog with sensible defaults and full control from the UI — without
hand-written entity code per signal.

## Target environment (the primary target)

- Home Assistant OS, installed via HACS custom repository (this fork).
- An **EU**-region Tesla account, so region selection has to work — not just NA.
- Two vehicles of different generations (one pre-Juniper, one Juniper), so both
  the older and the newer signal/firmware sets have to be supported. Signals
  gated on recent firmware must not be assumed present.
- A reverse proxy terminates mTLS on a shared port 443 (`ssl_verify_client`,
  Tesla `prod_ca.crt`) and forwards the car's request for `/` to
  `/api/tesla_telemetry/ws` with the proxy-secret and verified-VIN headers.
- Core HA `tesla_fleet` integration stays in place for **commands** (polling
  disabled). This integration is read-only. Same Tesla developer app and
  partner key for both.
- Prefer event-driven designs over polling.

## Ground rules

- **Never push to, or open PRs/issues against, the upstream repo** unless the
  owner explicitly asks. `origin` = RonnyAL fork; `upstream` = johnbr (fetch only).
- Work on feature branches; open PRs **into the fork's `main`**. Small, focused
  commits with clear messages.
- Keep the integration domain `tesla_telemetry` so the fork is a drop-in
  replacement. Preserve existing entity `unique_id`s — changing them orphans
  users' entity history.
- Every behavior change gets tests. Keep all existing tests passing
  (`pytest`), and make `ruff check .` pass (upstream currently has ~20 ruff
  findings; fixing them is an early, separate commit).
- Never commit secrets or personal data: VINs, tokens, private keys, proxy
  secrets, hostnames of the owner's infra. Use obvious placeholders in tests.
- Ask before destructive git operations (force-push, history rewrites, branch
  deletion) and before creating releases.
- When unsure about Tesla behavior, find a primary source (Tesla developer
  docs, `teslamotors/fleet-telemetry`, `teslamotors/vehicle-command`) and
  cite it in the PR description. Don't guess protocol details.

## Starting point

1. Fork + clone, add `upstream` remote.
2. Merge upstream PR #2 (EU region selection, IngmarStein) and PR #3
   (Let's Encrypt Gen-Y roots YE/YR in the default CA bundle). They merge
   cleanly onto `main` (v0.6.2).
3. Apply `tesla_telemetry_local.patch` (provided alongside this file). It
   contains, on top of main + #2 + #3:
   - `vehicle_location` added to `OAUTH_SCOPES`.
   - `proto/schemas/vehicle_data.proto` bumped to fleet-telemetry
     `8fbaa100bd365936dab6ecbf0e2d7070c4d765cb` (purely additive: 10 new Field
     values) and `vehicle_data_pb2.py` regenerated with **protoc 25.3**
     (per `proto/README.md`, keeps compatibility across protobuf 4.25–7.x).
   - `local_extras.py`: ~42 extra signals with hand-written entities, hooked in
     from `const.py`, `sensor.py`, `binary_sensor.py`. This is a stopgap —
     Phase 3 replaces it with generic entities (keep its unique_id suffixes
     where the same entity survives).
   - Test tweak: examples of "non-default signal" changed to `Hvil`.
   Verified: applies cleanly, 29/29 tests pass.

## Known bugs and gaps (all confirmed during setup)

1. **Missing `vehicle_location` scope** → Tesla returns 403
   `missing scopes vehicle_location` when the config includes location fields.
   Fixed in the patch.
2. **README nginx example is wrong**: it only forwards
   `/api/tesla_telemetry/ws`, but vehicles always open their WebSocket at `/`
   (Tesla's server registers only `/`; the config has hostname+port, no path).
   Correct pattern: `location = / { proxy_pass http://HA:8123/api/tesla_telemetry/ws; ... }`
   plus `location / { return 404; }`. Note the `=`: two plain `location /`
   blocks make nginx reject the config.
3. **Token exchange domain**: `TESLA_USER_TOKEN_URL` is
   `https://auth.tesla.com/oauth2/v3/token`; Tesla announced (2025-07-21) that
   token exchange should use `fleet-auth.prd.vn.cloud.tesla.com`. Core HA
   `tesla_fleet` uses `https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token`.
   Switch, and verify the authorize URL too.
4. **`ca_pem` override is not persisted**: `bootstrap` accepts `ca_pem`, but the
   daily auto-resync (>7 days) and options-change re-push always use
   `DEFAULT_CA_BUNDLE_PEM` → a private CA silently breaks after a week. Persist
   the override (or a "CA mode" option) in the entry and use it on every push.
5. **No reauth flow**: a scope change currently requires deleting and re-adding
   the entry. Implement `async_step_reauth` / `reauth_confirm`.
6. **Outdated vendored proto**: already bumped in the patch; automate it
   (Phase 2).
7. Region is fixed to NA upstream (PR #2 fixes it).
8. Troubleshooting docs should mention: `key_paired: false` / `config: null`
   in `get_telemetry_config` means the virtual key isn't on the car
   (`https://tesla.com/_ak/<partner-domain>`); the Fleet API
   `/api/1/vehicles/{vin}/fleet_telemetry_errors` endpoint (worth exposing as a
   service); HA only logs `vehicle connected` at info level; nginx logs
   WebSocket requests to the access log only when they close.

## Roadmap

### Phase 1 — Stabilize
Items 3–5 and 8 above, ruff clean, README fixes (nginx, EU, troubleshooting).
Add a `get_telemetry_errors` service.

### Phase 2 — Signal metadata + proto automation
- Build `signal_metadata.json` (generated, committed) from:
  - the `Field` enum in `vehicle_data.proto` (names, ids, firmware notes in comments),
  - Tesla's Available Data page
    (https://developer.tesla.com/docs/fleet-api/fleet-telemetry/available-data):
    Field, Category, Type, Description. Units appear only in some descriptions.
- Units / device classes: a hand-maintained override table. Cross-reference
  HA core's `teslemetry` integration (Apache-2.0; attribute) for units already
  mapped. Unknown unit → no unit (don't guess).
- A generator script (`scripts/`) that regenerates metadata + `vehicle_data_pb2.py`
  (protoc 25.3), plus a scheduled GitHub Action that opens a PR in the fork when
  Tesla's proto or docs change.

### Phase 3 — Generic entities for the whole catalog
- Entity type from the Value oneof arm Tesla actually sends:
  boolean → `binary_sensor`; enum → `sensor` with `device_class: enum` and
  options from the proto enum (strip the enum's common value prefix, snake_case);
  numeric → `sensor` with metadata unit/device_class/state_class;
  string → text `sensor`; location → `device_tracker`; `invalid` → unavailable.
- Create entities lazily when a signal's first datum arrives (only what the car
  sends), and restore them on restart from the entity registry.
- Curated entities (derived logic: plugged-in veto, charging active, etc.) stay
  hand-written and take precedence; everything else goes through the generic path.
- Remove `local_extras.py` once covered; keep unique_ids stable.

### Phase 4 — UI and defaults
- Presets (e.g. eco / balanced / live) by category, with per-signal override of
  `interval_seconds`, plus `minimum_delta` (firmware ≥ 2024.44.32) and
  `include_fields` (firmware ≥ 2026.26.6) where supported.
- Show the estimated monthly cost for the current selection (reuse the existing
  signal-count / cost sensors' pricing constant).
- Options flow must stay usable with ~270 signals: category sections, search.

### Phase 5 — Release
- Version bumps in `manifest.json`, GitHub releases with notes, HACS-compatible
  (`hacs.json` already present). Existing CI: lint, test, hacs/hassfest validate.

## Out of scope
- Vehicle commands (core `tesla_fleet` handles them; the vehicle-command protocol
  is a separate, session-based signing scheme).
- Upstreaming — only on the owner's explicit request.

## Useful references
- Tesla: Fleet Telemetry docs, Available Data, Announcements/Changelog on developer.tesla.com
- `teslamotors/fleet-telemetry` — `protos/`, `config/files/prod_ca.crt`,
  `server/streaming/server.go` (vehicle connects to `/`, VIN from client cert CN)
- Core HA `tesla_fleet` and `teslemetry` integrations (reference implementations)
