"""Small per-entry state shared by the existing platforms and diagnostics."""

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
import asyncio
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from .const import (
    CONF_SHOW_APP_ICON_FALLBACK,
    DEFAULT_SHOW_APP_ICON_FALLBACK,
    CONF_HOLD_SECONDS,
    CONF_ROTATION_SECONDS,
    DEFAULT_HOLD_SECONDS,
    DEFAULT_ROTATION_SECONDS,
    DOMAIN,
    entry_option,
    slots_from_entry,
    slot_settings_from_entry,
    tracked_entities,
)
from .app_icons import AppIconCache, app_icon_slug, placeholder_state
from .resolver import Resolution, priority_slots, resolve_with_hold
from .rotation import (
    Rotation,
    TitleSwitch,
    active_titles,
    advance_rotation,
    rotation_sources,
    title_switch_after_change,
    shown_source_state,
)


class EntryRuntime:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.show_app_icon_fallback = entry_option(
            entry, CONF_SHOW_APP_ICON_FALLBACK, DEFAULT_SHOW_APP_ICON_FALLBACK
        )
        self._icon_cache = AppIconCache()
        self._icons = {}
        self._icon_lock = asyncio.Lock()
        self._icon_task = None
        self._cover_origins = {}
        self.slots = slots_from_entry(entry)
        self.slot_settings = slot_settings_from_entry(entry)
        self.rotation = Rotation()
        self._titles: dict = {}
        self._title_switch = TitleSwitch()
        self._rotation_due: float | None = None
        self.next_switch_at: str | None = None
        self.rotation_seconds = float(
            entry_option(entry, CONF_ROTATION_SECONDS, DEFAULT_ROTATION_SECONDS)
        )
        self._durations = {
            slot["source"]: slot.get("display_seconds") for slot in self.slot_settings
        }
        self._rotation_enabled = False
        self._rotation_cancel: Callable[[], None] | None = None
        self.override: str | None = None
        self.selection = Resolution(None, None, "off", ())
        self.display_state: State | None = None
        self.hold_active = False
        self.failed_at: float | None = None
        self.hold_seconds = float(
            entry_option(entry, CONF_HOLD_SECONDS, DEFAULT_HOLD_SECONDS)
        )
        self._listeners: list[Callable[[], None]] = []
        self._hold_cancel: Callable[[], None] | None = None
        self._repair_cancel: Callable[[], None] | None = None
        self._unsubs: list[Callable[[], None]] = []
        self._repairs_ready = False
        self._issue_id = f"{entry.entry_id}_missing_entities"

    @callback
    def start(self) -> None:
        entities = tracked_entities(self.slots)
        if entities:
            self._unsubs.append(
                async_track_state_change_event(self.hass, entities, self._changed)
            )
        self._unsubs.append(
            self.hass.bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED, self._registry_changed
            )
        )
        self._unsubs.append(async_at_started(self.hass, self._started))
        self.refresh()

    @callback
    def subscribe(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def set_override(self, source: str | None) -> None:
        self.override = source if source in {sid for sid, _ in self.slots} else None
        self.refresh()

    @callback
    def refresh(self, _now=None) -> None:
        states = {
            sid: state
            for sid in tracked_entities(self.slots)
            if (state := self.hass.states.get(sid)) is not None
        }
        result = resolve_with_hold(
            priority_slots(self.slots, self.override),
            states,
            self.selection,
            self.failed_at,
            time.monotonic(),
            self.hold_seconds,
            previous_attributes=self.display_state.attributes
            if self.display_state
            else None,
        )
        self.selection, self.failed_at, self.hold_active = result
        if not self.hold_active:
            self.display_state = states.get(self.selection.display_source)
        if self._hold_cancel:
            self._hold_cancel()
            self._hold_cancel = None
        if self.hold_active:
            remaining = self.failed_at + self.hold_seconds - time.monotonic()
            self._hold_cancel = async_call_later(
                self.hass, max(0, remaining), self._hold_expired
            )
        self._refresh_rotation(states)
        if self.show_app_icon_fallback and (
            self._icon_task is None or self._icon_task.done()
        ):
            self._icon_task = self.hass.async_create_task(self.async_prepare_icons())
        for listener in tuple(self._listeners):
            listener()

    @property
    def main_presentation_state(self):
        if self.display_state is not None:
            return self.display_state
        return placeholder_state(
            self.selection,
            priority_slots(self.slots, self.override),
            {
                sid: state
                for sid, _ in self.slots
                if (state := self.hass.states.get(sid)) is not None
            },
            self.show_app_icon_fallback,
        )

    @property
    def shown_presentation_state(self):
        source = self.rotation.shown_source
        return shown_source_state(
            source,
            self.selection.display_source,
            self.main_presentation_state,
            {source: self.hass.states.get(source)} if source else {},
        )

    def icon_for(self, state):
        slug = app_icon_slug(state.attributes) if state else None
        return self._icons.get(slug) if self.show_app_icon_fallback else None

    async def async_prepare_icons(self):
        """Stat/read/decode off-loop; properties only ever read memory."""
        if not self.show_app_icon_fallback:
            return
        async with self._icon_lock:
            changed = False
            for state in (self.main_presentation_state, self.shown_presentation_state):
                slug = app_icon_slug(state.attributes) if state else None
                if slug is None:
                    continue
                try:
                    icon = await self.hass.async_add_executor_job(
                        self._icon_cache.load,
                        slug,
                        self.hass.config.path("www", DOMAIN, "app_icons"),
                        Path(__file__).parent / "app_icons",
                    )
                except Exception:
                    icon = None
                previous = self._icons.get(slug)
                if previous != icon:
                    self._icons[slug] = icon
                    changed = True
            if changed:
                for listener in tuple(self._listeners):
                    listener()

    @staticmethod
    def _cover_key(state):
        if state is None:
            return None
        return (
            state.entity_id,
            *(
                state.attributes.get(key)
                for key in ("entity_picture", "media_title", "app_name", "app_id")
            ),
        )

    def record_cover(self, state, origin, slug=None):
        key = self._cover_key(state)
        value = (key, origin, slug)
        source = state.entity_id if state else None
        if self._cover_origins.get(source) != value:
            self._cover_origins[source] = value
            for listener in tuple(self._listeners):
                listener()

    def cover_attributes_for(self, state):
        """Allowlisted metadata for bytes served for this content snapshot."""
        record = self._cover_origins.get(state.entity_id if state else None)
        origin, slug = (
            record[1:]
            if record and record[0] == self._cover_key(state)
            else (None, None)
        )
        if origin == "app_icon" and (
            not self.show_app_icon_fallback or not self._icons.get(slug)
        ):
            origin, slug = None, None
        return {"cover_origin": origin, "app_icon_slug": slug}

    @property
    def cover_attributes(self):
        return self.cover_attributes_for(self.shown_presentation_state)

    @callback
    def start_rotation(self) -> None:
        """Only the player needs rotation; the dedicated image stays pure."""
        self._rotation_enabled = True
        self.refresh()

    @callback
    def stop_rotation(self) -> None:
        self._rotation_enabled = False
        if self._rotation_cancel:
            self._rotation_cancel()
            self._rotation_cancel = None
        self.rotation = self.rotation._replace(next_switch_at=None)
        self._title_switch = TitleSwitch()
        self._titles.clear()
        self._rotation_due = None
        self.next_switch_at = None

    @callback
    def _refresh_rotation(self, states) -> None:
        effective_states = dict(states)
        if self.hold_active and self.display_state:
            effective_states[self.display_state.entity_id] = self.display_state
        sources = rotation_sources(
            [source for source, _ in self.slots], effective_states
        )
        now = time.monotonic()
        titles = active_titles([source for source, _ in self.slots], states)
        self._title_switch = title_switch_after_change(
            self._title_switch, self._titles, titles, sources, now
        )
        self._titles = titles
        previous_deadline = self._rotation_due
        self.rotation = advance_rotation(
            self.rotation,
            sources,
            self.selection.display_source,
            now,
            self.rotation_seconds,
            self._durations,
            self._title_switch,
        )
        if (
            len(sources) < 2
            or self._title_switch.at is not None
            and now >= self._title_switch.at
        ):
            self._title_switch = TitleSwitch()
        if self._rotation_cancel:
            self._rotation_cancel()
            self._rotation_cancel = None
        deadline = (
            (self._title_switch.at or self.rotation.next_switch_at)
            if self._rotation_enabled
            else None
        )
        self._rotation_due = deadline
        if deadline is None:
            self.rotation = self.rotation._replace(next_switch_at=None)
            self.next_switch_at = None
        else:
            if deadline != previous_deadline:
                self.next_switch_at = (
                    dt_util.utcnow() + timedelta(seconds=max(0, deadline - now))
                ).isoformat()
            self._rotation_cancel = async_call_later(
                self.hass, max(0, deadline - time.monotonic()), self._rotation_expired
            )

    @callback
    def _rotation_expired(self, _now) -> None:
        self._rotation_cancel = None
        self.refresh()

    @callback
    def _hold_expired(self, _now) -> None:
        self._hold_cancel = None
        self.refresh()

    @callback
    def _changed(self, _event) -> None:
        self.refresh()
        self._check_repairs()

    @callback
    def _registry_changed(self, _event) -> None:
        self._check_repairs()

    @callback
    def _started(self, _hass) -> None:
        self._repair_cancel = async_call_later(self.hass, 120, self._enable_repairs)

    @callback
    def _enable_repairs(self, _now) -> None:
        self._repair_cancel = None
        self._repairs_ready = True
        self._check_repairs()

    @callback
    def _check_repairs(self) -> None:
        if not self._repairs_ready:
            return
        registry = er.async_get(self.hass)
        missing = [
            sid
            for sid in tracked_entities(self.slots)
            if self.hass.states.get(sid) is None and registry.async_get(sid) is None
        ]
        if missing:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="missing_entities",
                translation_placeholders={"entities": ", ".join(missing)},
                is_persistent=False,
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)

    @callback
    def stop(self) -> None:
        if self._icon_task is not None:
            self._icon_task.cancel()
        self.stop_rotation()
        for cancel in self._unsubs:
            cancel()
        self._unsubs.clear()
        if self._hold_cancel:
            self._hold_cancel()
        if self._repair_cancel:
            self._repair_cancel()
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
