from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.translation import async_get_translations

from .const import (
    CONF_HIDE_RADIO_STATION,
    CONF_HOLD_SECONDS,
    CONF_NAME,
    CONF_ROTATION_SECONDS,
    CONF_SHOW_OTHER_SOURCES,
    CONF_SLOTS,
    CONF_SOURCES,
    DEFAULT_HIDE_RADIO_STATION,
    DEFAULT_HOLD_SECONDS,
    DEFAULT_ROTATION_SECONDS,
    DEFAULT_SHOW_OTHER_SOURCES,
    DOMAIN,
    entry_option,
    slots_from_entry,
    slot_settings_from_entry,
)
from .slot_config import make_slots, settings_by_source


def _settings(form: dict) -> dict:
    return {
        CONF_ROTATION_SECONDS: form.get(
            CONF_ROTATION_SECONDS, DEFAULT_ROTATION_SECONDS
        ),
        CONF_SHOW_OTHER_SOURCES: form.get(
            CONF_SHOW_OTHER_SOURCES, DEFAULT_SHOW_OTHER_SOURCES
        ),
        CONF_HIDE_RADIO_STATION: form.get(
            CONF_HIDE_RADIO_STATION, DEFAULT_HIDE_RADIO_STATION
        ),
        CONF_HOLD_SECONDS: form.get(CONF_HOLD_SECONDS, DEFAULT_HOLD_SECONDS),
    }


def _source_schema(values: dict) -> vol.Schema:
    settings = _settings(values)
    return vol.Schema(
        {
            vol.Required(
                CONF_NAME, default=values.get(CONF_NAME, "")
            ): selector.TextSelector(),
            vol.Optional(
                CONF_ROTATION_SECONDS, default=settings[CONF_ROTATION_SECONDS]
            ): _duration_selector(),
            vol.Optional(
                CONF_SHOW_OTHER_SOURCES, default=settings[CONF_SHOW_OTHER_SOURCES]
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_HIDE_RADIO_STATION, default=settings[CONF_HIDE_RADIO_STATION]
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_HOLD_SECONDS, default=settings[CONF_HOLD_SECONDS]
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0,
                    max=30,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                    unit_of_measurement="s",
                )
            ),
            vol.Required(
                CONF_SOURCES, default=values.get(CONF_SOURCES, [])
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="media_player", multiple=True, reorder=True
                )
            ),
        }
    )


def _duration_selector():
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=5,
            max=600,
            step=1,
            mode=selector.NumberSelectorMode.BOX,
            unit_of_measurement="s",
        )
    )


async def _control_schema(flow, sources: list[str], settings: dict) -> vol.Schema:
    # HA looks up field translations by static paths. These source-bound fields
    # have no fixed count: use translated captions as their fallback field names.
    translations = await async_get_translations(
        flow.hass,
        flow.context.get("language") or flow.hass.config.language,
        "config",
        {DOMAIN},
    )
    prefix = f"component.{DOMAIN}.config.step.controls.data."
    labels = {
        "control": translations.get(prefix + "control", "Control via (optional)"),
        "display_seconds": translations.get(
            prefix + "display_seconds", "Display duration (seconds)"
        ),
    }
    fields = {}
    flow._source_fields = {}
    for source in sources:
        state = flow.hass.states.get(source)
        name = (state.attributes.get("friendly_name") if state else None) or source
        for setting, label in labels.items():
            key = f"{label} — {name} ({source})"
            flow._source_fields[(source, setting)] = key
            value = settings.get(source, {}).get(setting)
            marker = (
                vol.Optional(key, description={"suggested_value": value})
                if value is not None
                else vol.Optional(key)
            )
            fields[marker] = (
                selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="media_player")
                )
                if setting == "control"
                else _duration_selector()
            )
    return vol.Schema(fields)


def _submitted_slots(flow, sources, user_input):
    controls, durations = {}, {}
    for source in sources:
        controls[source] = user_input.get(flow._source_fields[(source, "control")])
        durations[source] = user_input.get(
            flow._source_fields[(source, "display_seconds")]
        )
    return make_slots(sources, controls, durations)


