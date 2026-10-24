from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import selector

from .const import CONF_NAME, CONF_SLOTS, DOMAIN, slots_from_entry

_NUM_SLOTS = 8


def _form_slots(form: dict) -> list[dict[str, str | None]]:
    """Keep slot order and the first occurrence of each source."""
    seen: set[str] = set()
    result = []
    keys = sorted(
        (key for key in form if key.startswith("slot_")),
        key=lambda key: int(key.removeprefix("slot_")),
    )
    for key in keys:
        source = form[key].get("source")
        if source and source not in seen:
            seen.add(source)
            result.append(
                {"source": source, "control": form[key].get("control") or None}
            )
    return result


def _schema(hass, name: str = "", slots=(), prefill: dict | None = None) -> vol.Schema:
    """Pair source and control selectors inside a native HA section."""
    entity_options = [selector.SelectOptionDict(value="", label="—")]
    entity_options.extend(
        selector.SelectOptionDict(
            value=state.entity_id,
            label=f"{state.attributes.get('friendly_name') or state.entity_id} ({state.entity_id})",
        )
        for state in sorted(
            hass.states.async_all("media_player"),
            key=lambda state: (
                (state.attributes.get("friendly_name") or state.entity_id).lower(),
                state.entity_id,
            ),
        )
    )
    known = {option["value"] for option in entity_options}
    # Preserve saved IDs while their integration is unloaded.
    for source, control in slots:
        for entity_id in (source, control):
            if entity_id and entity_id not in known:
                entity_options.append(
                    selector.SelectOptionDict(value=entity_id, label=entity_id)
                )
                known.add(entity_id)
    entity_selector = selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=entity_options,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )
    fields = {
        vol.Required(
            CONF_NAME, default=(prefill or {}).get(CONF_NAME, name)
        ): selector.TextSelector(),
    }
    for index in range(max(_NUM_SLOTS, len(slots))):
        key = f"slot_{index + 1}"
        source, control = slots[index] if index < len(slots) else ("", None)
        values = (
            prefill.get(key, {})
            if prefill is not None
            else {
                "source": source,
                "control": control or "",
            }
        )
        fields[vol.Required(key)] = section(
            vol.Schema(
                {
                    vol.Optional(
                        "source", default=values.get("source", "")
                    ): entity_selector,
                    vol.Optional(
                        "control", default=values.get("control", "")
                    ): entity_selector,
                }
            ),
            {"collapsed": index > 0},
        )
    return vol.Schema(fields)


class CombinedMediaPlayerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 2
    MINOR_VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors = {}
        if user_input is not None:
            name = user_input.get(CONF_NAME, "").strip()
            slots = _form_slots(user_input)
            if not name:
                errors[CONF_NAME] = "name_required"
            elif not slots:
                errors["base"] = "sources_required"
            else:
                await self.async_set_unique_id(
                    f"combined_{name.lower().replace(' ', '_')}"
                )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=name,
                    data={CONF_NAME: name},
                    options={CONF_SLOTS: slots},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=_schema(self.hass, prefill=user_input),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return CombinedMediaPlayerOptionsFlow()


class CombinedMediaPlayerOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        errors = {}
        if user_input is not None:
            name = user_input.get(CONF_NAME, "").strip()
            slots = _form_slots(user_input)
            if not name:
                errors[CONF_NAME] = "name_required"
            elif not slots:
                errors["base"] = "sources_required"
            else:
                if name != self.config_entry.title:
                    self.hass.config_entries.async_update_entry(
                        self.config_entry, title=name
                    )
                return self.async_create_entry(
                    title="",
                    data={CONF_NAME: name, CONF_SLOTS: slots},
                )
        name = self.config_entry.options.get(CONF_NAME) or self.config_entry.data.get(
            CONF_NAME, ""
        )
        return self.async_show_form(
            step_id="init",
            data_schema=_schema(
                self.hass, name, slots_from_entry(self.config_entry), user_input
            ),
            errors=errors,
        )
