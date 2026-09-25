"""One-time unique_id migration, v2 -> v3.

Half the hand-written entities were named editorially rather than
mechanically — abbreviations in both directions, semantic renames, dropped
suffixes — so the uniform generic rule renames them. Renaming a unique_id
orphans that entity's recorder history, so the registry is rewritten once
here instead of aliasing the old names forever.

This map is migration data. Nothing reads it at runtime, and once every entry
has migrated it can be deleted.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.helpers import entity_registry as er

from .const import CONF_VIN, DOMAIN

_LOGGER = logging.getLogger(__name__)

# old unique_id suffix -> new unique_id suffix
# Generated from tests/fixtures/legacy_unique_ids.json by the snippet in
# task-6-brief.md Step 1: every hand-written entity whose class-level signal
# is unclaimed (not in generic.claimed.CLAIMED_SIGNALS) and whose old suffix
# differs from snake(signal) + "_telemetry".
LEGACY_UNIQUE_IDS: dict[str, str] = {
    "arrival_soc_telemetry": "expected_energy_percent_at_trip_arrival_telemetry",   # ExpectedEnergyPercentAtTripArrival
    "battery_heater_telemetry": "battery_heater_on_telemetry",   # BatteryHeaterOn
    "battery_range_telemetry": "est_battery_range_telemetry",   # EstBatteryRange
    "cabin_overheat_protection_telemetry": "cabin_overheat_protection_mode_telemetry",   # CabinOverheatProtectionMode
    "guest_mode_telemetry": "guest_mode_enabled_telemetry",   # GuestModeEnabled
    "hazard_lights_telemetry": "lights_hazards_active_telemetry",   # LightsHazardsActive
    "hvac_left_temp_request_telemetry": "hvac_left_temperature_request_telemetry",   # HvacLeftTemperatureRequest
    "hvac_right_temp_request_telemetry": "hvac_right_temperature_request_telemetry",   # HvacRightTemperatureRequest
    "inside_temperature_telemetry": "inside_temp_telemetry",   # InsideTemp
    "lifetime_energy_charged_telemetry": "lifetime_energy_charged_kwh_telemetry",   # LifetimeEnergyChargedKwh
    "lifetime_energy_regen_telemetry": "lifetime_energy_gained_regen_telemetry",   # LifetimeEnergyGainedRegen
    "motor_stator_temp_front_telemetry": "di_stator_temp_f_telemetry",   # DiStatorTempF
    "motor_stator_temp_rear_telemetry": "di_stator_temp_r_telemetry",   # DiStatorTempR
    "nominal_full_pack_energy_telemetry": "nominal_full_pack_energy_kwh_telemetry",   # NominalFullPackEnergyKwh
    "outside_temperature_telemetry": "outside_temp_telemetry",   # OutsideTemp
    "software_update_download_telemetry": "software_update_download_percent_complete_telemetry",   # SoftwareUpdateDownloadPercentComplete
    "software_update_install_telemetry": "software_update_installation_percent_complete_telemetry",   # SoftwareUpdateInstallationPercentComplete
    "software_version_telemetry": "software_update_version_telemetry",   # SoftwareUpdateVersion
    "tire_pressure_front_left_telemetry": "tpms_pressure_fl_telemetry",   # TpmsPressureFl
    "tire_pressure_front_right_telemetry": "tpms_pressure_fr_telemetry",   # TpmsPressureFr
    "tire_pressure_rear_left_telemetry": "tpms_pressure_rl_telemetry",   # TpmsPressureRl
    "tire_pressure_rear_right_telemetry": "tpms_pressure_rr_telemetry",   # TpmsPressureRr
    "user_present_telemetry": "driver_seat_occupied_telemetry",   # DriverSeatOccupied
    "valet_mode_telemetry": "valet_mode_enabled_telemetry",   # ValetModeEnabled
    "wiper_heat_telemetry": "wiper_heat_enabled_telemetry",   # WiperHeatEnabled
}


async def async_migrate_unique_ids(hass: Any, entry: Any) -> int:
    """Rewrite legacy unique_ids for this entry. Returns how many changed.

    Never raises. async_update_entity raises when the target unique_id is in
    use, and an exception escaping a migration fails the whole config entry —
    a dead integration is far worse than a few stale entity names.
    """
    registry = er.async_get(hass)
    vin = entry.data.get(CONF_VIN)
    if not vin:
        return 0

    migrated = 0
    for old_suffix, new_suffix in LEGACY_UNIQUE_IDS.items():
        old = f"{vin}_{old_suffix}"
        new = f"{vin}_{new_suffix}"
        for domain in ("sensor", "binary_sensor", "device_tracker"):
            entity_id = registry.async_get_entity_id(domain, DOMAIN, old)
            if entity_id is None:
                continue
            if registry.async_get_entity_id(domain, DOMAIN, new) is not None:
                _LOGGER.debug("%s already exists; leaving %s alone", new, old)
                continue
            try:
                registry.async_update_entity(entity_id, new_unique_id=new)
            except ValueError as err:
                _LOGGER.warning("could not migrate %s to %s: %s", old, new, err)
                continue
            migrated += 1
    if migrated:
        _LOGGER.info(
            "tesla_telemetry: migrated %d entity unique_id(s) for vin=%s", migrated, vin
        )
    return migrated
