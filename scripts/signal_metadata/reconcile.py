"""Merge the proto, Tesla's catalog and the overrides into one table.

The proto Field enum is authoritative for existence and id: a name absent
from it can never arrive on the wire, so a signal Tesla documents but our
vendored proto lacks is excluded and reported — that is the proto-drift
signal the weekly refresh surfaces.

"In the proto but undocumented" is a normal state. Five signals already
shipped as defaults are in it: LifetimeEnergyChargedKwh,
LifetimeEnergyGainedRegen, NominalFullPackEnergyKwh, RemoteStartActive and
ScheduledDepartureTime.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .proto_parser import ProtoField

PLACEHOLDER_PREFIXES: tuple[str, ...] = ("Deprecated_", "Experimental_")
_NOT_A_SIGNAL = frozenset({"Unknown"})

# Types for which a unit or a state_class is meaningless. An override putting
# one on these is a mistake worth failing over, not quietly dropping — this is
# the only automated check on a hand-maintained table.
#
# `time` and `timestamp` belong here as much as `boolean` does: "kWh" on an
# arrival time is exactly the same error. device_class is deliberately NOT
# checked, because `enum` is the correct device class for an enum signal and
# twenty overrides legitimately set it.
_NON_NUMERIC_TYPES = frozenset(
    {"boolean", "string", "enum", "Location", "time", "timestamp"}
)

# Tesla states delta advice in prose, in both spellings. The values are
# hand-entered in the overrides table; this only detects a field that has
# advice and no entry, so a new one cannot slip through unnoticed.
_DELTA_ADVICE_RE = re.compile(r"minimum[ _]delta", re.IGNORECASE)


class ReconcileError(Exception):
    """The three inputs cannot be merged into a trustworthy table."""


@dataclass(frozen=True, slots=True)
class SignalRecord:
    name: str
    field_id: int
    category: str | None
    value_type: str | None
    enum_name: str | None
    unit: str | None
    device_class: str | None
    state_class: str | None
    enum_labels: dict[str, str] | None
    min_firmware: str | None
    semi_only: bool
    documented: bool
    minimum_delta_required: float | None
    minimum_delta_default: float | None
    minimum_delta_recommended: bool


def _clean(value: Any) -> str | None:
    """Tesla ships "" rather than null for absent values."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _is_placeholder(name: str) -> bool:
    return name in _NOT_A_SIGNAL or name.startswith(PLACEHOLDER_PREFIXES)


def reconcile(
    proto: ProtoField,
    nodes: list[dict[str, Any]],
    overrides: dict[str, Any],
) -> tuple[list[SignalRecord], list[str]]:
    documented = {n["field_name"]: n for n in nodes}
    warnings: list[str] = []

    unknown_overrides = sorted(set(overrides) - set(proto.ids))
    if unknown_overrides:
        raise ReconcileError(
            f"overrides name signals that are in neither source: "
            f"{unknown_overrides} — remove them or fix the spelling"
        )

    docs_only = sorted(set(documented) - set(proto.ids))
    if docs_only:
        warnings.append(
            f"{len(docs_only)} signal(s) are documented by Tesla but absent "
            f"from the vendored proto, so they are excluded: {docs_only}. "
            "The proto needs bumping — see proto/README.md."
        )

    records: list[SignalRecord] = []
    for name, field_id in proto.ids.items():
        if _is_placeholder(name):
            continue
        node = documented.get(name, {})
        value_type = _clean(node.get("type"))
        override = overrides.get(name)

        if override is not None and value_type in _NON_NUMERIC_TYPES:
            for attribute in ("unit", "state_class"):
                offending = getattr(override, attribute, None)
                if offending:
                    raise ReconcileError(
                        f"{name}: override sets {attribute} {offending!r} but "
                        f"Tesla documents the type as {value_type!r}"
                    )

        description = _clean(node.get("description")) or ""
        if _DELTA_ADVICE_RE.search(description):
            has_advice = override is not None and (
                override.minimum_delta_required is not None
                or override.minimum_delta_default is not None
                or override.minimum_delta_recommended
            )
            if not has_advice:
                raise ReconcileError(
                    f"{name}: Tesla's description mentions a minimum delta but "
                    f"the overrides table has no entry for it. Transcribe the "
                    f"value by hand into signal_catalog/overrides.py — set "
                    f"minimum_delta_required, minimum_delta_default, or "
                    f"minimum_delta_recommended."
                )

        records.append(
            SignalRecord(
                name=name,
                field_id=field_id,
                category=_clean(node.get("category")),
                value_type=value_type,
                enum_name=_clean(node.get("proto_enum_name")),
                unit=override.unit if override else None,
                device_class=override.device_class if override else None,
                state_class=override.state_class if override else None,
                enum_labels=override.enum_labels if override else None,
                min_firmware=proto.firmware.get(field_id),
                semi_only=field_id in proto.semi_only,
                documented=name in documented,
                minimum_delta_required=(
                    override.minimum_delta_required if override else None
                ),
                minimum_delta_default=(
                    override.minimum_delta_default if override else None
                ),
                minimum_delta_recommended=(
                    override.minimum_delta_recommended if override else False
                ),
            )
        )

    records.sort(key=lambda r: r.field_id)
    return records, warnings
