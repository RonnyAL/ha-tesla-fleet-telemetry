"""Tests for the default CA bundle and override resolution (``tls_ca.py``).

The bundle is the TLS trust anchor pushed to the vehicle in
``fleet_telemetry_config``. If it is wrong the car cannot validate the
WebSocket endpoint and simply stops connecting, with no error surfaced in
Home Assistant — so the contents are pinned here.

``tls_ca.py`` is pure (stdlib only), so it loads directly by path with no
Home Assistant installed.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from datetime import UTC
from pathlib import Path
from types import ModuleType

import pytest

_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "tesla_telemetry"
_PKG = "tesla_telemetry_isolated"

_CERT_RE = re.compile(
    r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.DOTALL
)


def _load() -> ModuleType:
    if f"{_PKG}.tls_ca" in sys.modules:
        return sys.modules[f"{_PKG}.tls_ca"]
    if _PKG not in sys.modules:
        pkg = ModuleType(_PKG)
        pkg.__path__ = [str(_DIR)]
        sys.modules[_PKG] = pkg
    spec = importlib.util.spec_from_file_location(f"{_PKG}.tls_ca", _DIR / "tls_ca.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"{_PKG}.tls_ca"] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Override resolution
# ---------------------------------------------------------------------------
# A `ca_pem:` override used to apply only to the service call that carried it:
# the daily auto-resync and the options-change re-push both rebuilt the config
# from DEFAULT_CA_BUNDLE_PEM, so a private CA silently reverted days later.
# The override now lives on the config entry and every push resolves through
# here.
def test_no_override_yields_the_default_bundle() -> None:
    tls_ca = _load()
    for empty in (None, "", "   ", "\n\t "):
        assert tls_ca.ca_bundle_pem(empty) == tls_ca.DEFAULT_CA_BUNDLE_PEM.strip() + "\n"


def test_override_replaces_the_default_entirely() -> None:
    tls_ca = _load()
    private = "-----BEGIN CERTIFICATE-----\nprivateca\n-----END CERTIFICATE-----"
    result = tls_ca.ca_bundle_pem(private)
    assert result == private + "\n"
    assert "ISRG Root X1" not in result


@pytest.mark.parametrize("raw", ["pem-body", "  pem-body  ", "pem-body\n\n"])
def test_result_is_always_stripped_and_newline_terminated(raw: str) -> None:
    """Tesla's fleet_telemetry_config expects `ca` in exactly this shape."""
    result = _load().ca_bundle_pem(raw)
    assert result == "pem-body\n"
    assert not result.startswith((" ", "\n"))
    assert result.endswith("\n")
    assert not result.endswith("\n\n")


# ---------------------------------------------------------------------------
# Default bundle contents
# ---------------------------------------------------------------------------
def test_default_bundle_parses_and_is_self_consistent() -> None:
    tls_ca = _load()
    blocks = _CERT_RE.findall(tls_ca.DEFAULT_CA_BUNDLE_PEM)
    assert len(blocks) == 7, f"expected 7 certificates, found {len(blocks)}"
    # Guards against a truncated paste: every BEGIN has a matching END.
    assert tls_ca.DEFAULT_CA_BUNDLE_PEM.count(
        "-----BEGIN CERTIFICATE-----"
    ) == tls_ca.DEFAULT_CA_BUNDLE_PEM.count("-----END CERTIFICATE-----")


def test_default_bundle_covers_the_expected_issuers() -> None:
    """The roots vehicles need: ISRG X1/X2, the Gen-Y roots, USERTrust."""
    x509 = pytest.importorskip("cryptography.x509")
    tls_ca = _load()
    subjects = {
        x509.load_pem_x509_certificate(block.encode()).subject.rfc4514_string()
        for block in _CERT_RE.findall(tls_ca.DEFAULT_CA_BUNDLE_PEM)
    }
    for expected in ("CN=ISRG Root X1", "CN=ISRG Root X2", "CN=Root YE", "CN=Root YR"):
        assert any(s.startswith(expected) for s in subjects), f"missing {expected}"
    assert any("USERTrust RSA" in s for s in subjects), "missing USERTrust RSA"


def test_default_bundle_certificates_are_unexpired() -> None:
    """A trust anchor that has expired makes vehicles stop connecting."""
    x509 = pytest.importorskip("cryptography.x509")
    from datetime import datetime

    tls_ca = _load()
    now = datetime.now(UTC)
    for block in _CERT_RE.findall(tls_ca.DEFAULT_CA_BUNDLE_PEM):
        cert = x509.load_pem_x509_certificate(block.encode())
        assert cert.not_valid_after_utc > now, (
            f"{cert.subject.rfc4514_string()} expired on {cert.not_valid_after_utc}"
        )
