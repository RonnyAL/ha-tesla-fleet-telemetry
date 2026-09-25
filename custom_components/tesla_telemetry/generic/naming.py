"""Naming rules for generic entities.

snake() decides every generic unique_id. Changing it orphans history for
every entity it touches, so it is pinned by tests against the whole catalog.
"""
from __future__ import annotations

import re

_LOWER_TO_UPPER = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_ACRONYM_END = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])")

# Acronyms that read wrong when sentence-cased: "Dc charging power".
_ACRONYMS = {"dc", "ac", "gps", "hvac", "tpms", "soc", "vin", "bms", "hvil"}


def snake(name: str) -> str:
    """CamelCase signal name to snake_case."""
    return _ACRONYM_END.sub("_", _LOWER_TO_UPPER.sub("_", name)).lower()


def humanise(name: str) -> str:
    """A readable entity name. has_entity_name prefixes the device name."""
    words = snake(name).split("_")
    out = [w.upper() if w in _ACRONYMS else w for w in words]
    first = out[0]
    return " ".join([first if first.isupper() else first.capitalize(), *out[1:]])


def generic_unique_id(vin: str, signal: str) -> str:
    return f"{vin}_{snake(signal)}_telemetry"
