"""Resolved source diagnostics as an automation-friendly sensor."""

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import slugify

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_add_entities([ActiveSourceSensor(hass.data[DOMAIN][entry.entry_id], entry)])


class ActiveSourceSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_translation_key = "active_source"
    _attr_icon = "mdi:multimedia"

    def __init__(self, runtime, entry) -> None:
        self._runtime = runtime
        self._attr_unique_id = f"{entry.unique_id}_active_source"
        self._attr_suggested_object_id = (
            f"{slugify(entry.unique_id or entry.entry_id)}_active_source"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)}, name=entry.title
        )

    @property
    def native_value(self) -> str:
        return self._runtime.selection.display_source or "none"

    @property
    def extra_state_attributes(self) -> dict:
        selection = self._runtime.selection
        state = self._runtime.display_state
        return {
            # HA reserves friendly_name for this sensor's own translated name.
            "active_source_name": (
                state.attributes.get("friendly_name") or state.entity_id
            )
            if state
            else None,
            "control_target": selection.control_target,
            "other_active_sources": list(selection.other_active_sources),
            "secondary_source": selection.secondary_source,
            "override": self._runtime.override,
            "hold_active": self._runtime.hold_active,
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._runtime.subscribe(self._updated))

    @callback
    def _updated(self) -> None:
        self.async_write_ha_state()
