#!/usr/bin/env python3
"""Generate custom_components/tesla_telemetry/signal_metadata.py.

    python3 scripts/gen_signal_metadata.py            fetch, regenerate, write
    python3 scripts/gen_signal_metadata.py --offline  regenerate from the snapshot
    python3 scripts/gen_signal_metadata.py --check    fail if the output is stale

Fail-closed throughout: any fetch, parse or validation failure exits
non-zero and writes nothing. A broken fetch that wrote a partial table
would look exactly like Tesla deleting most of the catalogue.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.signal_metadata.proto_parser import parse_field_enum
from scripts.signal_metadata.reconcile import ReconcileError, reconcile
from scripts.signal_metadata.render import render_module
from scripts.signal_metadata.tesla_docs import (
    CatalogError,
    fetch_catalog,
    validate_catalog,
)
from signal_catalog.overrides import OVERRIDES

PROTO = REPO / "custom_components/tesla_telemetry/proto/schemas/vehicle_data.proto"
SNAPSHOT = REPO / "signal_catalog/tesla_fields.json"
GENERATED = REPO / "custom_components/tesla_telemetry/signal_metadata.py"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_STALE = 2


def _load_nodes(offline: bool) -> list[dict[str, Any]]:
    if offline:
        return json.loads(SNAPSHOT.read_text())
    return fetch_catalog()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="use the vendored snapshot instead of fetching",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 2 if the generated file is out of date",
    )
    args = parser.parse_args(argv)
    offline = args.offline or args.check

    try:
        nodes = _load_nodes(offline)
        validate_catalog(nodes)
        proto = parse_field_enum(PROTO.read_text())
        records, warnings = reconcile(proto, nodes, OVERRIDES)
    except (CatalogError, ReconcileError, ValueError) as err:
        print(f"error: {err}", file=sys.stderr)
        return EXIT_FAILED

    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)

    rendered = render_module(records)

    if args.check:
        current = GENERATED.read_text() if GENERATED.exists() else ""
        if current != rendered:
            print(
                "error: signal_metadata.py is out of date — run "
                "python3 scripts/gen_signal_metadata.py --offline",
                file=sys.stderr,
            )
            return EXIT_STALE
        print(f"signal_metadata.py is current ({len(records)} signals)")
        return EXIT_OK

    if not offline:
        SNAPSHOT.write_text(json.dumps(nodes, indent=2, sort_keys=True) + "\n")
    GENERATED.write_text(rendered)
    # relative_to raises when the target is outside the repo, which it is
    # whenever a test points these paths at a temporary directory. A status
    # line must not be able to fail the run.
    try:
        where: Path | str = GENERATED.relative_to(REPO)
    except ValueError:
        where = GENERATED
    print(f"wrote {len(records)} signals to {where}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
