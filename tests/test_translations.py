"""Tests for the translation files (``strings.json`` / ``translations/en.json``).

``strings.json`` is the source of truth: hassfest validates it and the HA
translation pipeline regenerates ``translations/en.json`` from it. When a new
config step is added to only one of the two, the next regeneration silently
drops it, so the two are checked for structural parity here.

``selector`` also has to sit at the *top level*, not inside ``config``: a
``SelectSelector(translation_key="region")`` is resolved by the frontend as
``component.tesla_telemetry.selector.region.options.<value>``. Nested one level
too deep, the lookup misses and the radio buttons render their raw values
("na" / "eu") instead of their labels.

Pure JSON, so no Home Assistant is needed.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "tesla_telemetry"
_STRINGS = _DIR / "strings.json"
_EN = _DIR / "translations" / "en.json"
_PKG = "tesla_telemetry_isolated"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_const() -> ModuleType:
    """Load const.py under a synthetic package, with no Home Assistant."""
    if f"{_PKG}.const" in sys.modules:
        return sys.modules[f"{_PKG}.const"]
    if _PKG not in sys.modules:
        pkg = ModuleType(_PKG)
        pkg.__path__ = [str(_DIR)]
        sys.modules[_PKG] = pkg
    spec = importlib.util.spec_from_file_location(f"{_PKG}.const", _DIR / "const.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"{_PKG}.const"] = module
    spec.loader.exec_module(module)
    return module


def _key_paths(node: Any, prefix: str = "") -> set[str]:
    """Every dotted key path in a nested dict (leaf values ignored)."""
    if not isinstance(node, dict):
        return set()
    paths: set[str] = set()
    for key, value in node.items():
        path = f"{prefix}.{key}" if prefix else key
        paths.add(path)
        paths |= _key_paths(value, path)
    return paths


def test_strings_and_en_have_the_same_keys() -> None:
    """en.json is regenerated from strings.json, so their shapes must match."""
    strings = _key_paths(_load(_STRINGS))
    english = _key_paths(_load(_EN))
    assert strings == english, (
        "strings.json and translations/en.json have drifted:\n"
        f"  only in strings.json: {sorted(strings - english)}\n"
        f"  only in en.json:      {sorted(english - strings)}"
    )


def test_selector_block_is_top_level() -> None:
    """HA resolves component.<domain>.selector.<key>, not config.selector.<key>."""
    for path in (_STRINGS, _EN):
        data = _load(path)
        assert "selector" in data, f"{path.name}: no top-level 'selector' block"
        assert "selector" not in data["config"], (
            f"{path.name}: 'selector' is nested under 'config'; the frontend "
            "looks it up at the top level, so the options render as raw values"
        )


def test_every_selector_translation_key_is_defined() -> None:
    """Each translation_key used by a selector has matching option labels."""
    source = (_DIR / "config_flow.py").read_text(encoding="utf-8")
    used = set(re.findall(r'translation_key="([A-Za-z0-9_]+)"', source))
    assert used, "no selector translation_key found in config_flow.py"

    for path in (_STRINGS, _EN):
        defined = _load(path).get("selector", {})
        missing = used - set(defined)
        assert not missing, f"{path.name}: no selector translations for {missing}"


def test_region_selector_covers_every_selectable_region() -> None:
    """The options offered by the config flow all have a label."""
    regions = set(_load_const().SELECTABLE_REGIONS)
    assert regions, "SELECTABLE_REGIONS is empty"

    for path in (_STRINGS, _EN):
        options = _load(path)["selector"]["region"]["options"]
        assert regions <= set(options), (
            f"{path.name}: regions without a label: {sorted(regions - set(options))}"
        )


def test_region_step_is_present() -> None:
    """The region step needs a title/description in both files."""
    for path in (_STRINGS, _EN):
        step = _load(path)["config"]["step"]
        assert "region" in step, f"{path.name}: config.step.region is missing"
        assert step["region"].get("title"), f"{path.name}: region step has no title"