def _source_description(hass, sources: list[str]) -> dict[str, str]:
    descriptions = []
    for source in sources:
        state = hass.states.get(source)
        name = (state.attributes.get("friendly_name") if state else None) or source
        descriptions.append(f"- {name} (`{source}`)")
    return {"sources": "\n".join(descriptions)}


def _input_errors(form: dict) -> dict[str, str]:
    if not form.get(CONF_NAME, "").strip():
        return {CONF_NAME: "name_required"}
    if not form.get(CONF_SOURCES):
        return {CONF_SOURCES: "sources_required"}
    return {}


class CombinedMediaPlayerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 2
    MINOR_VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors = _input_errors(user_input) if user_input is not None else {}
        if user_input is not None and not errors:
            name = user_input[CONF_NAME].strip()
            await self.async_set_unique_id(f"combined_{name.lower().replace(' ', '_')}")
            self._abort_if_unique_id_configured()
            self._pending = {
                **user_input,
                CONF_NAME: name,
                CONF_SOURCES: list(dict.fromkeys(user_input[CONF_SOURCES])),
            }
            return await self.async_step_controls()
        return self.async_show_form(
            step_id="user",
            data_schema=_source_schema(user_input or {}),
            errors=errors,
            last_step=False,
        )

    async def async_step_controls(self, user_input: dict[str, Any] | None = None):
        if not hasattr(self, "_pending"):
            return await self.async_step_user()
        sources = self._pending[CONF_SOURCES]
        if user_input is not None:
            return self.async_create_entry(
                title=self._pending[CONF_NAME],
                data={CONF_NAME: self._pending[CONF_NAME]},
                options={
                    CONF_SLOTS: _submitted_slots(self, sources, user_input),
                    **_settings(self._pending),
                },
            )
        return self.async_show_form(
            step_id="controls",
            data_schema=await _control_schema(self, sources, {}),
            description_placeholders=_source_description(self.hass, sources),
            last_step=True,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return CombinedMediaPlayerOptionsFlow()


class CombinedMediaPlayerOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        slots = slots_from_entry(self.config_entry)
        errors = _input_errors(user_input) if user_input is not None else {}
        if user_input is not None and not errors:
            self._pending = {
                **user_input,
                CONF_NAME: user_input[CONF_NAME].strip(),
                CONF_SOURCES: list(dict.fromkeys(user_input[CONF_SOURCES])),
            }
            self._source_settings = settings_by_source(
                slot_settings_from_entry(self.config_entry)
            )
            return await self.async_step_controls()
        values = (
            user_input
            if user_input is not None
            else {
                CONF_NAME: self.config_entry.options.get(CONF_NAME)
                or self.config_entry.data.get(CONF_NAME, ""),
                CONF_SOURCES: list(dict.fromkeys(source for source, _ in slots)),
                **{
                    key: entry_option(self.config_entry, key, default)
                    for key, default in (
                        (CONF_ROTATION_SECONDS, DEFAULT_ROTATION_SECONDS),
                        (CONF_SHOW_OTHER_SOURCES, DEFAULT_SHOW_OTHER_SOURCES),
                        (CONF_HIDE_RADIO_STATION, DEFAULT_HIDE_RADIO_STATION),
                        (CONF_HOLD_SECONDS, DEFAULT_HOLD_SECONDS),
                    )
                },
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=_source_schema(values),
            errors=errors,
            last_step=False,
        )

    async def async_step_controls(self, user_input: dict[str, Any] | None = None):
        if not hasattr(self, "_pending"):
            return await self.async_step_init()
        sources = self._pending[CONF_SOURCES]
        if user_input is not None:
            name = self._pending[CONF_NAME]
            if name != self.config_entry.title:
                self.hass.config_entries.async_update_entry(
                    self.config_entry, title=name
                )
            return self.async_create_entry(
                title="",
                data={
                    CONF_NAME: name,
                    CONF_SLOTS: _submitted_slots(self, sources, user_input),
                    **_settings(self._pending),
                },
            )
        return self.async_show_form(
            step_id="controls",
            data_schema=await _control_schema(self, sources, self._source_settings),
            description_placeholders=_source_description(self.hass, sources),
            last_step=True,
        )
