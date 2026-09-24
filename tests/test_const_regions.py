"""Tests for region constants and token-based region detection (``const.py``).

The config flow preselects the account region from the ``ou_code`` claim of
the access-token JWT. ``const.py`` is pure (stdlib only), so it is loaded
directly by path under a synthetic package, with no Home Assistant installed.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

# --- Load const.py in isolation under a synthetic package -------------------
_PKG = "tesla_telemetry_isolated"
_DIR = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "tesla_telemetry"
)


def _load_isolated() -> ModuleType:
    if f"{_PKG}.const" in sys.modules:
        return sys.modules[f"{_PKG}.const"]
    pkg = ModuleType(_PKG)
    pkg.__path__ = [str(_DIR)]
    sys.modules[_PKG] = pkg
    spec = importlib.util.spec_from_file_location(
        f"{_PKG}.const", _DIR / "const.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"{_PKG}.const"] = module
    spec.loader.exec_module(module)
    return sys.modules[f"{_PKG}.const"]


const = _load_isolated()


def _make_token(claims: dict) -> str:
    """A syntactically valid, unsigned JWT with the given claims."""
    header = base64.urlsafe_b64encode(b'{"alg":"RS256","typ":"JWT"}').rstrip(b"=")
    payload = base64.urlsafe_b64encode(
        json.dumps(claims).encode()
    ).rstrip(b"=")
    return f"{header.decode()}.{payload.decode()}.signature"


# ---------------------------------------------------------------------------
# region tables
# ---------------------------------------------------------------------------
def test_partner_token_url_is_global_host() -> None:
    # The client_credentials endpoint is the same host for every region;
    # regional scoping happens via the `audience` in the request body.
    assert const.TESLA_PARTNER_TOKEN_URL == (
        "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token"
    )


def test_selectable_regions_subset_of_known_regions() -> None:
    assert set(const.SELECTABLE_REGIONS) <= set(const.FLEET_API_BASE_URLS)
    assert const.REGION_CN not in const.SELECTABLE_REGIONS


def test_ou_codes_map_to_regions() -> None:
    assert set(const.TOKEN_OU_CODES.values()) == set(const.FLEET_API_BASE_URLS)


# ---------------------------------------------------------------------------
# region_from_access_token
# ---------------------------------------------------------------------------
def test_detects_na_eu_cn() -> None:
    assert const.region_from_access_token(_make_token({"ou_code": "NA"})) == "na"
    assert const.region_from_access_token(_make_token({"ou_code": "EU"})) == "eu"
    assert const.region_from_access_token(_make_token({"ou_code": "cn"})) == "cn"


def test_unknown_or_missing_ou_code_returns_none() -> None:
    assert const.region_from_access_token(_make_token({"ou_code": "XX"})) is None
    assert const.region_from_access_token(_make_token({"sub": "abc"})) is None


def test_malformed_tokens_return_none() -> None:
    assert const.region_from_access_token("") is None
    assert const.region_from_access_token("not-a-jwt") is None
    assert const.region_from_access_token("a.!!!.c") is None  # bad base64/json


# ---------------------------------------------------------------------------
# OAuth endpoints
# ---------------------------------------------------------------------------
# Tesla's third-party token docs: "calls to /token must use the
# fleet-auth.prd.vn.cloud.tesla.com domain as these calls can come from
# application servers and require different rate limits", reinforced by the
# 2025-07-21 announcement warning that auth.tesla.com token generation became
# unreliable from August 2025. /authorize is browser-facing and stays put.
#
# The OpenID discovery document still advertises the old auth.tesla.com token
# endpoint, so these are pinned to stop it being "corrected" back.
_FLEET_AUTH_HOST = "https://fleet-auth.prd.vn.cloud.tesla.com"


def test_token_exchange_uses_the_fleet_auth_host() -> None:
    const = _load_isolated()
    assert const.TESLA_USER_TOKEN_URL == f"{_FLEET_AUTH_HOST}/oauth2/v3/token"
    # application_credentials hands HA the same endpoint for both grants.
    assert const.OAUTH_TOKEN_URL == const.TESLA_USER_TOKEN_URL
    # Partner (client_credentials) calls are server-to-server too.
    assert const.TESLA_PARTNER_TOKEN_URL == f"{_FLEET_AUTH_HOST}/oauth2/v3/token"


def test_authorize_stays_on_auth_tesla_com() -> None:
    const = _load_isolated()
    assert const.OAUTH_AUTHORIZE_URL == "https://auth.tesla.com/oauth2/v3/authorize"


def test_no_token_endpoint_still_points_at_auth_tesla_com() -> None:
    const = _load_isolated()
    for name in ("TESLA_USER_TOKEN_URL", "OAUTH_TOKEN_URL", "TESLA_PARTNER_TOKEN_URL"):
        url = getattr(const, name)
        assert "auth.tesla.com/oauth2/v3/token" not in url, (
            f"{name} uses the legacy token host: {url}"
        )


# ---------------------------------------------------------------------------
# Malformed JWT payloads
# ---------------------------------------------------------------------------
# `token.split(".")[1]` of an arbitrary string can base64-decode to any JSON
# value, not just an object. These used to raise AttributeError out of
# async_oauth_create_entry and fail the config flow *after* the user had
# already completed Tesla's consent screen.
@pytest.mark.parametrize(
    "payload", [["x"], 123, "a string", None, True, 1.5, []]
)
def test_non_object_jwt_payloads_return_none(payload: object) -> None:
    const = _load_isolated()
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    assert const.region_from_access_token(f"header.{body}.signature") is None


def test_object_payload_without_ou_code_returns_none() -> None:
    const = _load_isolated()
    body = base64.urlsafe_b64encode(json.dumps({"sub": "x"}).encode())
    body = body.rstrip(b"=").decode()
    assert const.region_from_access_token(f"header.{body}.signature") is None


# ---------------------------------------------------------------------------
# Detected region vs. what the form offers
# ---------------------------------------------------------------------------
def test_cn_is_detected_but_not_selectable() -> None:
    """A CN account must not become a SelectSelector default it has no option
    for — the config flow clamps to SELECTABLE_REGIONS."""
    const = _load_isolated()
    body = base64.urlsafe_b64encode(json.dumps({"ou_code": "CN"}).encode())
    body = body.rstrip(b"=").decode()
    assert const.region_from_access_token(f"h.{body}.s") == const.REGION_CN
    assert const.REGION_CN not in const.SELECTABLE_REGIONS


def test_config_flow_clamps_detected_region_to_selectable() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "custom_components"
        / "tesla_telemetry"
        / "config_flow.py"
    ).read_text(encoding="utf-8")
    assert "if detected in SELECTABLE_REGIONS:" in source, (
        "config_flow must only preselect a region the form offers"
    )
