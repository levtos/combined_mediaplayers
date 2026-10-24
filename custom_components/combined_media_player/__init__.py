from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_AUDIO_SOURCES,
    CONF_SLOTS,
    CONF_SOURCES,
    DOMAIN,
    PLATFORMS,
    slots_from_entry,
)
from .runtime import EntryRuntime

_LOGGER = logging.getLogger(__name__)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate ordered sources to slots without changing entity identities."""
    if entry.version > 2:
        return False
    if entry.version == 1:
        slots = [
            {"source": source, "control": control}
            for source, control in slots_from_entry(entry)
        ]
        data, options = dict(entry.data), dict(entry.options)
        had_audio_sources = any(
            CONF_AUDIO_SOURCES in config for config in (data, options)
        )
        for config in (data, options):
            config.pop(CONF_SOURCES, None)
            config.pop(CONF_AUDIO_SOURCES, None)
            config.pop(CONF_SLOTS, None)
        options[CONF_SLOTS] = slots
        hass.config_entries.async_update_entry(
            entry,
            data=data,
            options=options,
            version=2,
            minor_version=1,
        )
        if had_audio_sources:
            _LOGGER.info(
                "Removed legacy audio_sources during slot migration; "
                "configure optional control targets per slot"
            )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime = EntryRuntime(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    runtime.start()
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        runtime.stop()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id).stop()
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
