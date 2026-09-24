"""Tests for the fleet telemetry errors endpoint and its service.

This endpoint is how you find out why a vehicle accepted the telemetry config
and then never connected — a wrong hostname, an untrusted certificate chain, a
TLS or mTLS failure. None of that is visible from Home Assistant otherwise,
because the car simply never opens the WebSocket.

It is partner-scoped, which is easy to get wrong: it lives under
``partner_accounts``, takes a ``domain`` query parameter rather than a VIN, and
requires the partner (client_credentials) token. Calling the per-vehicle path
with a user token does not work. Those details are asserted here.

  https://developer.tesla.com/docs/fleet-api/endpoints/partner-endpoints
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tesla_telemetry.const import (
    FLEET_API_BASE_URLS,
    TESLA_PARTNER_TOKEN_URL,
)
from custom_components.tesla_telemetry.tesla_api import TeslaApi, TeslaApiError

DOMAIN_NAME = "telemetry.example.invalid"
REGION = "eu"
VIN = "TESTVIN0000000001"
ERRORS_URL = (
    f"{FLEET_API_BASE_URLS[REGION]}/api/1/partner_accounts/fleet_telemetry_errors"
)


def _api(hass: HomeAssistant) -> TeslaApi:
    oauth = AsyncMock()
    oauth.token = {"access_token": "user-token"}
    return TeslaApi(
        aiohttp_client.async_get_clientsession(hass),
        oauth,
        client_id="cid",
        client_secret="csecret",
        region=REGION,
    )


def _mock_partner_token(aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(
        TESLA_PARTNER_TOKEN_URL,
        json={"access_token": "partner-token", "expires_in": 3600},
    )


_SAMPLE = [
    {
        "vin": VIN,
        "error_name": "cloud_manager_error",
        "error": "x509: certificate signed by unknown authority",
        "hostname": DOMAIN_NAME,
        "port": "443",
        "created_at": "2026-09-24T16:47:45.308654633Z",
    },
    {
        "vin": "TESTVIN0000000002",
        "error_name": "handshake_error",
        "error": "tls: bad certificate",
        "hostname": DOMAIN_NAME,
        "port": "443",
        "created_at": "2026-09-24T16:48:01.000000000Z",
    },
]


async def test_uses_the_partner_endpoint_with_a_domain_param(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock_partner_token(aioclient_mock)
    aioclient_mock.get(ERRORS_URL, json={"response": _SAMPLE})

    result = await _api(hass).get_fleet_telemetry_errors(DOMAIN_NAME)

    assert result == _SAMPLE
    request = next(
        call for call in aioclient_mock.mock_calls if "fleet_telemetry_errors" in str(call[1])
    )
    url = request[1]
    assert "/api/1/partner_accounts/fleet_telemetry_errors" in str(url)
    # Vehicle-scoped path would be wrong, and a VIN is not how this is queried.
    assert "/vehicles/" not in str(url)
    assert url.query.get("domain") == DOMAIN_NAME


async def test_sends_the_partner_token_not_the_user_token(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A user token gets a 403 here, so the partner grant must be used."""
    _mock_partner_token(aioclient_mock)
    aioclient_mock.get(ERRORS_URL, json={"response": []})

    await _api(hass).get_fleet_telemetry_errors(DOMAIN_NAME)

    headers = [
        call[3]
        for call in aioclient_mock.mock_calls
        if "fleet_telemetry_errors" in str(call[1])
    ]
    assert headers, "no request recorded for the errors endpoint"
    assert headers[0]["Authorization"] == "Bearer partner-token"


@pytest.mark.parametrize(
    "payload",
    [
        {"response": _SAMPLE},
        {"response": {"fleet_telemetry_errors": _SAMPLE}},
        {"response": {"errors": _SAMPLE}},
        {"response": {"data": _SAMPLE}},
    ],
)
async def test_accepts_the_documented_response_shapes(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, payload: dict[str, Any]
) -> None:
    """Tesla has returned both a bare list and a wrapped object here."""
    _mock_partner_token(aioclient_mock)
    aioclient_mock.get(ERRORS_URL, json=payload)
    assert await _api(hass).get_fleet_telemetry_errors(DOMAIN_NAME) == _SAMPLE


@pytest.mark.parametrize(
    "payload", [{"response": None}, {"response": []}, {"response": {}}, {}]
)
async def test_no_errors_is_an_empty_list_not_a_failure(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, payload: dict[str, Any]
) -> None:
    """No errors reported is the healthy case and must not raise."""
    _mock_partner_token(aioclient_mock)
    aioclient_mock.get(ERRORS_URL, json=payload)
    assert await _api(hass).get_fleet_telemetry_errors(DOMAIN_NAME) == []


async def test_http_error_is_surfaced(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock_partner_token(aioclient_mock)
    aioclient_mock.get(ERRORS_URL, status=403, text="forbidden")
    with pytest.raises(TeslaApiError) as excinfo:
        await _api(hass).get_fleet_telemetry_errors(DOMAIN_NAME)
    assert excinfo.value.status == 403
