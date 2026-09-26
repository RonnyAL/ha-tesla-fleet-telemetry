"""Tests for firmware version comparison and support evidence.

The pending-update trap is the reason this module exists. Before firmware
2024.44 the `Version` signal reported the *available update*, not what was
installed, so a 2024.38 car with a 2024.44.32 update queued reports a number
above the floor. Evidence proven by receipt cannot lie that way: a car cannot
send a field its firmware does not have.
"""
from __future__ import annotations

from typing import ClassVar

import pytest

from custom_components.tesla_telemetry.firmware import (
    FLOOR_INCLUDE_FIELDS,
    FLOOR_MINIMUM_DELTA,
    FirmwareEvidence,
    at_least,
    evidence_from_entry,
    parse_version,
    proof_from_signals,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2024.44.32", (2024, 44, 32)),
        ("2025.44.25.5", (2025, 44, 25, 5)),
        ("2026.26.6", (2026, 26, 6)),
        ("2024.44.32 4c7a3b1e", (2024, 44, 32)),
        ("2024.44.32-rc1", (2024, 44, 32)),
        ("", None),
        (None, None),
        ("not a version", None),
        ("....", None),
    ],
)
def test_parse_version(text, expected) -> None:
    assert parse_version(text) == expected


def test_at_least_compares_numerically_not_lexically() -> None:
    """"2024.9" < "2024.44" is true numerically and false as strings."""
    assert at_least("2024.44", "2024.9")
    assert not at_least("2024.9", "2024.44")


def test_at_least_treats_a_shorter_version_as_zero_padded() -> None:
    assert at_least("2024.44.32", "2024.44")
    assert not at_least("2024.44", "2024.44.32")


def test_at_least_is_false_for_junk() -> None:
    assert not at_least(None, FLOOR_MINIMUM_DELTA)
    assert not at_least("unknown", FLOOR_MINIMUM_DELTA)


def test_proof_takes_the_highest_floor_among_received_signals() -> None:
    # HvacPower is floored at 2024.44.25, ChargerVoltage at 2024.44.32.
    assert proof_from_signals(["VehicleSpeed", "HvacPower"]) == "2024.44.25"
    assert proof_from_signals(["HvacPower", "ChargerVoltage"]) == "2024.44.32"


def test_proof_ignores_unknown_and_unfloored_signals() -> None:
    assert proof_from_signals(["VehicleSpeed"]) is None
    assert proof_from_signals(["NotASignal"]) is None
    assert proof_from_signals([]) is None


def test_proof_alone_establishes_support() -> None:
    evidence = FirmwareEvidence(proven_version="2024.44.32")
    assert evidence.supports(FLOOR_MINIMUM_DELTA)
    assert not evidence.supports(FLOOR_INCLUDE_FIELDS)


def test_the_pending_update_trap_does_not_establish_support() -> None:
    """A 2024.38 car with a 2024.44.32 update queued reports the update."""
    evidence = FirmwareEvidence(
        reported_version="2024.44.32", proven_version="2024.26"
    )
    assert not evidence.supports(FLOOR_MINIMUM_DELTA)


def test_a_reported_version_is_trusted_once_proof_reaches_2024_44() -> None:
    """Corroboration flips exactly the same claim from untrusted to trusted."""
    evidence = FirmwareEvidence(
        reported_version="2025.8.1", proven_version="2024.44.25"
    )
    assert evidence.supports(FLOOR_MINIMUM_DELTA)


def test_a_reported_version_alone_never_establishes_support() -> None:
    assert not FirmwareEvidence(reported_version="2026.30").supports(
        FLOOR_MINIMUM_DELTA
    )


def test_assume_support_bypasses_all_evidence() -> None:
    evidence = FirmwareEvidence(assume_support=True)
    assert evidence.supports(FLOOR_MINIMUM_DELTA)
    assert evidence.supports(FLOOR_INCLUDE_FIELDS)


def test_evidence_from_entry_reads_data_and_options() -> None:
    class Entry:
        data: ClassVar = {
            "firmware_evidence": {
                "reported": "2025.8.1",
                "proven": "2024.44.25",
            }
        }
        options: ClassVar = {"assume_firmware_support": True}

    evidence = evidence_from_entry(Entry())
    assert evidence.reported_version == "2025.8.1"
    assert evidence.proven_version == "2024.44.25"
    assert evidence.assume_support is True


def test_evidence_from_entry_tolerates_a_bare_entry() -> None:
    class Entry:
        data: ClassVar[dict] = {}
        options: ClassVar[dict] = {}

    evidence = evidence_from_entry(Entry())
    assert evidence == FirmwareEvidence()
    assert not evidence.supports(FLOOR_MINIMUM_DELTA)
