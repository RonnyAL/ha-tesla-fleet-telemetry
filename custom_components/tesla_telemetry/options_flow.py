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
from functools import lru_cache
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
    SIGNAL_INTERVAL_MAX,
)
from .cost import monthly_ceiling, monthly_floor, signals_to_cost
from .firmware import evidence_from_entry
from .presets import PRESETS, normalise_preset
from .signals import active_preset, resolve_field_policies, signal_overrides

MENU_OPTIONS = ["preset", "category", "signal", "cost", "finish"]


def category_slug(category: str | None) -> str:
    """A translation-safe key for one of Tesla's category names."""
    if category is None:
        return "uncategorised"
    return category.lower().replace(" ", "_")


@lru_cache(maxsize=1)
def _catalog_by_category() -> dict[str, list[str]]:
    """Every catalog signal, grouped by its documented category slug.

    Cached — the catalog never changes at runtime, but this was re-grouping
    and re-sorting all 251 signals on every call, twice per category step.
    Callers only read the returned dict; nothing mutates it.
    """
    from .signal_metadata import SIGNALS

    grouped: dict[str, list[str]] = {}
    for name, meta in SIGNALS.items():
        grouped.setdefault(category_slug(meta.category), []).append(name)
    for names in grouped.values():
        names.sort()
    return grouped


# Tesla: "MilesSinceReset and SelfDrivingMilesSinceReset may only be included
# by each other."
_MILES_PAIR = frozenset({"MilesSinceReset", "SelfDrivingMilesSinceReset"})


def validate_include_fields(
    signal: str, targets: list[str], enabled: set[str]
) -> str | None:
    """An error key for an invalid include selection, or None.

    Three rules. Two are Tesla's; the third — that a target must itself be
    enabled — is ours, because whether an unconfigured field can be
    piggybacked is undocumented and we stay inside what is known.

    The pair rule constrains who a miles-pair member may be included *by*
    when something is being included — it does not require it to include
    anything. Applying it to an empty selection made `MilesSinceReset` and
    `SelfDrivingMilesSinceReset` untunable at all, so it only runs when
    ``targets`` is non-empty.
    """
    for target in targets:
        if target == signal:
            return "include_self"
        if target not in enabled:
            return "include_disabled"
    if targets:
        if (signal in _MILES_PAIR) != bool(_MILES_PAIR & set(targets)):
            return "include_miles_pair"
        if signal in _MILES_PAIR and set(targets) - _MILES_PAIR:
            return "include_miles_pair"
    return None


