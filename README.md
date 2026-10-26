# Combined Media Player

![Combined Media Player](custom_components/combined_media_player/brand/logo.png)

A Home Assistant custom integration that merges multiple media players into a single combined player with configurable priority logic.

## Features

- **Multiple sources** – reorderable `media_player` sources without a fixed form limit
- **Priority order** – position 1 = highest priority, position N = lowest
- **Activity detection** – playing/buffering sources beat paused; on/idle never provide media content
- **Fixed media attributes** – title, artist, app name, series/season/episode and position come from the display winner
- **Full-cover rotation** – alternate simultaneous sources without changing the title or controls; optional other-source details in the album field
- **Cover art image entity** – dedicated `image` entity for use in dashboards and automations
- **Cached cover art** – HA's media proxy uses the existing validated image fetching and shared cache
- **Per-slot controls** – forward commands and control attributes to the slot's optional control device, or its source
- **State model** – `playing`, `paused`, `idle`, `off`; always available
- **Device grouping** – media player and image entity are grouped under a single device in HA
- **UI-only setup** – no YAML required, fully configured via the UI

## Priority Logic

| Tier | States | Description |
|------|--------|-------------|
| 1 | `playing`, `buffering` | Actively playing |
| 2 | `paused` | Paused media |
| No display | `on`, `idle` | Ready for controls; no cover, title or media attributes |
| — | `off`, `unavailable`, `unknown` | Ignored |

Within the same tier, the source with the lower position (higher priority) wins.

**State mapping of the combined player:**

| Active source | Combined player |
|---------------|-----------------|
| `playing` / `buffering` | `playing` |
| `paused` | `paused` |
| No display winner, at least one source `on` / `idle` | `idle` |
| All sources off, missing, unavailable or unknown | `off` |

The same pure resolver selects the display winner and control target. All entities share its result, including the optional hold time and priority override below. The first configured slot has the highest priority within a tier. A lower-priority playing source always beats a higher-priority paused source. An idle placeholder never supplies its title or picture.

## Global options

These controls appear above the source list in the first setup/options step. Existing entries inherit defaults. Config-entry minor version 2.2 silently removes the obsolete `show_secondary` option from data and options; entity identities and existing slot settings are retained.

| Option | German label | Default |
| --- | --- | --- |
| `rotation_seconds` | Wechselintervall (Sekunden) | 30 seconds; range 5–600 |
| `show_other_sources` | Weitere Quellen in zweiter Zeile anzeigen | On |
| `hide_radio_station` | Sendername bei Radio ausblenden | On |
| `hold_seconds` | Haltezeit (Sekunden) | 5 seconds; range 0–30, 0 disables |

**Hold time:** when the display source leaves playing/buffering/paused, keep that source's last active media attributes, display state, control routing and cover until the deadline. A source higher in the effective slot order that starts playing/buffering interrupts immediately; a lower playing source waits until the hold expires. If the held source recovers, the failure timer resets. An expiry callback reevaluates even without another source event. Hold state is transient and is not restored after restarting HA.

**Radio albums:** with radio filtering enabled, an album name is hidden only when the winning `media_content_id` contains `://radio/`, covering Music Assistant's `radiobrowser://radio/…` and `library://radio/…` IDs. Other albums are unchanged.

**Priority source:** the new select offers Automatic (German: Automatisch) and readable names with entity IDs. A chosen source moves to the front of the effective slot order; playback tiers still apply, and the stored slot order stays intact. An idle preferred source does not displace a playing source. The selected entity ID survives restarts even if its friendly name changes. Removing that source from the configuration returns to automatic selection.

## Control via / Steuerung über

Each slot pairs a **Source** with an optional **Control via** device (German: **Steuerung über (optional)**). Empty means the source controls itself. When that slot wins, playback commands, volume, mute, supported features, shuffle, repeat and source/source-list attributes use its control target. A missing, unknown or unavailable target falls back to the source. A target that is off is still available for commands.

Without a display winner, controls use the first on/idle source's slot. If all sources are inactive, there is no control target. Devices reporting `supported_features: 0` gain no invented capabilities; select a suitable control device if needed.

