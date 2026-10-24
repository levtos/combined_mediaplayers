from __future__ import annotations

from typing import Any

from homeassistant.const import Platform

DOMAIN = "combined_media_player"

PLATFORMS: list[Platform] = [
    Platform.MEDIA_PLAYER,
    Platform.IMAGE,
    Platform.SELECT,
    Platform.SENSOR,
]

CONF_NAME = "name"
CONF_SOURCES = "sources"
CONF_AUDIO_SOURCES = "audio_sources"
CONF_SLOTS = "slots"
CONF_SHOW_SECONDARY = "show_secondary"
CONF_HIDE_RADIO_STATION = "hide_radio_station"
CONF_HOLD_SECONDS = "hold_seconds"
DEFAULT_SHOW_SECONDARY = True
DEFAULT_HIDE_RADIO_STATION = True
DEFAULT_HOLD_SECONDS = 5


def entry_option(entry: Any, key: str, default: Any) -> Any:
    return entry.options.get(key, entry.data.get(key, default))


def slots_from_entry(entry: Any) -> list[tuple[str, str | None]]:
    """Read slots with a compatibility fallback for legacy entries."""
    for config in (entry.options, entry.data):
        if CONF_SLOTS in config:
            return [
                (slot["source"], slot.get("control") or None)
                for slot in config[CONF_SLOTS]
            ]
    sources = entry.options.get(CONF_SOURCES) or entry.data.get(CONF_SOURCES, [])
    return [(source, None) for source in sources]


def tracked_entities(slots: list[tuple[str, str | None]]) -> list[str]:
    """Track each source and configured control once, in slot order."""
    return list(dict.fromkeys(entity for slot in slots for entity in slot if entity))
