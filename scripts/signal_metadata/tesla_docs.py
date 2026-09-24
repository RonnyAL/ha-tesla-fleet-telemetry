"""Fetch Tesla's published telemetry signal catalog.

The Available Data page is Gatsby-rendered: `VehicleSpeed` does not appear
anywhere in its HTML. The underlying data is served as structured JSON,
reached like this:

    1. GET /docs/page-data/fleet-api/fleet-telemetry/available-data/page-data.json
    2. read `staticQueryHashes`
    3. GET /docs/page-data/sq/d/<hash>.json for each, and take the one
       containing data.allFleetStreamingFieldsCsv.nodes

The hash changes whenever Tesla rebuilds the site, so it is always
discovered and never pinned. This chain is an undocumented framework
internal and is the most likely part of this system to need maintenance;
every failure path therefore raises rather than degrading quietly.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable
from typing import Any

BASE = "https://developer.tesla.com/docs"
PAGE_DATA = f"{BASE}/page-data/fleet-api/fleet-telemetry/available-data/page-data.json"
NODE_KEY = "allFleetStreamingFieldsCsv"

# A healthy catalogue has ~239 entries. Anything far below that means a
# truncated or error response, not that Tesla deleted the catalogue.
MIN_SIGNALS = 200

_TIMEOUT = 45


class CatalogError(Exception):
    """Any failure to obtain a trustworthy catalogue."""


def _urlopen_json(url: str) -> Any:
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError) as err:
        raise CatalogError(f"fetching {url}: {err}") from err
    except json.JSONDecodeError as err:
        raise CatalogError(f"{url} did not return JSON: {err}") from err
    except UnicodeDecodeError as err:
        # A gzip or otherwise binary body. Without this the codec error
        # escapes with no URL in it, so the log says nothing about where the
        # failure came from.
        raise CatalogError(f"{url} did not return decodable text: {err}") from err


def fetch_catalog(
    opener: Callable[[str], Any] = _urlopen_json,
) -> list[dict[str, Any]]:
    """Return the catalogue records, or raise CatalogError."""
    page = opener(PAGE_DATA)
    if not isinstance(page, dict):
        # Valid JSON need not be an object; without this a list or scalar
        # body raises AttributeError, which main() does not catch.
        raise CatalogError(f"{PAGE_DATA} returned {type(page).__name__}, not an object")
    hashes = page.get("staticQueryHashes") or []
    if not hashes:
        raise CatalogError(f"no staticQueryHashes in {PAGE_DATA}")

    for query_hash in hashes:
        data = opener(f"{BASE}/page-data/sq/d/{query_hash}.json")
        node = (data or {}).get("data", {}).get(NODE_KEY)
        if node and node.get("nodes"):
            return list(node["nodes"])

    raise CatalogError(
        f"none of the static queries {hashes} contained {NODE_KEY}; "
        "Tesla's site structure has probably changed"
    )


def validate_catalog(nodes: list[dict[str, Any]]) -> None:
    """Raise CatalogError unless the catalogue looks trustworthy."""
    if len(nodes) < MIN_SIGNALS:
        raise CatalogError(
            f"catalogue has only {len(nodes)} signals, expected at least "
            f"{MIN_SIGNALS} — refusing to treat this as authoritative"
        )
    missing = [i for i, n in enumerate(nodes) if not n.get("field_name")]
    if missing:
        raise CatalogError(f"records without a field_name at indexes {missing[:5]}")
    duplicates = [
        name
        for name, count in Counter(n["field_name"] for n in nodes).items()
        if count > 1
    ]
    if duplicates:
        raise CatalogError(f"duplicate field_name entries: {sorted(duplicates)[:5]}")
