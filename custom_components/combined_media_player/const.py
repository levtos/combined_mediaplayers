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
CONF_ROTATION_SECONDS = "rotation_seconds"
CONF_SHOW_OTHER_SOURCES = "show_other_sources"
CONF_HIDE_RADIO_STATION = "hide_radio_station"
CONF_HOLD_SECONDS = "hold_seconds"
DEFAULT_ROTATION_SECONDS = 30
DEFAULT_SHOW_OTHER_SOURCES = True
DEFAULT_HIDE_RADIO_STATION = True
DEFAULT_HOLD_SECONDS = 5


def entry_option(entry: Any, key: str, default: Any) -> Any:
    return entry.options.get(key, entry.data.get(key, default))


def slots_from_entry(entry: Any) -> list[tuple[str, str | None]]:
    """Read slots with a compatibility fallback for legacy entries."""
    return [
        (slot["source"], slot.get("control") or None)
        for slot in slot_settings_from_entry(entry)
    ]


def slot_settings_from_entry(entry: Any) -> list[dict]:
    """Keep optional per-source display durations without changing resolver slots."""
    from .slot_config import settings_by_source

    for config in (entry.options, entry.data):
        if CONF_SLOTS in config:
            return list(settings_by_source(config[CONF_SLOTS]).values())
    sources = entry.options.get(CONF_SOURCES) or entry.data.get(CONF_SOURCES, [])
    return [{"source": source, "control": None} for source in dict.fromkeys(sources)]


def tracked_entities(slots: list[tuple[str, str | None]]) -> list[str]:
    """Track each source and configured control once, in slot order."""
    return list(dict.fromkeys(entity for slot in slots for entity in slot if entity))