Media browsing keeps its existing delegation order: display winner, selected control target, then any configured source or control with `BROWSE_MEDIA`. That capability remains exposed even when no display source is playing.

## Cover behavior

The dedicated cover image always follows the display winner. The media player's cover follows `shown_source`, which rotates when multiple eligible sources run simultaneously. Absolute HTTP(S) pictures and HA-relative proxy paths are supported. The image hash includes the shown source's ID, picture and title, plus the main cover for fallback invalidation. Cover switches and title changes invalidate HA's proxy even if the URL remains the same.

HA always serves the combined image through its own proxy. Rotating covers use the existing validated source-image fetcher; an unavailable rotating image falls back to the paired cover image entity. Absolute picture URLs are cache keys for the shared memory/disk cache (14-day TTL). Relative paths are cached only in memory. Source entity fetching is preferred, with an entity-picture URL fallback. Every result must have an image MIME type and pass Pillow verification in the executor. Invalid bytes are rejected and the next route is tried; invalid stored disk images are removed on cache initialization. Pillow is used only for validation.

The cover image entity can retain the last good image during a brief gap for the **same display source**. During the optional hold time it uses the last active source snapshot. A source switch or loss of the display winner after the hold expires clears that fallback. A lower-priority source's image is never substituted for the winner. In-flight responses from an earlier source/title are discarded.

### Cover rotation and second line (1.10.0 decision)

The rotation set contains all playing/buffering sources in the **stored slot order**. During the existing main-source hold time, its last active snapshot also participates. Known titles are compared after trimming and casefolding: only the first occurrence remains, so mirrored game presence does not displace music. Missing/blank titles are not duplicates. Paused/on/idle sources do not join the rotation; a paused display winner still supplies the fixed cover when fewer than two rotation members exist.

With at least two members, only the cover rotates, starting with the display winner, then continuing in slot order and wrapping to the first member. Each source stays for its optional `display_seconds` value (5–600 seconds), otherwise the global interval (default 30 seconds). A source or title update does not restart an ongoing duration. If membership changes and the shown source remains, its remaining time is preserved. If it disappears, the display winner is shown immediately with a fresh duration. Fewer than two members stop rotation immediately; no rotation timer remains. Timers are cancelled when the player is removed. Rotation is transient and starts afresh after reload/restart.

**Rotation is visible only when a client follows the media player's cover, for example `media_player.media`.** A card or client following the dedicated `image` entity continues to see the pure main cover. There is no inset, compositor or composed-image cache. The priority select still determines the fixed display/control winner; it does not rewrite the stored rotation order. If that winner is itself an excluded title duplicate, it is shown on reset before entering the deduplicated rotation.

Title, artist, content type, duration/position, app and controls remain with the display winner and its control target. With **Show other sources in the second line** enabled, `media_album_name` lists the other rotation members in slot order, separated by ` · `:

- Music: `♪ Title – Artist` (omit the artist separator when empty).
- Games: `🎮 Title`.
- Other media, including video/movies/TV shows: `▶ Title`.

Otherwise the original album behavior applies, including **Hide station name for radio**. A client such as Kiosk Satellite may render `media_artist · media_album_name` below the fixed title. `secondary_source` remains a compatibility attribute using its existing first-nonduplicate rule; `other_active_sources` remains complete, including duplicate sources. Neither controls the rotating image.

## Entities

Each integration entry creates four entities under a shared device:

| Entity | Description |
|--------|-------------|
| `media_player.<name>` | Combined media player with full controls |
| `image.<name>_cover` | Cover art image of the currently active source |
| `select.<entry_unique_id>_override` | Priority source, restored after restart |
| `sensor.<entry_unique_id>_active_source` | Winning entity ID or `none` |

The two new entity IDs above are suggestions derived from the entry unique ID (HA may resolve naming conflicts). Their unique IDs are exactly `<entry.unique_id>_override` and `<entry.unique_id>_active_source`. Their displayed names are the configured player name followed by “Priority source” / “Active source”, or “Vorrangquelle” / “Aktive Quelle” in German. Existing player and cover IDs and unique IDs are unchanged.

