"""Restored per-entry priority override."""

from homeassistant.components.select import SelectEntity
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import slugify

from .const import DOMAIN

AUTOMATIC = "automatic"


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([PrioritySelect(hass.data[DOMAIN][entry.entry_id], entry)])


class PrioritySelect(SelectEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "override"
    _attr_icon = "mdi:sort-variant"

    def __init__(self, runtime, entry) -> None:
        self._runtime = runtime
        self._attr_unique_id = f"{entry.unique_id}_override"
        self._attr_suggested_object_id = (
            f"{slugify(entry.unique_id or entry.entry_id)}_override"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name=entry.title
        )

    def _choices(self) -> dict[str, str]:
        return {
            f"{(state.attributes.get('friendly_name') if state else None) or source} ({source})": source
            for source, _ in self._runtime.slots
            for state in [self.hass.states.get(source)]
        }

    @property
    def options(self) -> list[str]:
        return [AUTOMATIC, *self._choices()]

    @property
    def current_option(self) -> str:
        return next(
            (
                label
                for label, source in self._choices().items()
                if source == self._runtime.override
            ),
            AUTOMATIC,
        )

    @property
    def extra_state_attributes(self) -> dict:
        # Restore the stable ID rather than a friendly label that can change.
        return {"override_source": self._runtime.override}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            self._runtime.set_override(last.attributes.get("override_source"))
        self.async_on_remove(self._runtime.subscribe(self._updated))

    @callback
    def _updated(self) -> None:
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        if option not in self.options:
            raise ValueError("Unknown priority option")
        self._runtime.set_override(self._choices().get(option))
