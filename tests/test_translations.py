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


def _load_presets() -> ModuleType:
    """Load presets.py under the synthetic package, with no Home Assistant."""
    _load_const()  # presets.py imports nothing from const, but this seeds _PKG
    name = f"{_PKG}.presets"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _DIR / "presets.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
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
    source = "\n".join(
        (_DIR / name).read_text(encoding="utf-8")
        for name in ("config_flow.py", "options_flow.py")
    )
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


# ---------------------------------------------------------------------------
# Reauth surface
# ---------------------------------------------------------------------------
# The reauth flow is what lets an existing entry pick up a newly-added OAuth
# scope (HA refreshes with a stored refresh_token, which carries the scopes it
# was minted with). A missing translation here is a broken dialog, and a
# placeholder the code does not supply is a KeyError when HA renders the step.
def test_reauth_step_and_aborts_are_translated() -> None:
    for path in (_STRINGS, _EN):
        config = _load(path)["config"]
        step = config["step"]
        assert "reauth_confirm" in step, f"{path.name}: no reauth_confirm step"
        assert step["reauth_confirm"].get("title")
        assert step["reauth_confirm"].get("description")
        for reason in ("reauth_successful", "wrong_account"):
            assert reason in config["abort"], f"{path.name}: no '{reason}' abort"


def test_reauth_description_placeholders_are_supplied() -> None:
    """Every {placeholder} in the step text is passed by config_flow.py."""
    description = _load(_STRINGS)["config"]["step"]["reauth_confirm"]["description"]
    used = set(re.findall(r"\{([a-z_]+)\}", description))
    assert used, "reauth_confirm description has no placeholders to check"

    source = (_DIR / "config_flow.py").read_text(encoding="utf-8")
    block = re.search(
        r'step_id="reauth_confirm".*?description_placeholders=\{(.*?)\}',
        source,
        re.DOTALL,
    )
    assert block, "config_flow.py passes no description_placeholders for reauth_confirm"
    supplied = set(re.findall(r'"([a-z_]+)":', block.group(1)))
    missing = used - supplied
    assert not missing, f"reauth_confirm uses {missing} but config_flow supplies {supplied}"


def test_abort_reasons_used_in_code_are_translated() -> None:
    """Every async_abort(reason=...) in the config flow has a message."""
    source = "\n".join(
        (_DIR / name).read_text(encoding="utf-8")
        for name in ("config_flow.py", "options_flow.py")
    )
    used = set(re.findall(r'async_abort\(\s*reason="([a-z_]+)"', source))
    assert used, "no async_abort reasons found in config_flow.py"
    for path in (_STRINGS, _EN):
        defined = set(_load(path)["config"]["abort"])
        missing = used - defined
        assert not missing, f"{path.name}: untranslated abort reasons {sorted(missing)}"


def test_every_preset_has_a_label() -> None:
    """A preset with no label renders as its raw name in the radio list."""
    presets = set(_load_presets().PRESETS)
    for path in (_STRINGS, _EN):
        options = _load(path)["selector"]["interval_preset"]["options"]
        missing = presets - set(options)
        assert not missing, f"{path.name}: presets without a label: {sorted(missing)}"
