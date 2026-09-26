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

import copy
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
from .presets import PRESETS, normalise_preset
from .signals import active_preset, resolve_field_policies, signal_overrides

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
            # Deep, not shallow: a shallow copy shares nested values (e.g. a
            # per-signal override dict) with the live entry, so mutating one
            # in place would write straight into entry.options before
            # `finish` ever runs — defeating "abandoning the flow persists
            # nothing" the moment a later step edits a nested value.
            # `entry.options` itself is a read-only `MappingProxyType`, which
            # `copy.deepcopy` cannot pickle directly, so it is unwrapped to a
            # plain dict first; the values underneath are ordinary dicts.
            stored = copy.deepcopy(dict(self.config_entry.options))
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
        """Resolve the edits in progress, without saving them.

        Firmware evidence is read from the same pending stand-in as the
        policies, not from `self.config_entry` — `assume_firmware_support` is
        itself a pending edit on the cost step, and reading it off the stored
        entry would show bounds that contradict what `finish` is about to
        push the moment the user toggles it and re-enters this step.
        """

        class _Pending:
            data = self.config_entry.data
            options = self.working

        pending = _Pending()
        return resolve_field_policies(pending, evidence_from_entry(pending))

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
                    # Read the pending choice first: re-entering this step
                    # after picking a preset but before `finish` must not
                    # revert to what is stored on the entry, or submitting
                    # the pre-filled form silently discards the pending
                    # choice — exactly what a frontend "Save" click does.
                    default=normalise_preset(
                        self.working.get(
                            CONF_INTERVAL_PRESET, active_preset(self.config_entry)
                        )
                    ),
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
        so this is the only point at which an edit reaches the vehicle. A
        copy is handed over rather than `self.working` itself: HA stores
        whatever object is passed here directly on `entry.options`, so
        without the copy a later mutation of this (still-alive) flow
        instance's working dict would silently rewrite the saved entry too.
        """
        return self.async_create_entry(title="", data=copy.deepcopy(self.working))
