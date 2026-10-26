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

from .const import (
    CONF_HIDE_RADIO_STATION,
    CONF_NAME,
    CONF_SHOW_OTHER_SOURCES,
    DEFAULT_HIDE_RADIO_STATION,
    DEFAULT_SHOW_OTHER_SOURCES,
    DOMAIN,
    entry_option,
    slots_from_entry,
    tracked_entities,
)
from .image import async_fetch_source_image
from .resolver import Resolution, is_radio
from .rotation import other_sources_line


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
        self._show_other_sources = entry_option(
            entry, CONF_SHOW_OTHER_SOURCES, DEFAULT_SHOW_OTHER_SOURCES
        )
        self._hide_radio = entry_option(
            entry, CONF_HIDE_RADIO_STATION, DEFAULT_HIDE_RADIO_STATION
        )

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
        self._unsub = self._runtime.subscribe(self._handle_state_change)
        self._runtime.start_rotation()

    async def async_will_remove_from_hass(self) -> None:
        self._runtime.stop_rotation()
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        await super().async_will_remove_from_hass()

    @callback
    def _handle_state_change(self) -> None:
        self.async_write_ha_state()

    # ── Active source resolution ───────────────────────────────────────────────

    @property
    def _runtime(self):
        return self.hass.data[DOMAIN][self._entry.entry_id]

    def _resolve(self) -> Resolution:
        return self._runtime.selection

    def _active_state(self) -> State | None:
        return self._runtime.display_state

    def _active_entity_id(self) -> str | None:
        return self._resolve().display_source

    def _control_state(self) -> State | None:
        target = self._resolve().control_target
        if self._runtime.hold_active and target == self._resolve().display_source:
            return self._active_state()
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
        if self._show_other_sources:
            line = other_sources_line(
                self._runtime.rotation.sources,
                self._active_entity_id(),
                {
                    source: self.hass.states.get(source)
                    for source in self._runtime.rotation.sources
                },
            )
            if line:
                return line
        if self._hide_radio and is_radio(self._from_active("media_content_id")):
            return None
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

    def _shown_state(self) -> State | None:
        source = self._runtime.rotation.shown_source
        return (
            self._active_state()
            if source == self._active_entity_id()
            else self.hass.states.get(source)
            if source
            else None
        )

    @property
    def media_image_hash(self) -> str | None:
        """Invalidate the proxy on rotation, title, picture or fallback changes."""
        shown, active = self._shown_state(), self._active_state()
        if shown is None and active is None:
            return None

        def fingerprint(state):
            return (
                [
                    state.entity_id,
                    state.attributes.get("entity_picture"),
                    state.attributes.get("media_title"),
                ]
                if state
                else None
            )

        # Also include the fixed main cover: a missing rotating image falls back
        # to it, and a main title/picture change must invalidate that response.
        value = json.dumps([fingerprint(shown), fingerprint(active)])
        return hashlib.sha256(value.encode()).hexdigest()[:32]

    @property
    def media_image_url(self) -> str | None:
        """HA serves the rotating cover through its own proxy."""
        shown = self._shown_state()
        url = shown.attributes.get("entity_picture") if shown else None
        return (
            url
            if isinstance(url, str) and url.startswith(("http://", "https://"))
            else None
        )

    async def async_get_media_image(self) -> tuple[bytes | None, str | None]:
        """Fetch the shown cover through validation; fall back to the pure main cover."""
        fingerprint = self.media_image_hash

        def is_current():
            return self.media_image_hash == fingerprint

        shown = self._shown_state()
        if shown and shown.entity_id != self._active_entity_id():
            image = await async_fetch_source_image(self.hass, shown, is_current)
            if not is_current():
                return None, None
            if image:
                return image

        registry = async_get_entity_registry(self.hass)
        image_id = registry.async_get_entity_id(
            "image", DOMAIN, f"{self._entry.unique_id}_cover"
        )
        component = self.hass.data.get("image")
        entity = (
            component.get_entity(image_id)
            if image_id and component is not None and hasattr(component, "get_entity")
            else None
        )
        if entity is None:
            return None, None
        try:
            main = await entity.async_image()
        except Exception:
            return None, None
        if not main or not is_current():
            return None, None
        return main, getattr(entity, "content_type", None) or "image/jpeg"

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
            "override": self._runtime.override,
            "hold_active": self._runtime.hold_active,
            "other_active_sources": list(selection.other_active_sources),
            "secondary_source": selection.secondary_source,
            "rotation_sources": list(self._runtime.rotation.sources),
            "shown_source": self._runtime.rotation.shown_source,
            "next_switch_at": self._runtime.next_switch_at,
            "slots": [dict(slot) for slot in self._runtime.slot_settings],
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
