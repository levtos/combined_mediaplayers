from __future__ import annotations

import hashlib
import json
from typing import Any

from homeassistant.components.media_player import (
    BrowseMedia,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.components.media_player.errors import BrowseError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry
from homeassistant.helpers.event import async_track_state_change_event

from .const import CONF_NAME, DOMAIN, slots_from_entry, tracked_entities
from .resolver import Resolution, resolve


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities
) -> None:
    async_add_entities([CombinedMediaPlayer(entry)], update_before_add=False)


class CombinedMediaPlayer(MediaPlayerEntity):
    """Media player that combines multiple sources with configurable priority order.

    The first source in the list has the highest priority. Among sources in the
    same activity tier (playing/buffering > paused), the lower index wins.
    Controls go to the optional control target of that slot, or its source.
    """

    _attr_should_poll = False
    _attr_has_entity_name = False
    _attr_icon = "mdi:television-play"

    def __init__(self, entry: ConfigEntry) -> None:
        try:
            MediaPlayerEntity.__init__(self)
        except TypeError:
            pass
        self._entry = entry
        self._attr_unique_id = entry.unique_id
        self._slots = slots_from_entry(entry)
        self._attr_name: str = entry.options.get(CONF_NAME) or entry.data.get(
            CONF_NAME, "Combined Media Player"
        )
        self._unsub: Any = None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.entry_id)},
            name=self._attr_name,
            manufacturer="Combined Media Player",
            model="Virtual Media Player",
        )

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._slots = slots_from_entry(self._entry)
        all_tracked = tracked_entities(self._slots)
        if all_tracked:
            self._unsub = async_track_state_change_event(
                self.hass,
                all_tracked,
                self._handle_state_change,
            )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        await super().async_will_remove_from_hass()

    @callback
    def _handle_state_change(self, event) -> None:
        self.async_write_ha_state()

    # ── Active source resolution ───────────────────────────────────────────────

    def _resolve(self) -> Resolution:
        states = {
            entity_id: state
            for entity_id in tracked_entities(self._slots)
            if (state := self.hass.states.get(entity_id)) is not None
        }
        return resolve(self._slots, states)

    def _active_state(self) -> State | None:
        source = self._resolve().display_source
        return self.hass.states.get(source) if source else None

    def _active_entity_id(self) -> str | None:
        return self._resolve().display_source

    def _control_state(self) -> State | None:
        target = self._resolve().control_target
        return self.hass.states.get(target) if target else None

    # ── Availability & state ───────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        """Always available – combined player never shows unavailable."""
        return True

    @property
    def state(self) -> MediaPlayerState:
        return MediaPlayerState(self._resolve().cmp_state)

    # ── Features ──────────────────────────────────────────────────────────────

    @property
    def supported_features(self) -> MediaPlayerEntityFeature:
        ctrl = self._control_state()
        try:
            features = MediaPlayerEntityFeature(
                int(ctrl.attributes.get("supported_features", 0)) if ctrl else 0
            )
        except (TypeError, ValueError):
            features = MediaPlayerEntityFeature(0)
        # BROWSE_MEDIA is a content-library capability, not a hardware capability.
        # Expose it whenever *any* configured source supports it so the browser
        # remains accessible regardless of which source is currently active or
        # whether the audio/control target (e.g. HomePods) supports browsing.
        browse_bit = int(MediaPlayerEntityFeature.BROWSE_MEDIA)
        for sid in tracked_entities(self._slots):
            s = self.hass.states.get(sid)
            if s is None:
                continue
            try:
                if int(s.attributes.get("supported_features", 0)) & browse_bit:
                    features |= MediaPlayerEntityFeature.BROWSE_MEDIA
                    break
            except (TypeError, ValueError):
                continue
        return features

    # ── Media attributes (proxied from active source) ──────────────────────────

    def _from_active(self, key: str, default: Any = None) -> Any:
        active = self._active_state()
        return active.attributes.get(key, default) if active else default

    def _from_control(self, key: str, default: Any = None) -> Any:
        """Read an attribute from the selected slot's control target."""
        ctrl = self._control_state()
        return ctrl.attributes.get(key, default) if ctrl else default

    @property
    def media_title(self) -> str | None:
        return self._from_active("media_title")

    @property
    def media_artist(self) -> str | None:
        return self._from_active("media_artist")

    @property
    def media_album_name(self) -> str | None:
        return self._from_active("media_album_name")

    @property
    def media_content_type(self) -> str | None:
        return self._from_active("media_content_type")

    @property
    def media_duration(self) -> int | None:
        return self._from_active("media_duration")

    @property
    def media_position(self) -> float | None:
        return self._from_active("media_position")

    @property
    def media_position_updated_at(self) -> Any:
        return self._from_active("media_position_updated_at")

    @property
    def media_series_title(self) -> str | None:
        return self._from_active("media_series_title")

    @property
    def media_season(self) -> str | None:
        return self._from_active("media_season")

    @property
    def media_episode(self) -> str | None:
        return self._from_active("media_episode")

    @property
    def app_name(self) -> str | None:
        return self._from_active("app_name")

    @property
    def media_content_id(self) -> Any:
        return self._from_active("media_content_id")

    @property
    def media_album_artist(self) -> Any:
        return self._from_active("media_album_artist")

    @property
    def media_track(self) -> Any:
        return self._from_active("media_track")

    @property
    def media_channel(self) -> Any:
        return self._from_active("media_channel")

    @property
    def media_playlist(self) -> Any:
        return self._from_active("media_playlist")

    @property
    def app_id(self) -> Any:
        return self._from_active("app_id")

    @property
    def volume_level(self) -> float | None:
        return self._from_control("volume_level")

    @property
    def is_volume_muted(self) -> bool | None:
        return self._from_control("is_volume_muted")

    @property
    def source(self) -> str | None:
        return self._from_control("source")

    @property
    def source_list(self) -> list[str] | None:
        return self._from_control("source_list")

    @property
    def shuffle(self) -> bool | None:
        return self._from_control("shuffle")

    @property
    def repeat(self) -> str | None:
        return self._from_control("repeat")

    # ── Cover art ─────────────────────────────────────────────────────────────

    @property
    def media_image_hash(self) -> str | None:
        """Invalidate HA's proxy on source, title or picture changes."""
        active = self._active_state()
        if active is None or not active.attributes.get("entity_picture"):
            return None
        fingerprint = json.dumps(
            [
                active.entity_id,
                active.attributes["entity_picture"],
                active.attributes.get("media_title"),
            ]
        )
        return hashlib.sha256(fingerprint.encode()).hexdigest()[:32]

    @property
    def media_image_url(self) -> str | None:
        """Expose absolute source pictures; HA still serves the cover proxy."""
        url = self._from_active("entity_picture")
        return (
            url
            if isinstance(url, str) and url.startswith(("http://", "https://"))
            else None
        )

    async def async_get_media_image(self) -> tuple[bytes | None, str | None]:
        """Fetch cover art from the paired CombinedCoverImage entity.

        image.py handles caching, entity_picture fallback, and last-known-good
        persistence.  Delegating here avoids duplicating that logic and ensures
        the media-player card and the image entity always show the same art.
        """
        er = async_get_entity_registry(self.hass)
        image_entity_id = er.async_get_entity_id(
            "image", DOMAIN, f"{self._entry.unique_id}_cover"
        )
        if image_entity_id:
            image_component = self.hass.data.get("image")
            if image_component is not None and hasattr(image_component, "get_entity"):
                entity = image_component.get_entity(image_entity_id)
                if entity is not None:
                    try:
                        data = await entity.async_image()
                        if data:
                            return data, getattr(
                                entity, "content_type", None
                            ) or "image/jpeg"
                    except Exception:
                        pass
        return None, None

    def _browse_entity_id(self) -> str | None:
        """Return the entity_id of the best source to delegate media browsing to.

        Selection order:
        1. The currently active display source, if it supports BROWSE_MEDIA.
        2. The currently selected control target, if it supports BROWSE_MEDIA.
        3. Any configured source or control (regardless of playback state) that
           supports BROWSE_MEDIA – first match wins.
        """
        browse_bit = int(MediaPlayerEntityFeature.BROWSE_MEDIA)

        def _supports_browse(entity_id: str) -> bool:
            s = self.hass.states.get(entity_id)
            if s is None:
                return False
            try:
                return bool(int(s.attributes.get("supported_features", 0)) & browse_bit)
            except (TypeError, ValueError):
                return False

        # Prefer whichever source is currently active and can browse
        for candidate in (self._active_entity_id(), self._resolve().control_target):
            if candidate and _supports_browse(candidate):
                return candidate

        # Fall back to any configured source that supports browsing
        seen: set[str] = set()
        for sid in [source for source, _ in self._slots] + [
            control for _, control in self._slots if control
        ]:
            if sid not in seen:
                seen.add(sid)
                if _supports_browse(sid):
                    return sid

        return None

    async def async_browse_media(
        self,
        media_content_type: str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        """Delegate media browsing to the best available source entity."""
        browse_id = self._browse_entity_id()
        if not browse_id:
            raise BrowseError("No source with BROWSE_MEDIA support found")
        component = self.hass.data.get("media_player")
        if component is None or not hasattr(component, "get_entity"):
            raise BrowseError("Media player component not available")
        entity = component.get_entity(browse_id)
        if entity is None:
            raise BrowseError(f"Source entity {browse_id!r} not found")
        return await entity.async_browse_media(media_content_type, media_content_id)

    @property
    def media_image_remotely_accessible(self) -> bool:
        """Always use HA's proxy and the paired cover entity/cache."""
        return False

    # ── Diagnostics ───────────────────────────────────────────────────────────

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        selection = self._resolve()
        return {
            "active_source": selection.display_source,
            "control_target": selection.control_target,
            "other_active_sources": list(selection.other_active_sources),
            "slots": [
                {"source": source, "control": control}
                for source, control in self._slots
            ],
        }

    # ── Controls (forwarded to active source) ─────────────────────────────────

    async def _call_active(self, service: str, **kwargs: Any) -> None:
        target = self._resolve().control_target
        if target is None:
            return
        await self.hass.services.async_call(
            "media_player",
            service,
            {"entity_id": target, **kwargs},
            blocking=True,
        )

    async def async_media_play(self) -> None:
        await self._call_active("media_play")

    async def async_media_pause(self) -> None:
        await self._call_active("media_pause")

    async def async_media_stop(self) -> None:
        await self._call_active("media_stop")

    async def async_media_next_track(self) -> None:
        await self._call_active("media_next_track")

    async def async_media_previous_track(self) -> None:
        await self._call_active("media_previous_track")

    async def async_set_volume_level(self, volume: float) -> None:
        await self._call_active("volume_set", volume_level=volume)

    async def async_volume_up(self) -> None:
        await self._call_active("volume_up")

    async def async_volume_down(self) -> None:
        await self._call_active("volume_down")

    async def async_mute_volume(self, mute: bool) -> None:
        await self._call_active("volume_mute", is_volume_muted=mute)

    async def async_media_seek(self, position: float) -> None:
        await self._call_active("media_seek", seek_position=position)

    async def async_play_media(
        self, media_type: str, media_id: str, **kwargs: Any
    ) -> None:
        await self._call_active(
            "play_media",
            media_content_type=media_type,
            media_content_id=media_id,
            **kwargs,
        )

    async def async_select_source(self, source: str) -> None:
        await self._call_active("select_source", source=source)

    async def async_set_shuffle(self, shuffle: bool) -> None:
        await self._call_active("shuffle_set", shuffle=shuffle)

    async def async_set_repeat(self, repeat: str) -> None:
        await self._call_active("repeat_set", repeat=repeat)

    async def async_turn_on(self) -> None:
        await self._call_active("turn_on")

    async def async_turn_off(self) -> None:
        await self._call_active("turn_off")

    async def async_toggle(self) -> None:
        await self._call_active("toggle")
