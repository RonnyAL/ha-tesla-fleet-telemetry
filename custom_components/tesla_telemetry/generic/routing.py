"""Choose an entity platform from the oneof arm Tesla sent.

The catalog metadata says a signal exists and what Tesla documents it as. It
does not say this car sends it, or in which arm — only the arriving datum
knows that. Phase 1 showed the cost of assuming otherwise: three signals
gated on firmware 2026.32 were enabled by default and would have produced
permanently-unknown entities on an older car.
"""
from __future__ import annotations

from typing import Any

BOOLEAN_ARM = "boolean_value"
LOCATION_ARM = "location_value"
NUMERIC_ARMS = frozenset({"int_value", "long_value", "float_value", "double_value"})

SENSOR = "sensor"
BINARY_SENSOR = "binary_sensor"
DEVICE_TRACKER = "device_tracker"


def platform_for(value: Any) -> str | None:
    """The platform this datum belongs to, or None if it cannot decide."""
    if value.HasField("invalid"):
        return None
    arm = value.WhichOneof("value")
    if arm is None:
        return None
    if arm == BOOLEAN_ARM:
        return BINARY_SENSOR
    if arm == LOCATION_ARM:
        return DEVICE_TRACKER
    # Enum, numeric and string all present as a sensor. There are 42 enum
    # arms in the Value message, so this is the catch-all rather than a list.
    return SENSOR
