"""Diagnostics with an explicit allowlist; never export art URLs or titles."""

import re

from .const import (
    CONF_HIDE_RADIO_STATION,
    CONF_HOLD_SECONDS,
    CONF_NAME,
    CONF_SHOW_SECONDARY,
    CONF_SLOTS,
    DEFAULT_HIDE_RADIO_STATION,
    DEFAULT_HOLD_SECONDS,
    DEFAULT_SHOW_SECONDARY,
    DOMAIN,
    entry_option,
)

REDACTED = "**REDACTED**"


def _entity(value):
    if value is None:
        return None
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"media_player\.[a-z0-9_]+", value)
        else REDACTED
    )


async def async_get_config_entry_diagnostics(hass, entry) -> dict:
    runtime = hass.data[DOMAIN][entry.entry_id]
    selection = runtime.selection
    fetch_status = hass.data[DOMAIN].get("image_fetch_status", {})

    def last_fetch(source):
        status = fetch_status.get(source)
        return (
            {key: status.get(key) for key in ("status", "reason", "route", "at")}
            if status
            else None
        )

    composition = hass.data[DOMAIN].get("composition_status", {}).get(entry.entry_id)
    last_composition = None
    if composition:
        last_composition = {
            key: composition.get(key)
            for key in ("status", "reason", "at", "fingerprint")
        }
        last_composition.update(
            {
                "primary": _entity(composition.get("primary")),
                "secondary": _entity(composition.get("secondary")),
            }
        )

    def state_info(entity_id):
        state = hass.states.get(entity_id) if entity_id else None
        if state is None:
            return None
        # These two enums cannot contain a token, title, or URL in this export.
        state_value = (
            state.state
            if state.state
            in {
                "playing",
                "buffering",
                "paused",
                "idle",
                "on",
                "off",
                "unknown",
                "unavailable",
            }
            else REDACTED
        )
        media_type = state.attributes.get("media_content_type")
        media_type = (
            media_type
            if isinstance(media_type, (str, type(None)))
            and media_type
            in {
                None,
                "music",
                "tvshow",
                "video",
                "movie",
                "channel",
                "playlist",
                "podcast",
                "game",
                "app",
            }
            else REDACTED
        )
        picture = bool(state.attributes.get("entity_picture"))
        return {
            "state": state_value,
            "media_content_type": media_type,
            "has_entity_picture": picture,
            "entity_picture": REDACTED if picture else None,
        }

    # Unknown options are redacted, not copied through from arbitrary entry data.
    options = {key: REDACTED for key in entry.options}
    options[CONF_NAME] = REDACTED
    options[CONF_SLOTS] = [
        {"source": _entity(source), "control": _entity(control)}
        for source, control in runtime.slots
    ]
    for key, default in (
        (CONF_SHOW_SECONDARY, DEFAULT_SHOW_SECONDARY),
        (CONF_HIDE_RADIO_STATION, DEFAULT_HIDE_RADIO_STATION),
    ):
        value = entry_option(entry, key, default)
        options[key] = value if isinstance(value, bool) else REDACTED
    hold = entry_option(entry, CONF_HOLD_SECONDS, DEFAULT_HOLD_SECONDS)
    options[CONF_HOLD_SECONDS] = (
        hold if isinstance(hold, (int, float)) and 0 <= hold <= 30 else REDACTED
    )
    return {
        "options": options,
        "last_composition": last_composition,
        "resolution": {
            "display_source": _entity(selection.display_source),
            "secondary_source": _entity(selection.secondary_source),
            "control_target": _entity(selection.control_target),
            "other_active_sources": [
                _entity(source) for source in selection.other_active_sources
            ],
            "cmp_state": selection.cmp_state,
            "override": _entity(runtime.override),
            "hold_active": runtime.hold_active,
        },
        "slots": [
            {
                "source": _entity(source),
                "control": _entity(control),
                "source_state": state_info(source),
                "control_state": state_info(control),
                "last_fetch": last_fetch(source),
                "control_last_fetch": last_fetch(control),
            }
            for source, control in runtime.slots
        ],
    }
