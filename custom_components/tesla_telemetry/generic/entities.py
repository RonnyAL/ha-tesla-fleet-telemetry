"""Entity classes for signals with no hand-written entity.

The platform is fixed when the entity is created and never changes: migrating
an entity between platforms would lose its history, which is the thing this
phase is most careful about. Each class therefore accepts any arm it can
meaningfully represent and ignores the rest.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.device_tracker import TrackerEntity
from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.restore_state import RestoreEntity

from ..coordinator import SignalSample, signal_dispatcher_topic
from ..values import value_as_bool, value_as_float, value_as_string
from .naming import generic_unique_id, humanise
from .routing import BOOLEAN_ARM, NUMERIC_ARMS

_LOGGER = logging.getLogger(__name__)

# Enum members that mean "no reading", never displayed and never an option.
_NULL_MEMBERS = ("unknown", "sna", "invalid")


def _member_name(value: Any) -> str | None:
    """The proto enum member name for this datum, or None."""
    arm = value.WhichOneof("value")
    if arm is None:
        return None
    field = value.DESCRIPTOR.fields_by_name.get(arm)
    if field is None or field.enum_type is None:
        return None
    member = field.enum_type.values_by_number.get(int(getattr(value, arm)))
    return member.name if member else None


def _strip_prefix(enum_type: Any) -> str:
    import os.path

    names = [v.name for v in enum_type.values]
    return os.path.commonprefix(names) if len(names) > 1 else ""


def _label(member: str, enum_type: Any, labels: dict[str, str] | None) -> str | None:
    if labels and member in labels:
        return labels[member]
    short = member[len(_strip_prefix(enum_type)):] or member
    if short.lower() in _NULL_MEMBERS:
        return None
    from .naming import snake

    return snake(short)


def enum_options(value: Any, meta: Any) -> list[str] | None:
    """Every state this enum can report, for SensorDeviceClass.ENUM.

    Read from the live protobuf descriptor rather than from metadata, so a
    proto bump cannot put the options out of step with the bindings actually
    decoding the stream.
    """
    arm = value.WhichOneof("value")
    if arm is None:
        return None
    field = value.DESCRIPTOR.fields_by_name.get(arm)
    if field is None or field.enum_type is None:
        return None
    labels = getattr(meta, "enum_labels", None)
    out = []
    for member in field.enum_type.values:
        label = _label(member.name, field.enum_type, labels)
        if label is not None and label not in out:
            out.append(label)
    return out


class _GenericEntity:
    """Marker: lets tests tell generic entities from curated ones."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, coordinator: Any, signal: str, meta: Any) -> None:
        self._coordinator = coordinator
        self._signal_name = signal
        self._meta = meta
        self._attr_device_info = coordinator.device_info
        self._attr_unique_id = generic_unique_id(coordinator.vin, signal)
        self._attr_name = humanise(signal)

    async def async_added_to_hass(self) -> None:
        sample = self._coordinator.get(self._signal_name)
        if sample is not None:
            self._handle(sample)
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                signal_dispatcher_topic(self._coordinator.vin, self._signal_name),
                self._on_sample,
            )
        )

    @callback
    def _on_sample(self, sample: SignalSample) -> None:
        self._handle(sample)
        if self.hass is not None:
            self.async_write_ha_state()

    def _handle(self, sample: SignalSample) -> None:
        raise NotImplementedError


class GenericSensor(_GenericEntity, RestoreSensor):
    """Numeric, enum or string signal.

    RestoreSensor, not RestoreEntity: it supplies SensorEntity as well, and
    without SensorEntity this is not a valid sensor at all.
    """

    def __init__(self, coordinator: Any, signal: str, meta: Any) -> None:
        super().__init__(coordinator, signal, meta)
        self._attr_native_unit_of_measurement = meta.unit
        if meta.device_class:
            try:
                self._attr_device_class = SensorDeviceClass(meta.device_class)
            except ValueError:
                _LOGGER.debug("unknown device_class %r for %s", meta.device_class, signal)
        if meta.state_class:
            try:
                self._attr_state_class = SensorStateClass(meta.state_class)
            except ValueError:
                _LOGGER.debug("unknown state_class %r for %s", meta.state_class, signal)

    # native_value, options and available come from SensorEntity, which
    # already reads the matching _attr_ fields.

    def _handle(self, sample: SignalSample) -> None:
        value = sample.value
        if value.HasField("invalid"):
            self._attr_available = False
            return
        self._attr_available = True
        arm = value.WhichOneof("value")
        if arm is None:
            return
        member = _member_name(value)
        if member is not None:
            field = value.DESCRIPTOR.fields_by_name[arm]
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_state_class = None
            self._attr_options = enum_options(value, self._meta)
            self._attr_native_value = _label(
                member, field.enum_type, getattr(self._meta, "enum_labels", None)
            )
            return
        if arm in NUMERIC_ARMS:
            self._attr_native_value = value_as_float(value)
            return
        self._attr_native_value = value_as_string(value)


class GenericBinarySensor(_GenericEntity, BinarySensorEntity, RestoreEntity):
    """Boolean signal."""

    def __init__(self, coordinator: Any, signal: str, meta: Any) -> None:
        super().__init__(coordinator, signal, meta)
        if meta.device_class:
            try:
                self._attr_device_class = BinarySensorDeviceClass(meta.device_class)
            except ValueError:
                _LOGGER.debug("unknown device_class %r for %s", meta.device_class, signal)

    # is_on and available come from BinarySensorEntity.

    def _handle(self, sample: SignalSample) -> None:
        value = sample.value
        if value.HasField("invalid"):
            self._attr_available = False
            return
        self._attr_available = True
        if value.WhichOneof("value") == BOOLEAN_ARM:
            self._attr_is_on = value_as_bool(value)
            return
        member = _member_name(value)
        if member is not None:
            short = member[len(_strip_prefix(
                value.DESCRIPTOR.fields_by_name[value.WhichOneof("value")].enum_type
            )):].lower()
            if short in ("on", "true", "active", "enabled"):
                self._attr_is_on = True
            elif short in ("off", "false", "inactive", "disabled"):
                self._attr_is_on = False
            return
        # An arm this entity cannot represent. Keep the last value: flapping
        # to unavailable on a type change is worse than a stale reading.
        _LOGGER.debug(
            "%s: ignoring datum in arm %s, entity is a binary_sensor",
            self._signal_name,
            value.WhichOneof("value"),
        )


class GenericTracker(_GenericEntity, TrackerEntity, RestoreEntity):
    """Location signal. Kept minimal: latitude/longitude only."""

    _attr_latitude: float | None = None
    _attr_longitude: float | None = None

    # latitude and longitude come from TrackerEntity.

    def _handle(self, sample: SignalSample) -> None:
        value = sample.value
        if value.HasField("invalid"):
            self._attr_available = False
            return
        self._attr_available = True
        if value.WhichOneof("value") == "location_value":
            self._attr_latitude = value.location_value.latitude
            self._attr_longitude = value.location_value.longitude
