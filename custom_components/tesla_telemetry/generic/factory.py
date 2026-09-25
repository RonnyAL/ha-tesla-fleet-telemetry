"""Create entities for signals with no hand-written entity.

Home Assistant fixes platforms at setup time, but the entity type is only
known when a datum arrives. Each platform therefore registers its
add-entities callback here, and this routes on first sight.
"""
from __future__ import annotations

import logging
from typing import Any

from ..signal_metadata import SIGNALS
from .claimed import CLAIMED_SIGNALS
from .entities import GenericBinarySensor, GenericSensor, GenericTracker
from .naming import generic_unique_id
from .routing import BINARY_SENSOR, DEVICE_TRACKER, SENSOR, platform_for

_LOGGER = logging.getLogger(__name__)

_CLASSES = {
    SENSOR: GenericSensor,
    BINARY_SENSOR: GenericBinarySensor,
    DEVICE_TRACKER: GenericTracker,
}


class GenericEntityFactory:
    """One per config entry. Owns every generic entity for that vehicle."""

    def __init__(self, hass: Any, entry: Any, coordinator: Any) -> None:
        self._hass = hass
        self._entry = entry
        self._coordinator = coordinator
        self._add_entities: dict[str, Any] = {}
        self._created: set[str] = set()

    def register_platform(self, domain: str, add_entities: Any) -> None:
        self._add_entities[domain] = add_entities

    def handle_sample(self, signal: str, sample: Any) -> None:
        """Create the entity for `signal` if this is the first usable datum."""
        if signal in self._created or signal in CLAIMED_SIGNALS:
            return
        meta = SIGNALS.get(signal)
        if meta is None:
            # The vendored proto is ahead of the metadata table. Ignore rather
            # than raise: this runs inside a dispatcher callback, and an
            # exception here would break every other subscriber on it.
            _LOGGER.debug("no metadata for signal %s; no generic entity", signal)
            return
        domain = platform_for(sample.value)
        if domain is None:
            return          # invalid or empty: wait for a real datum
        add_entities = self._add_entities.get(domain)
        if add_entities is None:
            _LOGGER.debug("platform %s not set up yet; dropping %s", domain, signal)
            return
        # Mark before adding: async_add_entities can re-enter, and a burst of
        # data for one new signal must not produce two entities.
        self._created.add(signal)
        add_entities([_CLASSES[domain](self._coordinator, signal, meta)])

    def async_restore_known(self, registry: Any) -> None:
        """Recreate entities already in the registry, before any datum.

        Without this, a slow signal such as odometer would have no entity
        until it next changed, which can be hours.
        """
        from homeassistant.helpers import entity_registry as er

        known = {
            entry.unique_id: entry
            for entry in er.async_entries_for_config_entry(
                registry, self._entry.entry_id
            )
        }
        for signal in SIGNALS:
            if signal in CLAIMED_SIGNALS or signal in self._created:
                continue
            entry = known.get(generic_unique_id(self._coordinator.vin, signal))
            if entry is None:
                continue
            add_entities = self._add_entities.get(entry.domain)
            cls = _CLASSES.get(entry.domain)
            if add_entities is None or cls is None:
                continue
            self._created.add(signal)
            add_entities([cls(self._coordinator, signal, SIGNALS[signal])])
