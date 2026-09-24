"""Shared fixtures.

Most of the suite deliberately loads individual modules by path with no Home
Assistant installed (see the module docstrings in test_signals.py and
test_const_regions.py) — those stay fast and dependency-free. The config-flow
tests need a real HA instance, which
``pytest-homeassistant-custom-component`` provides.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let HA discover custom_components/tesla_telemetry during tests."""
    return
