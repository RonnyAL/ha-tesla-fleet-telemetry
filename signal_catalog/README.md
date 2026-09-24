# Signal catalog (build-time inputs)

Inputs to `scripts/gen_signal_metadata.py`, which writes
`custom_components/tesla_telemetry/signal_metadata.py`.

Nothing here is shipped to users or read at runtime — only the generated
module is. That is why these files live outside the integration package.

## `tesla_fields.json`

A vendored snapshot of Tesla's published signal catalog. Refreshed by the
generator, and by the weekly `signal-catalog` workflow. **This is the file to
read in a refresh pull request** — it is the human-readable record of what
Tesla changed. The generated module is fallout, like `vehicle_data_pb2.py`.

Obtained by discovery, never a pinned URL, because the hash changes on every
site rebuild:

1. `GET /docs/page-data/fleet-api/fleet-telemetry/available-data/page-data.json`
2. read `staticQueryHashes`
3. `GET /docs/page-data/sq/d/<hash>.json`, take the one containing
   `data.allFleetStreamingFieldsCsv.nodes`

The Available Data page itself is Gatsby-rendered and contains no signal
names in its HTML, so scraping it directly does not work.

## `overrides.py`

Hand-maintained units and device/state classes. Units are never parsed from
Tesla's prose — see the module docstring.

Entries marked `# teslemetry` were cross-referenced from Home Assistant's
`teslemetry` integration, which is Apache-2.0 licensed, Copyright the Home
Assistant authors. Only unit and device-class facts were used.

## Regenerating

```bash
python3 scripts/gen_signal_metadata.py            # fetch, regenerate, write
python3 scripts/gen_signal_metadata.py --offline  # regenerate from the vendored snapshot
python3 scripts/gen_signal_metadata.py --check    # fail if the output is stale
```

`--check` is what runs on every pull request. It never touches the network.
