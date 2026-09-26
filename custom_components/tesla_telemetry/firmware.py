"""Which telemetry field keys this vehicle's firmware can honour.

Tesla documents a firmware floor for each of the per-field keys added after
`interval_seconds`, but does not document what a car does with a key it
predates. So nothing new is sent until the vehicle has demonstrated it can
support it, and this module decides what counts as a demonstration.

Two sources of evidence, with different trust:

* The `Version` signal is fast but can *overstate*. Before firmware 2024.44 it
  reported the available software update rather than the installed version, so
  an older car with an update queued reports a number above the floor.
* The highest documented floor among signals actually received can only
  *understate*. A vehicle cannot transmit a field its firmware does not have.

A reported version is therefore trusted only once receipt has proven the car is
at 2024.44 or later, which is precisely the version where `Version` starts
meaning the installed firmware.

No Home Assistant imports: this is pure comparison and is unit-tested without
the HA harness.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .const import CONF_ASSUME_FIRMWARE_SUPPORT, CONF_FIRMWARE_EVIDENCE
from .signal_metadata import SIGNALS

# The version at which `Version` began reporting installed firmware rather
# than the available update. Below it, a reported version means nothing.
FLOOR_VERSION_IS_FIRMWARE = "2024.44"

# Announced 2025-01-09: both keys arrived together.
FLOOR_MINIMUM_DELTA = "2024.44.32"
FLOOR_RESEND_INTERVAL = "2024.44.32"

# Announced 2026-08-17, Fleet Telemetry client 1.3.0.
FLOOR_INCLUDE_FIELDS = "2026.26.6"

# Leading dotted-numeric run. Real values carry suffixes ("2024.44.32 4c7a3b1e",
# "…-rc1") that must not defeat the comparison.
_VERSION_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)")


def parse_version(text: str | None) -> tuple[int, ...] | None:
    """The leading dotted-numeric run of a Tesla version string, or None."""
    if not text:
        return None
    match = _VERSION_RE.match(str(text))
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def at_least(version: str | None, floor: str) -> bool:
    """True when ``version`` is a parseable version >= ``floor``.

    Shorter versions compare as zero-padded, so "2024.44" < "2024.44.32".
    """
    parsed = parse_version(version)
    if parsed is None:
        return False
    target = parse_version(floor)
    if target is None:  # pragma: no cover — every floor here is a literal
        return False
    width = max(len(parsed), len(target))
    padded = parsed + (0,) * (width - len(parsed))
    goal = target + (0,) * (width - len(target))
    return padded >= goal


def proof_from_signals(names: Iterable[str]) -> str | None:
    """The highest documented firmware floor among signals actually received.

    Receipt is proof: the vehicle sent a field that only exists from this
    firmware onwards, so it is at least that version.
    """
    best: str | None = None
    for name in names:
        meta = SIGNALS.get(name)
        floor = getattr(meta, "min_firmware", None)
        if floor is None:
            continue
        if best is None or at_least(floor, best):
            best = floor
    return best


@dataclass(frozen=True, slots=True)
class FirmwareEvidence:
    """What this vehicle has shown about its firmware."""

    reported_version: str | None = None
    proven_version: str | None = None
    assume_support: bool = False

    def supports(self, floor: str) -> bool:
        """Whether a field key with this firmware floor may be sent."""
        if self.assume_support:
            return True
        if at_least(self.proven_version, floor):
            return True
        # A reported version is only meaningful once receipt has shown the car
        # is at the version where `Version` stopped meaning "available update".
        return at_least(self.reported_version, floor) and at_least(
            self.proven_version, FLOOR_VERSION_IS_FIRMWARE
        )


def evidence_from_entry(entry: Any) -> FirmwareEvidence:
    """Read stored evidence off a config entry.

    Duck-typed rather than typed against ``ConfigEntry`` so this module stays
    importable without Home Assistant.
    """
    data = getattr(entry, "data", None) or {}
    options = getattr(entry, "options", None) or {}
    stored = data.get(CONF_FIRMWARE_EVIDENCE) or {}
    return FirmwareEvidence(
        reported_version=stored.get("reported"),
        proven_version=stored.get("proven"),
        assume_support=bool(options.get(CONF_ASSUME_FIRMWARE_SUPPORT, False)),
    )
