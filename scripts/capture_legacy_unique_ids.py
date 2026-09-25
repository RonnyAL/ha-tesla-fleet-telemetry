#!/usr/bin/env python3
"""Record every current entity's (unique_id suffix -> signal(s), platform).

Run once, before the hand-written entities are replaced. The output is
committed as tests/fixtures/legacy_unique_ids.json and is the evidence that
the migration loses nobody's history. It is never regenerated: after the old
code is gone there is nothing left to regenerate it from.

Most entities declare a class-level ``_signal_name`` we can just read. A
handful don't: composite/derived entities (``AvgBatteryTempSensor``) and both
``device_tracker`` entities (``LocationTracker``, ``RouteTracker``) subscribe
to their signal(s) at runtime inside ``async_added_to_hass`` via
``self._subscribe(...)``/``async_dispatcher_connect(...)`` directly, without
ever setting ``_signal_name``. Reading only the class attribute would
silently record those as signal-less, which is wrong — they very much
consume real signals, just not exactly one apiece via that attribute.

So every entity's ``async_added_to_hass`` is also actually invoked, with the
platform module's ``async_dispatcher_connect`` monkeypatched to record the
dispatcher topic instead of connecting to a real dispatcher. The signal name
is recovered from the topic: ``coordinator.signal_dispatcher_topic`` builds
it as ``f"{DOMAIN}.{vin}.{name}"``, so it's everything after the last dot.
The entities never went through ``entity_platform`` (no ``hass``/``entity_id``
assigned), so restore-state calls harmlessly resolve to "nothing stored" and
each entity's own dispatcher subscriptions still run. If an entity's
``async_added_to_hass`` still raises (e.g. the two accounting sensors, which
call ``async_write_ha_state()`` unconditionally and have no signal to find at
all), the failure is recorded as ``capture_error`` rather than left as a
silent, clean-looking gap.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

REPO = Path(__file__).resolve().parents[1]
VIN = "CAPTUREVIN0000000"


async def _collect() -> dict[str, dict[str, object]]:
    from custom_components.tesla_telemetry import binary_sensor as bs
    from custom_components.tesla_telemetry import device_tracker as dt
    from custom_components.tesla_telemetry import sensor as se

    coordinator = SimpleNamespace(
        vin=VIN,
        vehicle_name="Captured",
        device_info={},
        effective_intervals={},
        get=lambda name: None,
    )
    hass = MagicMock()
    # Platforms now also pull a generic-entity factory out of hass.data and
    # register themselves with it (register_platform); a no-op stub is
    # enough here since this script only cares about the curated entities.
    generic_factory = SimpleNamespace(register_platform=lambda *a, **kw: None)
    hass.data = {
        "tesla_telemetry": {
            "entry": {
                "coordinator": coordinator,
                "generic_factory": generic_factory,
            }
        }
    }
    entry = SimpleNamespace(entry_id="entry", data={"vin": VIN}, options={})

    out: dict[str, dict[str, object]] = {}
    for module, domain in ((se, "sensor"), (bs, "binary_sensor"), (dt, "device_tracker")):
        captured: list[object] = []
        await module.async_setup_entry(
            hass, entry, lambda es, _captured=captured, **kw: _captured.extend(es)
        )
        for entity in captured:
            uid = getattr(entity, "_attr_unique_id", None) or getattr(entity, "unique_id", None)
            if not uid or not uid.startswith(f"{VIN}_"):
                continue

            signals: list[str] = []

            def _record_connect(_hass, topic, _target, _signals=signals):
                name = topic.rsplit(".", 1)[-1]
                if name not in _signals:
                    _signals.append(name)
                return lambda: None

            original_connect = module.async_dispatcher_connect
            module.async_dispatcher_connect = _record_connect
            capture_error: str | None = None
            try:
                await entity.async_added_to_hass()
            except Exception as exc:  # noqa: BLE001 - a visible failure beats a silent gap
                capture_error = f"{type(exc).__name__}: {exc}"
            finally:
                module.async_dispatcher_connect = original_connect

            declared = getattr(entity, "_signal_name", "") or ""
            entry_out: dict[str, object] = {
                "signal": declared,
                "platform": domain,
                "signals": signals,
            }
            if capture_error is not None:
                entry_out["capture_error"] = capture_error
            out[uid[len(VIN) + 1:]] = entry_out
    return out


def main() -> int:
    import sys

    sys.path.insert(0, str(REPO))
    data = asyncio.run(_collect())
    target = REPO / "tests/fixtures/legacy_unique_ids.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print(f"captured {len(data)} entities to {target.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
