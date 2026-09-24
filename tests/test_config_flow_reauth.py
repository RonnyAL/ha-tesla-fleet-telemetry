"""Reauth flow tests, against a real Home Assistant instance.

Reauth is the only way an existing entry picks up a newly added OAuth scope:
HA refreshes with the stored refresh_token, which carries the scopes granted
when it was first minted, so adding one to OAUTH_SCOPES does nothing for
entries created earlier. It also has to preserve the VIN, region and endpoint
settings, because those decide the entity unique_ids — re-creating the entry
instead would orphan the vehicle's history.
"""
from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.components.application_credentials import (
    ClientCredential,
    async_import_client_credential,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.tesla_telemetry.const import (
    CONF_HOSTNAME,
    CONF_PARTNER_DOMAIN,
    CONF_PORT,
    CONF_PRIVATE_KEY_PEM,
    CONF_PROXY_SECRET,
    CONF_REGION,
    CONF_VEHICLE_NAME,
    CONF_VIN,
    DOMAIN,
    FLEET_API_BASE_URLS,
    OAUTH_TOKEN_URL,
)

CLIENT_ID = "test-client-id"
CLIENT_SECRET = "test-client-secret"
VIN = "TESTVIN0000000001"
OTHER_VIN = "TESTVIN0000000002"
REGION = "eu"


@pytest.fixture(autouse=True)
async def setup_credentials(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "application_credentials", {})
    await async_import_client_credential(
        hass,
        DOMAIN,
        ClientCredential(CLIENT_ID, CLIENT_SECRET),
        "test-impl",
    )


@pytest.fixture(autouse=True)
def mock_setup_entry() -> Generator[AsyncMock]:
    """Don't boot the integration when a reauth reloads the entry.

    ``async_update_reload_and_abort`` reloads, which would set up the real
    platforms — including the legacy device_tracker, whose stale-check timer
    outlives the test and trips HA's lingering-timer assertion. Flow tests
    care about the flow, not about setup.
    """
    with patch(
        "custom_components.tesla_telemetry.async_setup_entry", return_value=True
    ) as mock:
        yield mock


def _entry(**overrides: Any) -> MockConfigEntry:
    data: dict[str, Any] = {
        "auth_implementation": "test-impl",
        "token": {
            "access_token": "old-access-token",
            "refresh_token": "old-refresh-token",
            "expires_in": 3600,
            "expires_at": 9_999_999_999,
        },
        CONF_VIN: VIN,
        CONF_VEHICLE_NAME: "Test Car",
        CONF_REGION: REGION,
        CONF_HOSTNAME: "telemetry.example.invalid",
        CONF_PORT: 443,
        CONF_PARTNER_DOMAIN: "example.invalid",
        CONF_PROXY_SECRET: "proxy-secret-placeholder",
        CONF_PRIVATE_KEY_PEM: "-----BEGIN PRIVATE KEY-----\nplaceholder\n"
        "-----END PRIVATE KEY-----",
    }
    data.update(overrides)
    return MockConfigEntry(
        domain=DOMAIN, unique_id=VIN, data=data, title=f"Test Car ({VIN})", version=2
    )


async def _start_reauth(hass: HomeAssistant, entry: MockConfigEntry) -> dict[str, Any]:
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    return result


def _mock_token_exchange(aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(
        OAUTH_TOKEN_URL,
        json={
            "access_token": "new-access-token",
            "refresh_token": "new-refresh-token",
            "token_type": "Bearer",
            "expires_in": 3600,
        },
    )


def _mock_vehicles(aioclient_mock: AiohttpClientMocker, *vins: str) -> None:
    aioclient_mock.get(
        f"{FLEET_API_BASE_URLS[REGION]}/api/1/vehicles",
        json={"response": [{"vin": v, "display_name": "Test Car"} for v in vins]},
    )


async def _complete_oauth(
    hass: HomeAssistant,
    hass_client_no_auth,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Walk the reauth_confirm -> external auth -> callback round-trip."""
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    state = result["url"].split("state=")[1].split("&")[0]

    client = await hass_client_no_auth()
    response = await client.get(f"/auth/external/callback?code=abcd&state={state}")
    assert response.status == 200

    return await hass.config_entries.flow.async_configure(result["flow_id"])


async def test_reauth_updates_token_and_keeps_vehicle_settings(
    hass: HomeAssistant,
    hass_client_no_auth,
    aioclient_mock: AiohttpClientMocker,
    current_request_with_host: None,
) -> None:
    entry = _entry()
    result = await _start_reauth(hass, entry)

    _mock_token_exchange(aioclient_mock)
    _mock_vehicles(aioclient_mock, VIN)

    result = await _complete_oauth(hass, hass_client_no_auth, result)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"

    # The new token landed...
    assert entry.data["token"]["access_token"] == "new-access-token"
    assert entry.data["token"]["refresh_token"] == "new-refresh-token"
    # ...and nothing that decides an entity unique_id moved.
    assert entry.data[CONF_VIN] == VIN
    assert entry.data[CONF_REGION] == REGION
    assert entry.data[CONF_HOSTNAME] == "telemetry.example.invalid"
    assert entry.data[CONF_PORT] == 443
    assert entry.data[CONF_PARTNER_DOMAIN] == "example.invalid"
    assert entry.data[CONF_PRIVATE_KEY_PEM].startswith("-----BEGIN PRIVATE KEY-----")
    assert entry.unique_id == VIN
    # Still exactly one entry — reauth must not create a second.
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_reauth_with_a_different_account_aborts(
    hass: HomeAssistant,
    hass_client_no_auth,
    aioclient_mock: AiohttpClientMocker,
    current_request_with_host: None,
) -> None:
    """Signing in with an account that does not own the VIN must fail loudly."""
    entry = _entry()
    result = await _start_reauth(hass, entry)

    _mock_token_exchange(aioclient_mock)
    _mock_vehicles(aioclient_mock, OTHER_VIN)

    result = await _complete_oauth(hass, hass_client_no_auth, result)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    # The old token is left alone rather than replaced with the wrong account's.
    assert entry.data["token"]["access_token"] == "old-access-token"


async def test_reauth_requests_the_current_scopes(
    hass: HomeAssistant,
    hass_client_no_auth,
    aioclient_mock: AiohttpClientMocker,
    current_request_with_host: None,
) -> None:
    """The authorize URL must carry today's OAUTH_SCOPES, which is the whole
    point of reauth after a scope is added."""
    from custom_components.tesla_telemetry.const import OAUTH_SCOPES

    entry = _entry()
    result = await _start_reauth(hass, entry)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.EXTERNAL_STEP
    for scope in OAUTH_SCOPES:
        assert scope in result["url"], f"{scope} missing from the authorize URL"
    assert "vehicle_location" in result["url"]


async def test_reauth_confirm_form_is_shown_before_leaving(
    hass: HomeAssistant,
) -> None:
    """The user gets a confirmation step, not an immediate redirect."""
    entry = _entry()
    result = await _start_reauth(hass, entry)
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"]["vin"] == VIN
    assert result["description_placeholders"]["vehicle"] == "Test Car"


async def test_reauth_source_is_set(hass: HomeAssistant) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    await entry.start_reauth_flow(hass)
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == config_entries.SOURCE_REAUTH
