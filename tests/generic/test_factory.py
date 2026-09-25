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


async def test_publishing_through_the_coordinator_creates_a_working_entity(hass) -> None:
    """Critical 1: `handle_sample` must actually work as a dispatcher callback.

    Every other test in this file calls `handle_sample` directly, bypassing
    the dispatcher entirely — they'd all pass even if `handle_sample` were
    unusable as a HA callback. In production it is only ever driven by
    `coordinator.async_publish` through `async_dispatcher_send`, so that is
    the only path that exercises HA's job-type classification.

    Without `@callback` on `handle_sample`, HA classifies it as an executor
    job and runs it on a worker thread; the real `add_entities` it calls
    from there (`EntityPlatform._async_schedule_add_entities`) then calls
    `hass.async_create_task_internal`, which requires the event loop thread
    and raises "loop ... is not the running loop". No entity is ever
    created. This test builds a real `EntityPlatform` (not a hand-rolled
    callback) so that failure mode is actually reachable.
    """
    from homeassistant.helpers import entity_registry as er
    from homeassistant.helpers.dispatcher import async_dispatcher_connect
    from pytest_homeassistant_custom_component.common import (
        MockConfigEntry,
        MockEntityPlatform,
    )

    from custom_components.tesla_telemetry.const import DOMAIN
    from custom_components.tesla_telemetry.coordinator import (
        TeslaTelemetryCoordinator,
        all_signals_topic,
    )
    from custom_components.tesla_telemetry.generic.naming import generic_unique_id

    entry = MockConfigEntry(domain=DOMAIN, unique_id=VIN, data={"vin": VIN}, version=3)
    entry.add_to_hass(hass)

    coordinator = TeslaTelemetryCoordinator(hass, VIN, "Test")
    factory = GenericEntityFactory(hass=hass, entry=entry, coordinator=coordinator)

    platform = MockEntityPlatform(hass, domain="binary_sensor", platform_name=DOMAIN)
    platform.config_entry = entry
    factory.register_platform("binary_sensor", platform._async_schedule_add_entities)

    async_dispatcher_connect(hass, all_signals_topic(VIN), factory.handle_sample)

    # DriveRail, not Locked: Locked is claimed and would get no generic
    # entity, defeating the test.
    coordinator.async_publish("DriveRail", pb.Value(boolean_value=True))
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "binary_sensor", DOMAIN, generic_unique_id(VIN, "DriveRail")
    )
    assert entity_id is not None, "no entity was created from live telemetry"
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "on"


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


async def test_data_arriving_before_restore_creates_only_one_entity(hass) -> None:
    """The mirror of the previous test: data first, restore afterwards.

    `__init__.py` calls `async_restore_known` before subscribing the
    dispatcher, but nothing structural guarantees that order forever, and
    Important 6's cache replay means a signal can be handled live before
    restore ever looks at it. `_created` must make this idempotent
    regardless of which happens first — only the restore-then-data order was
    covered before.
    """
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.tesla_telemetry.const import DOMAIN
    from custom_components.tesla_telemetry.generic.naming import generic_unique_id

    entry = MockConfigEntry(domain=DOMAIN, unique_id=VIN, data={"vin": VIN}, version=3)
    entry.add_to_hass(hass)
    registry = er.async_get(hass)

    coordinator = SimpleNamespace(vin=VIN, device_info={}, get=lambda n: None)
    created: list = []
    factory = GenericEntityFactory(hass=hass, entry=entry, coordinator=coordinator)
    factory.register_platform("sensor", created.extend)

    factory.handle_sample("Odometer", _sample(pb.Value(double_value=1234.0)))
    # Simulate the entity registry now holding the entity a real
    # `add_entities` call would have just registered.
    registry.async_get_or_create(
        "sensor", DOMAIN, generic_unique_id(VIN, "Odometer"), config_entry=entry
    )

    factory.async_restore_known(registry)

    assert len(created) == 1


def test_a_platform_that_never_registered_drops_its_entities() -> None:
    """Platforms register independently; a datum arriving before one has set
    up must not raise."""
    coordinator = SimpleNamespace(vin=VIN, device_info={}, get=lambda n: None)
    factory = GenericEntityFactory(
        hass=SimpleNamespace(), entry=SimpleNamespace(entry_id="e"), coordinator=coordinator
    )
    factory.handle_sample("VehicleSpeed", _sample(pb.Value(double_value=1.0)))