class TeslaTelemetryOptionsFlow(OptionsFlow):
    """Menu-driven telemetry configuration."""

    def __init__(self) -> None:
        self._working: dict[str, Any] | None = None
        self._category: str = ""
        self._signal: str = ""

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

    def _pending_entry(self) -> Any:
        """A read-only stand-in for `self.config_entry` reflecting pending edits.

        `self.working` holds every edit made so far in this flow, including
        ones (like `assume_firmware_support` on the cost step) that other
        steps must see immediately rather than only after `finish`. Anything
        that needs to resolve policies or firmware evidence consistent with
        the in-progress flow should build it from this stand-in, not from
        `self.config_entry` directly.
        """

        class _Pending:
            data = self.config_entry.data
            options = self.working

        return _Pending()

    def _pending_policies(self) -> dict[str, Any]:
        """Resolve the edits in progress, without saving them.

        Firmware evidence is read from the same pending stand-in as the
        policies, not from `self.config_entry` — `assume_firmware_support` is
        itself a pending edit on the cost step, and reading it off the stored
        entry would show bounds that contradict what `finish` is about to
        push the moment the user toggles it and re-enters this step.
        """
        pending = self._pending_entry()
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

    async def async_step_category(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        grouped = _catalog_by_category()
        if user_input is not None:
            self._category = user_input["category"]
            return await self.async_step_category_edit()
        options = [
            selector.SelectOptionDict(
                value=slug, label=f"{slug.replace('_', ' ').title()} ({len(names)})"
            )
            for slug, names in sorted(grouped.items())
        ]
        schema = vol.Schema(
            {
                vol.Required("category"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )
        return self.async_show_form(step_id="category", data_schema=schema)

    async def async_step_category_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """One interval per signal in the chosen category. 0 disables.

        Only values that differ from what the row was rendered with are
        stored, so scrolling past a category does not silently pin 56
        intervals and make every future preset a no-op for them.
        """
        names = _catalog_by_category().get(self._category, [])
        effective = self._pending_policies()
        # A signal the required-delta gate drops from `effective` (unproven
        # firmware, e.g. SelfDrivingMilesSinceReset) is not the same thing as
        # a signal with no override at all: falling back to 0 for both makes
        # a real, non-zero pin indistinguishable from "disabled", and typing
        # 0 to actually disable it is then a no-op because it matches what
        # was already rendered. Render the stored pin instead when there is
        # one.
        rendered = {
            name: (
                effective[name].interval_seconds
                if name in effective
                else self.overrides.get(name, {}).get("interval_seconds", 0)
            )
            for name in names
        }

        if user_input is not None:
            for name in names:
                submitted = user_input.get(name)
                if submitted is None or int(submitted) == rendered[name]:
                    continue
                self.overrides.setdefault(name, {})["interval_seconds"] = int(
                    submitted
                )
            return await self.async_step_init()

        schema = vol.Schema(
            {
                vol.Optional(name, default=rendered[name]): vol.All(
                    vol.Coerce(int), vol.Range(min=0, max=SIGNAL_INTERVAL_MAX)
                )
                for name in names
            }
        )
        return self.async_show_form(
            step_id="category_edit",
            data_schema=schema,
            description_placeholders={
                "category": self._category.replace("_", " ").title()
            },
        )

    async def async_step_signal(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        from .signal_metadata import SIGNALS

        if user_input is not None:
            self._signal = user_input["signal"]
            return await self.async_step_signal_edit()
        # A plain dropdown: Home Assistant's frontend filters it as the user
        # types, which is the search this form needs over the 251-entry
        # catalog (``len(SIGNALS)``).
        schema = vol.Schema(
            {
                vol.Required("signal"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=sorted(SIGNALS),
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        custom_value=False,
                    )
                )
            }
        )
        return self.async_show_form(step_id="signal", data_schema=schema)

    async def async_step_signal_edit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        from .signal_metadata import SIGNALS

        name = self._signal
        meta = SIGNALS[name]
        policies = self._pending_policies()
        current = policies.get(name)
        # The raw, un-gated override — not `current`, which is `policies[name]`
        # *after* the firmware gate has nulled minimum_delta,
        # resend_interval_seconds and include_fields for a car that hasn't
        # proven support. Pre-filling from the gated view and then writing
        # back whatever the form submits would silently delete a value the
        # user already typed the moment they reopen this step: the resolved
        # policy shows 0/[] for those three keys, the form pre-fills 0/[],
        # and submitting that pre-filled form overwrites the stored override
        # with the zeroed-out values. `interval_seconds` is never gated, so
        # it still comes from the resolved policy.
        raw_override = self.overrides.get(name, {})
        # Read from the same pending stand-in `_pending_policies` uses above,
        # not `self.config_entry` — `assume_firmware_support` can itself be a
        # pending edit from the cost step, and reading it off the stored
        # entry would keep showing stale gating notes until `finish`.
        evidence = evidence_from_entry(self._pending_entry())
        # What the interval field is about to be pre-filled with. Same M2
        # fallback as `category_edit`: a signal the required-delta gate drops
        # from `policies` still renders its stored pin, not 0, or it becomes
        # indistinguishable from "disabled" and cannot be told apart from an
        # unchanged submission below.
        rendered_interval = (
            current.interval_seconds
            if current is not None
            else raw_override.get("interval_seconds", 0)
        )

        if user_input is not None:
            targets = list(user_input.get("include_fields") or [])
            interval = int(user_input["interval_seconds"])
            resend = int(user_input.get("resend_interval_seconds") or 0)
            error_field = "include_fields"
            error = validate_include_fields(name, targets, set(policies))
            if error is None and resend and resend < interval:
                # A resend shorter than the interval can never fire: the
                # interval is how often the vehicle is allowed to send at
                # all, so a shorter resend is a promise nothing can keep.
                # cost.py defends its own arithmetic with max(resend,
                # interval); refusing it here means the number the user
                # typed is never silently reinterpreted.
                error = "resend_shorter_than_interval"
                error_field = "resend_interval_seconds"
            if error is not None:
                return self.async_show_form(
                    step_id="signal_edit",
                    data_schema=self._signal_schema(name, user_input),
                    errors={error_field: error},
                    description_placeholders=self._signal_placeholders(
                        name, meta, evidence
                    ),
                )
            # Only a value that differs from what the form rendered becomes a
            # pin — mirroring `interval_seconds`'s three states (0 disabled,
            # absent inherits the preset, positive pins). Storing whatever
            # was submitted unconditionally pins the preset's own resolved
            # value the instant any *other* field on this signal is edited,
            # permanently defeating every future preset for it (this was
            # Important 1: an entry on `eco`, opening a signal that had never
            # been touched, and saving after changing only `minimum_delta`
            # pinned the preset-resolved interval forever). An unchanged
            # value that was already an explicit pin stays pinned — it is not
            # a *new* pin, just a resubmission of an existing one.
            stored: dict[str, Any] = {}
            if interval != rendered_interval:
                stored["interval_seconds"] = interval
            elif "interval_seconds" in raw_override:
                stored["interval_seconds"] = raw_override["interval_seconds"]
            delta = user_input.get("minimum_delta")
            if delta:
                stored["minimum_delta"] = float(delta)
            if resend:
                stored["resend_interval_seconds"] = resend
            if targets:
                stored["include_fields"] = targets
            # `stored` can legitimately be `{}` now that an unchanged
            # `interval_seconds` is omitted (see above) -- and unlike
            # `category_edit`, this form always assigns a whole dict rather
            # than one field, so an empty `stored` for a signal that was
            # never in `self.overrides` before must not create a row at all,
            # or a no-op visit to any not-currently-enabled signal silently
            # enables it (`resolve_field_policies` treats a bare `{}` entry
            # as "enabled, inherit"). An existing row must still be
            # writable to `{}`, though -- that is how a pinned signal
            # legitimately reverts to inherit -- so the guard only skips the
            # write when there was nothing there to begin with.
            if stored or name in self.overrides:
                self.overrides[name] = stored
            return await self.async_step_init()

        defaults = {
            "interval_seconds": rendered_interval,
            "minimum_delta": raw_override.get("minimum_delta") or 0,
            "resend_interval_seconds": raw_override.get(
                "resend_interval_seconds"
            )
            or 0,
            "include_fields": list(raw_override.get("include_fields") or []),
        }
        return self.async_show_form(
            step_id="signal_edit",
            data_schema=self._signal_schema(name, defaults),
            description_placeholders=self._signal_placeholders(
                name, meta, evidence
            ),
        )

    def _signal_schema(self, name: str, defaults: dict[str, Any]) -> vol.Schema:
        from .signal_metadata import SIGNALS

        return vol.Schema(
            {
                vol.Required(
                    "interval_seconds",
                    default=int(defaults.get("interval_seconds") or 0),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=SIGNAL_INTERVAL_MAX)),
                vol.Optional(
                    "minimum_delta",
                    default=float(defaults.get("minimum_delta") or 0),
                ): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Optional(
                    "resend_interval_seconds",
                    default=int(defaults.get("resend_interval_seconds") or 0),
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=SIGNAL_INTERVAL_MAX)),
                vol.Optional(
                    "include_fields",
                    default=list(defaults.get("include_fields") or []),
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[s for s in sorted(SIGNALS) if s != name],
                        multiple=True,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                        custom_value=False,
                    )
                ),
            }
        )

    def _signal_placeholders(
        self, name: str, meta: Any, evidence: Any
    ) -> dict[str, str]:
        """Explain the delta's unit, Tesla's own default, and any gating.

        A blank delta field does not mean "no delta" — the car applies one of
        its own to ChargerVoltage and Odometer — and a value typed while the
        floor is unmet is stored but withheld. Both need saying, here, or the
        setting looks broken.
        """
        from .firmware import FLOOR_INCLUDE_FIELDS, FLOOR_MINIMUM_DELTA

        # Tesla measures location deltas in metres; everything else uses the
        # signal's own unit.
        unit = "m" if meta.value_type == "Location" else (meta.unit or "")
        notes: list[str] = []
        if meta.minimum_delta_required is not None:
            notes.append(
                f"Tesla requires a minimum delta of at least "
                f"{meta.minimum_delta_required} for this field; without one it "
                f"never reports."
            )
        # "default" and "recommended" are independent facts Tesla documents
        # about the same field (e.g. ChargerVoltage: the car already applies
        # 0.3 *and* Tesla recommends setting one), so both are shown when
        # both are true. "recommended" and "supported" are mutually
        # exclusive: "supported" is Tesla's weaker claim — a delta is merely
        # possible, with no advice either way — so it is only said when
        # "recommended" was not already the stronger, true claim.
        if meta.minimum_delta_default is not None:
            notes.append(
                f"The car already applies a default minimum delta of "
                f"{meta.minimum_delta_default} on recent firmware."
            )
        if meta.minimum_delta_recommended:
            notes.append("Tesla recommends setting a minimum delta for this field.")
        elif meta.minimum_delta_supported:
            # Weaker than "recommended": Tesla documents this only as
            # possible (e.g. Location — "specifying minimum delta for
            # location values is possible") and never advises setting one.
            # Saying "recommends" here would invent vendor guidance Tesla
            # never gave.
            notes.append(
                "Tesla says specifying a minimum delta is possible for this "
                "field, though it does not recommend one."
            )
        if not evidence.supports(FLOOR_MINIMUM_DELTA):
            notes.append(
                "Your car has not yet confirmed firmware "
                f"{FLOOR_MINIMUM_DELTA}, so minimum delta and resend interval "
                "are stored but not sent until it does."
            )
        if not evidence.supports(FLOOR_INCLUDE_FIELDS):
            notes.append(
                "Included fields need firmware "
                f"{FLOOR_INCLUDE_FIELDS}, which your car has not confirmed."
            )
        return {
            "signal": name,
            "unit": unit or "—",
            "notes": " ".join(notes) or "No special requirements.",
        }
