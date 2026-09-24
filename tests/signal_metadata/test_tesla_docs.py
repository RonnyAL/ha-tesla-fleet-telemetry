"""Tests for fetching Tesla's published signal catalog.

The Available Data page is Gatsby-rendered and contains no signal names in
its HTML. The data is reachable as structured JSON, but only via a
discovery chain whose hash changes on every site rebuild, so it can never
be pinned:

    page-data.json -> staticQueryHashes -> sq/d/<hash>.json

Every failure here must be fail-closed. A broken fetch that wrote a
partial table would look exactly like "Tesla deleted most of the catalog".
"""
from __future__ import annotations

import io
from typing import Any

import pytest

from scripts.signal_metadata import tesla_docs
from scripts.signal_metadata.tesla_docs import (
    CatalogError,
    fetch_catalog,
    validate_catalog,
)

_NODES = [
    {"field_name": "VehicleSpeed", "category": "Driving", "type": "real",
     "proto_enum_name": "", "description": "The speed of the vehicle.",
     "vehicle_data_equivalent": "drive_state.speed"},
    {"field_name": "Locked", "category": "Vehicle State", "type": "boolean",
     "proto_enum_name": "", "description": "Whether the vehicle is locked.",
     "vehicle_data_equivalent": ""},
]


def _opener(pages: dict[str, Any]):
    def open_json(url: str) -> Any:
        if url not in pages:
            raise CatalogError(f"unexpected url {url}")
        return pages[url]
    return open_json


def _working_pages(nodes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    base = "https://developer.tesla.com/docs"
    return {
        f"{base}/page-data/fleet-api/fleet-telemetry/available-data/page-data.json":
            {"staticQueryHashes": ["111", "222"]},
        f"{base}/page-data/sq/d/111.json": {"data": {"somethingElse": {}}},
        f"{base}/page-data/sq/d/222.json": {
            "data": {"allFleetStreamingFieldsCsv": {"nodes": nodes or _NODES}}
        },
    }


def test_discovers_the_catalog_through_the_hash_chain() -> None:
    result = fetch_catalog(opener=_opener(_working_pages()))
    assert [n["field_name"] for n in result] == ["VehicleSpeed", "Locked"]


def test_missing_static_query_hashes_is_an_error() -> None:
    pages = _working_pages()
    key = next(k for k in pages if k.endswith("available-data/page-data.json"))
    pages[key] = {"staticQueryHashes": []}
    with pytest.raises(CatalogError, match="staticQueryHashes"):
        fetch_catalog(opener=_opener(pages))


def test_no_matching_static_query_is_an_error() -> None:
    pages = _working_pages()
    pages["https://developer.tesla.com/docs/page-data/sq/d/222.json"] = {"data": {}}
    with pytest.raises(CatalogError, match="allFleetStreamingFieldsCsv"):
        fetch_catalog(opener=_opener(pages))


def test_non_json_body_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Review Focus 1: an HTTP 200 carrying an interstitial or login wall.

    This drives the real `_urlopen_json`, not a stub that raises on our
    behalf: the code under test is the `json.JSONDecodeError` handler, and a
    fake opener that raises CatalogError itself would pass even if that
    handler were deleted.
    """
    class _HtmlResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc: object) -> None:
            self.close()

    monkeypatch.setattr(
        tesla_docs.urllib.request,
        "urlopen",
        lambda *a, **kw: _HtmlResponse(b"<!DOCTYPE html><html>Just a moment...</html>"),
    )
    with pytest.raises(CatalogError, match="did not return JSON"):
        fetch_catalog()


def test_non_utf8_body_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A gzip or binary body decoded as text must also fail closed, with a
    message naming the URL rather than a bare codec error."""
    class _BinaryResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc: object) -> None:
            self.close()

    monkeypatch.setattr(
        tesla_docs.urllib.request,
        "urlopen",
        lambda *a, **kw: _BinaryResponse(b"\x1f\x8b\x08\x00\xff\xfe"),
    )
    with pytest.raises(CatalogError, match="page-data"):
        fetch_catalog()


def test_validate_rejects_an_implausibly_small_catalog() -> None:
    with pytest.raises(CatalogError, match="only 2"):
        validate_catalog(_NODES)


def test_validate_rejects_duplicate_field_names() -> None:
    """Review Focus 3: a duplicate must not silently last-wins."""
    nodes = [dict(_NODES[0]) for _ in range(250)]
    with pytest.raises(CatalogError, match="duplicate"):
        validate_catalog(nodes)


def test_validate_rejects_a_record_without_a_field_name() -> None:
    nodes: list[dict[str, Any]] = [
        {"field_name": f"Signal{i}", "type": "real"} for i in range(250)
    ]
    nodes[7] = {"type": "real"}
    with pytest.raises(CatalogError, match="field_name"):
        validate_catalog(nodes)


def test_validate_accepts_a_plausible_catalog() -> None:
    nodes: list[dict[str, Any]] = [
        {"field_name": f"Signal{i}", "type": "real"} for i in range(250)
    ]
    validate_catalog(nodes)  # must not raise



def test_valid_json_that_is_not_an_object_is_an_error() -> None:
    """A JSON list or scalar body must fail closed, not raise AttributeError
    out of fetch_catalog where the CLI does not catch it."""
    with pytest.raises(CatalogError, match="not an object"):
        fetch_catalog(opener=lambda url: ["unexpected"])