The `image` entity is useful in dashboards (Picture card) and automations where a proper `image` entity is required instead of `entity_picture`.

Both entities expose an `active_source` attribute with the `entity_id` of the currently winning source.

The cover entity also retains `active_source_name`. The player exposes `control_target`, ordered `slots` (`source`, optional `control` and `display_seconds`), `other_active_sources`, `secondary_source`, `override` and `hold_active`. Player and active-source sensor also expose `rotation_sources` (ordered list), `shown_source` (current cover source) and `next_switch_at` (UTC ISO timestamp or null). Diagnostics include the same rotation fields. The source sensor retains `active_source_name`; HA reserves `friendly_name` for the sensor's own translated name.

## Repairs and diagnostics

Two minutes after HA has fully started, the integration checks configured sources and control targets. A Repair notice lists only IDs that have neither a state nor an entity registry entry. Unavailable entities or entities still registered do not trigger it. State/registry updates remove the notice automatically once all configured IDs exist again. Reloading options removes obsolete notices and starts a new delayed check.

The integration's diagnostics download includes effective options, current resolution/rotation, per-source duration, and per-slot state, media type and picture-presence indicators. Each slot also has `last_fetch` and `control_last_fetch` (status, safe reason, route and UTC timestamp). Status is null until an attempt exists. The obsolete composition status is removed. Picture URL values are always `**REDACTED**`; names, unknown options and non-enum values are also redacted. Tokens, media titles and content URLs are not exported. No Home Assistant configuration is changed by downloading diagnostics.

## Installation

### HACS (recommended)

1. Open HACS → Integrations → ⋮ → Custom Repositories
2. URL: `https://github.com/Levtos/combined_mediaplayers`, Category: Integration
3. Install "Combined Media Player" and restart HA

### Manual

Copy the `custom_components/combined_media_player` folder into your `config/custom_components/` directory and restart HA.

## Configuration

1. **Settings → Devices & Services → Add Integration → Combined Media Player**
2. Enter a name (e.g. "Living Room")
3. Select **Sources** and drag them into priority order; the top source has the highest priority. Set the global options, then continue
4. In **Per-source settings**, optionally set **Control via** and **Display duration (seconds)** for each source. Every field includes the source's name and entity ID. Empty control = self-control; empty duration = global interval
5. Save – `media_player.<name>` and `image.<name>_cover` appear

Sources, their order and the name can be changed at any time under **Options** of the integration.

Setup and options use the same two steps and require at least one source. There is no fixed source-count limit. Duplicate sources keep their first occurrence. Controls and durations are prefilled by source entity ID, so dragging a source keeps both settings attached. Clearing a field removes its override. Renaming and other changes are saved only after the second step is submitted.

The stored `options["slots"]` list remains source-bound: `{"source": "media_player.example", "control": null, "display_seconds": 45}`. A missing `display_seconds` inherits the global interval and needs no data migration. Both source and control entities remain monitored. Entry minor migration 2.1→2.2 only removes `show_secondary` silently; names, IDs and unique IDs remain intact.

### Upgrade to 1.7.0

Config-entry version 1 migrates automatically to version 2. Existing ordered `sources` become `options["slots"] = [{"source": "media_player.example", "control": null}, ...]`, preserving their order. Legacy `sources` and `audio_sources` keys are removed from data and options; the old global audio list is deliberately discarded, with one info log during migration. Configure the optional control device for each slot after upgrading if required.

Entry IDs, unique IDs and entity registry identities are unchanged; existing player and cover entity IDs remain intact. Installation/restart and live acceptance are separate from the GitHub release.

## Example: PS5 · Apple TV · HomePods

```
Prio 1: media_player.ps5             → Gaming    → native thumbnails
Prio 2: media_player.appletv         → Streaming → native cover art
Prio 3: media_player.homepods_cover  → Music     → iTunes cover via media_cover_art
```

The combined player automatically shows the image of the currently active source.
