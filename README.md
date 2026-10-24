# Combined Media Player

![Combined Media Player](custom_components/combined_media_player/brand/logo.png)

A Home Assistant custom integration that merges multiple media players into a single combined player with configurable priority logic.

## Features

- **Multiple sources** – eight priority slots for `media_player` entities
- **Priority order** – position 1 = highest priority, position N = lowest
- **Activity detection** – playing/buffering sources beat paused; on/idle never provide media content
- **Media attributes** – title, artist, album, app name, series/season/episode and position come from the display winner
- **Cover art image entity** – dedicated `image` entity for use in dashboards and automations
- **Cached cover art** – HA's media proxy delegates to the existing cover entity
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

The same pure resolver selects the display winner and control target for both platforms. The first configured slot has the highest priority within a tier. A lower-priority playing source always beats a higher-priority paused source. An idle placeholder never supplies its title or picture.

## Control via / Steuerung über

Each slot pairs a **Source** with an optional **Control via** device (German: **Steuerung über (optional)**). Empty means the source controls itself. When that slot wins, playback commands, volume, mute, supported features, shuffle, repeat and source/source-list attributes use its control target. A missing, unknown or unavailable target falls back to the source. A target that is off is still available for commands.

Without a display winner, controls use the first on/idle source's slot. If all sources are inactive, there is no control target. Devices reporting `supported_features: 0` gain no invented capabilities; select a suitable control device if needed.

Media browsing keeps its existing delegation order: display winner, selected control target, then any configured source or control with `BROWSE_MEDIA`. That capability remains exposed even when no display source is playing.

## Cover behavior

Only the display winner supplies cover art, via its published `entity_picture`. Absolute HTTP(S) pictures and HA-relative proxy paths are supported. The combined player's image hash includes the winning entity ID, picture and title; source or title changes therefore invalidate HA's proxy even if the URL remains the same. No picture means no media-player image hash.

HA always serves the combined image through its own proxy, which delegates to the paired cover image entity. Absolute picture URLs are cache keys for the shared memory/disk cache (14-day TTL). Relative paths are cached only in memory. Source entity fetching is preferred, with an entity-picture URL fallback.

The cover image entity can retain the last good image during a brief gap for the **same display source**. A source switch, loss of the display winner, or an on/idle state clears that fallback. A lower-priority source's image is never substituted for the winner. In-flight responses from an earlier source/title are discarded.

## Entities

Each integration entry creates two entities under a shared device:

| Entity | Description |
|--------|-------------|
| `media_player.<name>` | Combined media player with full controls |
| `image.<name>_cover` | Cover art image of the currently active source |

The `image` entity is useful in dashboards (Picture card) and automations where a proper `image` entity is required instead of `entity_picture`.

Both entities expose an `active_source` attribute with the `entity_id` of the currently winning source.

The cover entity also retains `active_source_name`. The player exposes `control_target`, ordered `slots` (`source`, optional `control`) and `other_active_sources` (additional playing/buffering sources in slot order). The latter is diagnostic only; no collage is generated.

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
3. Expand priority sections and select each source, starting with slot 1
4. Optionally choose **Control via** for each source; leave unused fields on **—**
5. Save – `media_player.<name>` and `image.<name>_cover` appear

Sources, their order and the name can be changed at any time under **Options** of the integration.

Setup and options use the same slot form. Empty slots are removed when saved; duplicate sources keep their first slot and its control. Both source and control entities are monitored for state changes. Existing configurations with more than eight sources retain all sources during migration and editing.

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
