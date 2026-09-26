"""The per-vehicle telemetry options flow.

Lifted out of `config_flow.py`, which is OAuth and should not also own a
five-step form.

Tesla's categories are wildly uneven — Charging alone has 56 signals — and
Phase 4 adds three more keys per signal. A single form would be roughly a
thousand voluptuous fields, which is not a layout problem but a dead form. So
the flow splits along the seam that actually exists: bulk editing wants one
number per signal, per-field tuning wants four fields for one signal.

Every step accumulates into `self._working` and returns to the menu. Nothing
is written until `finish`, so abandoning the flow persists nothing — which
matters more here than in most flows, because saving pushes a new
configuration to the vehicle.
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlowResult, OptionsFlow
from homeassistant.helpers import selector

from .const import (
    CONF_ASSUME_FIRMWARE_SUPPORT,
    CONF_COST_PER_MILLION_SIGNALS,
    CONF_INTERVAL_PRESET,
    CONF_SIGNAL_OVERRIDES,
    DEFAULT_COST_PER_MILLION_SIGNALS,
)
from .cost import monthly_ceiling, monthly_floor, signals_to_cost
from .firmware import evidence_from_entry
from .presets import PRESETS
from .signals import active_preset, resolve_field_policies, signal_overrides

_LOGGER = logging.getLogger(__name__)

MENU_OPTIONS = ["preset", "category", "signal", "cost", "finish"]


class TeslaTelemetryOptionsFlow(OptionsFlow):
    """Menu-driven telemetry configuration."""

    def __init__(self) -> None:
        self._working: dict[str, Any] | None = None

    # -------------------- shared state --------------------
    @property
    def working(self) -> dict[str, Any]:
        """The options being edited, seeded from what is stored.

        Seeded lazily rather than in __init__ because `self.config_entry` is
        not available until Home Assistant has attached it to the flow.
        """
        if self._working is None:
            stored = dict(self.config_entry.options)
            # Normalise the override shape once, here, so every step below
            # works with dicts and the legacy bare-int form disappears the
            # first time anything is saved.
            stored[CONF_SIGNAL_OVERRIDES] = {
                name: dict(value)
                for name, value in signal_overrides(self.config_entry).items()
            }
            self._working = stored
        return self._working

    @property
    def overrides(self) -> dict[str, dict[str, Any]]:
        return self.working.setdefault(CONF_SIGNAL_OVERRIDES, {})

    def _currency(self) -> str:
        return (self.hass.config.currency if self.hass else None) or "USD"

    def _rate(self) -> float:
        return float(
            self.working.get(
                CONF_COST_PER_MILLION_SIGNALS, DEFAULT_COST_PER_MILLION_SIGNALS
            )
        )

    def _pending_policies(self) -> dict[str, Any]:
        """Resolve the edits in progress, without saving them."""

        class _Pending:
            data = self.config_entry.data
            options = self.working

        return resolve_field_policies(
            _Pending(), evidence_from_entry(self.config_entry)
        )

    # -------------------- steps --------------------
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=MENU_OPTIONS)

    async def async_step_preset(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self.working[CONF_INTERVAL_PRESET] = user_input[CONF_INTERVAL_PRESET]
            return await self.async_step_init()
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_INTERVAL_PRESET,
                    default=active_preset(self.config_entry),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=list(PRESETS),
                        mode=selector.SelectSelectorMode.LIST,
                        translation_key="interval_preset",
                    )
                )
            }
        )
        return self.async_show_form(step_id="preset", data_schema=schema)

    async def async_step_cost(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self.working[CONF_COST_PER_MILLION_SIGNALS] = float(
                user_input[CONF_COST_PER_MILLION_SIGNALS]
            )
            self.working[CONF_ASSUME_FIRMWARE_SUPPORT] = bool(
                user_input[CONF_ASSUME_FIRMWARE_SUPPORT]
            )
            return await self.async_step_init()

        policies = self._pending_policies()
        rate = self._rate()
        currency = self._currency()
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_COST_PER_MILLION_SIGNALS, default=rate
                ): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Required(
                    CONF_ASSUME_FIRMWARE_SUPPORT,
                    default=bool(
                        self.working.get(CONF_ASSUME_FIRMWARE_SUPPORT, False)
                    ),
                ): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="cost",
            data_schema=schema,
            description_placeholders={
                "ceiling": f"{currency} "
                f"{signals_to_cost(monthly_ceiling(policies), rate):.2f}",
                "floor": f"{currency} "
                f"{signals_to_cost(monthly_floor(policies), rate):.2f}",
                "signals": str(len(policies)),
            },
        )

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Write everything at once.

        The entry's update listener re-pushes the telemetry config from here,
        so this is the only point at which an edit reaches the vehicle.
        """
        return self.async_create_entry(title="", data=self.working)