def test_replay_cache_creates_entities_for_samples_already_on_the_coordinator() -> None:
    """Important 6: a datum arriving during platform setup must not be lost.

    __init__.py subscribes the factory to the dispatcher only after
    forwarding entry setups, so a sample that arrives in that window is
    cached on the coordinator and seen by nobody live — without a replay, no
    entity is created until that signal's *next* datum, which may be a long
    time coming.
    """
    samples = {
        "VehicleSpeed": _sample(pb.Value(double_value=10.0)),
        "DriveRail": _sample(pb.Value(boolean_value=True)),
    }
    coordinator = SimpleNamespace(
        vin=VIN,
        device_info={},
        get=lambda n: None,
        all_samples=lambda: list(samples.items()),
    )
    created: dict[str, list] = {"sensor": [], "binary_sensor": [], "device_tracker": []}
    factory = GenericEntityFactory(
        hass=SimpleNamespace(), entry=SimpleNamespace(entry_id="e"), coordinator=coordinator
    )
    for domain in created:
        factory.register_platform(domain, lambda es, d=domain: created[d].extend(es))

    factory.replay_cache()

    assert len(created["sensor"]) == 1
    assert len(created["binary_sensor"]) == 1


def test_replay_cache_does_not_duplicate_a_signal_already_handled_live() -> None:
    samples = {"VehicleSpeed": _sample(pb.Value(double_value=10.0))}
    coordinator = SimpleNamespace(
        vin=VIN,
        device_info={},
        get=lambda n: None,
        all_samples=lambda: list(samples.items()),
    )
    factory, created = _factory()
    factory._coordinator = coordinator

    factory.handle_sample("VehicleSpeed", samples["VehicleSpeed"])
    factory.replay_cache()

    assert len(created["sensor"]) == 1


async def test_a_sensor_keeps_working_after_an_enum_arm(hass) -> None:
    """The arm round-trip must survive real Home Assistant, not just _handle.

    HA forbids a unit on an ENUM sensor, so the enum branch swaps the device
    class and drops the unit. If those are never put back, HA rejects every
    later numeric state write — and because the write happens inside a
    dispatcher callback, the exception is swallowed and logged rather than
    raised. The entity then freezes at its last displayed value while fresh
    data keeps arriving, which no test calling `_handle` directly can see:
    `native_value` updates correctly there, it is the state write that dies.
    """
    from homeassistant.helpers.dispatcher import async_dispatcher_connect
    from pytest_homeassistant_custom_component.common import (
        MockConfigEntry,
        MockEntityPlatform,
    )

    from custom_components.tesla_telemetry.const import DOMAIN
    from custom_components.tesla_telemetry.coordinator import (
        TeslaTelemetryCoordinator,
        all_signals_topic,
    )

    entry = MockConfigEntry(domain=DOMAIN, unique_id=VIN, data={"vin": VIN}, version=3)
    entry.add_to_hass(hass)
    coordinator = TeslaTelemetryCoordinator(hass, VIN, "Test")
    factory = GenericEntityFactory(hass=hass, entry=entry, coordinator=coordinator)
    platform = MockEntityPlatform(hass, domain="sensor", platform_name=DOMAIN)
    platform.config_entry = entry
    factory.register_platform("sensor", platform._async_schedule_add_entities)
    async_dispatcher_connect(hass, all_signals_topic(VIN), factory.handle_sample)

    # VehicleSpeed carries unit=mph and device_class=speed in the catalog.
    coordinator.async_publish("VehicleSpeed", pb.Value(double_value=42.5))
    await hass.async_block_till_done()
    entity_id = next(
        e for e in hass.states.async_entity_ids("sensor") if "vehicle_speed" in e
    )
    first = hass.states.get(entity_id).state

    coordinator.async_publish("VehicleSpeed", pb.Value(shift_state_value=pb.ShiftStateP))
    await hass.async_block_till_done()

    coordinator.async_publish("VehicleSpeed", pb.Value(double_value=55.0))
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.state != first, (
        "the sensor stopped updating after an enum arm — HA is rejecting the "
        "numeric write because device_class is still enum"
    )
    assert state.attributes.get("device_class") == "speed"
    assert state.attributes.get("unit_of_measurement") is not None
