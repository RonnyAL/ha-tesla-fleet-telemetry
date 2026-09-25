"""First-sight entity creation.

The factory is the only thing that turns an arriving datum into an entity, so
its skip rules matter as much as its create rules: a claimed signal must not
get a duplicate, an unknown signal must not raise inside the dispatcher, and
a burst of data for one new signal must produce exactly one entity.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pb = pytest.importorskip("custom_components.tesla_telemetry.proto.vehicle_data_pb2")

from custom_components.tesla_telemetry.generic.factory import (
    GenericEntityFactory,
)

VIN = "TESTVIN0000000001"


def _sample(value):
    return SimpleNamespace(value=value, received_at=0.0, payload_created_at=None)


def _factory():
    coordinator = SimpleNamespace(vin=VIN, device_info={}, get=lambda n: None)
    created: dict[str, list] = {"sensor": [], "binary_sensor": [], "device_tracker": []}
    factory = GenericEntityFactory(
        hass=SimpleNamespace(), entry=SimpleNamespace(entry_id="e"), coordinator=coordinator
    )
    for domain in created:
        factory.register_platform(domain, lambda es, d=domain: created[d].extend(es))
    return factory, created


def test_creates_a_sensor_for_a_numeric_signal() -> None:
    factory, created = _factory()
    factory.handle_sample("VehicleSpeed", _sample(pb.Value(double_value=10.0)))
    assert len(created["sensor"]) == 1
    assert created["sensor"][0].unique_id == f"{VIN}_vehicle_speed_telemetry"


def test_creates_a_binary_sensor_for_a_boolean_signal() -> None:
    # DriveRail, not Locked: Locked is in CLAIMED_SIGNALS (LockBinarySensor
    # inverts it), so it would get no generic entity and defeat this test.
    factory, created = _factory()
    factory.handle_sample("DriveRail", _sample(pb.Value(boolean_value=True)))
    assert len(created["binary_sensor"]) == 1


def test_a_claimed_signal_gets_no_generic_entity() -> None:
    """Otherwise the user sees two entities for the same data."""
    from custom_components.tesla_telemetry.generic.claimed import CLAIMED_SIGNALS

    claimed = next(iter(CLAIMED_SIGNALS))
    factory, created = _factory()
    factory.handle_sample(claimed, _sample(pb.Value(double_value=1.0)))
    assert not any(created.values())


def test_repeated_data_create_one_entity() -> None:
    """Review Focus 5: a burst before the first entity is added."""
    factory, created = _factory()
    for _ in range(5):
        factory.handle_sample("VehicleSpeed", _sample(pb.Value(double_value=10.0)))
    assert len(created["sensor"]) == 1


def test_a_signal_absent_from_the_catalog_is_ignored() -> None:
    """Review Focus 4: proto newer than metadata. Must not raise inside the
    dispatcher, which would break every other subscriber on that callback."""
    factory, created = _factory()
    factory.handle_sample("NotARealSignal", _sample(pb.Value(double_value=1.0)))
    assert not any(created.values())


def test_an_invalid_first_datum_creates_nothing() -> None:
    factory, created = _factory()
    factory.handle_sample("VehicleSpeed", _sample(pb.Value(invalid=True)))
    assert not any(created.values())
    factory.handle_sample("VehicleSpeed", _sample(pb.Value(double_value=5.0)))
    assert len(created["sensor"]) == 1


async def test_restore_recreates_a_known_entity(hass) -> None:
    """Without this, a slow signal has no entity until it next changes.

    Odometer can sit unchanged for hours; a user restarting Home Assistant
    would find the entity simply missing rather than showing its last value.
    """
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.tesla_telemetry.const import DOMAIN
    from custom_components.tesla_telemetry.generic.naming import generic_unique_id

    entry = MockConfigEntry(domain=DOMAIN, unique_id=VIN, data={"vin": VIN}, version=3)
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor", DOMAIN, generic_unique_id(VIN, "Odometer"), config_entry=entry
    )

    coordinator = SimpleNamespace(vin=VIN, device_info={}, get=lambda n: None)
    created: list = []
    factory = GenericEntityFactory(hass=hass, entry=entry, coordinator=coordinator)
    factory.register_platform("sensor", created.extend)

    factory.async_restore_known(registry)

    assert [e.unique_id for e in created] == [generic_unique_id(VIN, "Odometer")]


async def test_restore_does_not_duplicate_an_already_created_entity(hass) -> None:
    """Restore runs at setup and data can arrive immediately after."""
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.tesla_telemetry.const import DOMAIN
    from custom_components.tesla_telemetry.generic.naming import generic_unique_id

    entry = MockConfigEntry(domain=DOMAIN, unique_id=VIN, data={"vin": VIN}, version=3)
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor", DOMAIN, generic_unique_id(VIN, "Odometer"), config_entry=entry
    )

    coordinator = SimpleNamespace(vin=VIN, device_info={}, get=lambda n: None)
    created: list = []
    factory = GenericEntityFactory(hass=hass, entry=entry, coordinator=coordinator)
    factory.register_platform("sensor", created.extend)

    factory.async_restore_known(registry)
    factory.handle_sample("Odometer", _sample(pb.Value(double_value=1234.0)))

    assert len(created) == 1


def test_a_platform_that_never_registered_drops_its_entities() -> None:
    """Platforms register independently; a datum arriving before one has set
    up must not raise."""
    coordinator = SimpleNamespace(vin=VIN, device_info={}, get=lambda n: None)
    factory = GenericEntityFactory(
        hass=SimpleNamespace(), entry=SimpleNamespace(entry_id="e"), coordinator=coordinator
    )
    factory.handle_sample("VehicleSpeed", _sample(pb.Value(double_value=1.0)))
