# EndermanGriefControl

Every mob can be spawn-proofed and optimized around — except endermen. They teleport straight through spawn-proofing into hidden pockets (deep underground, inside your own base), and the moment one picks up a block, it sticks around far longer than it should, quietly eating into your mob cap and tanking spawn rates on any mob farm nearby. Run a base with several farms, and endermen are the one mob you can't design around — no matter how well everything else is optimized. (And yes, they also just grief your builds overnight.)

**EndermanGriefControl** fixes that at the source: endermen simply can't pick up or place blocks anymore, full stop. Unlike turning off the `mobGriefing` gamerule, this doesn't touch anything else — creepers still explode, villagers still farm, silverfish still infest. Only endermen are affected.

## Features

- Endermen can't pick up or place blocks anymore — pickup *and* placement, both blocked.
- No more block-holding endermen surviving indefinitely in hidden pockets, eating into your mob cap and dragging down mob farm spawn rates.
- The global `mobGriefing` gamerule is never touched, so every other mob behaves exactly as vanilla intends.
- Enable or disable it per world, if you want different behavior in the Nether, the End, or specific worlds.
- Optional logging (with coordinates) if you want a record of what got blocked.
- One admin-only command, `/enderman`, to inspect and change every setting in-game — with tab-completion — without restarting the server.

## Requirements

- **Server:** Paper. (Not Spigot or Bukkit — the jar is built Mojang-mapped, which relies on Paper's own remapping at load time and won't load on either.)
- **Minecraft:** 1.21.x
- **Java:** 21

The plugin only calls long-stable Bukkit API (`EntityChangeBlockEvent`, `EntityType`, `JavaPlugin`, `FileConfiguration`) — nothing Paper-version-specific — so it's expected to keep working unmodified across the whole 1.21.x line without needing a rebuild per patch release.

## Installation

1. Download the jar (see [Building from source](#building-from-source) below, or grab a release from [Modrinth](https://modrinth.com/plugin/enderman-grief-control)).
2. Drop it into your server's `plugins/` folder.
3. Restart your server.
4. That's it — endermen are already blocked from griefing. Run `/plugins` to confirm **EndermanGriefControl** is listed and enabled.

The first time it runs, the plugin creates a `plugins/EndermanGriefControl/config.yml` with sensible defaults. You don't need to touch it unless you want to change something.

> **Upgrading from NoEndermanGrief?** The plugin (and its data folder) were renamed to match the project's new name. Your old settings are still at `plugins/NoEndermanGrief/config.yml` — copy the values you care about into the new `plugins/EndermanGriefControl/config.yml` after upgrading, since Bukkit won't do this automatically. If you granted the old `noendermangrief.reload`/`noendermangrief.admin` permission explicitly, re-grant it as `endermangriefcontrol.admin`.

## Configuration

`plugins/EndermanGriefControl/config.yml`:

```yaml
# If a world is not listed under "worlds", this value decides
# whether the plugin is enabled there by default.
default-enabled: true

# Per-world overrides.
# Add world names and set them to true/false as needed.
# Example:
# worlds:
#   world: true
#   world_nether: false
#   world_the_end: true
worlds: {}

# Handling for endermen already stuck holding a block placement can no longer clear (e.g. picked
# up before the plugin was enabled, or during a window where it was toggled off). Checked every
# ~2 minutes. One of "auto-clear" (default), "alert", or "off" - see "Stuck holders" below.
default-held-block-handling: auto-clear
held-block-worlds: {}

logging:
  # If true, log whenever the plugin denies an enderman block pickup/placement.
  enabled: false
  # If true, log whenever a stuck holder is auto-cleared. Separate from "enabled" above - on by
  # default, since a clear is a one-time confirmation, not a repeating denial.
  removals: true
```

### Per-world control

- If a world isn't listed under `worlds`, `default-enabled` decides whether the plugin is active there.
- If a world *is* listed under `worlds`, that value overrides `default-enabled` for just that world:

```yaml
worlds:
  world: true
  world_nether: false
  world_the_end: true
```

In this example: enabled in `world` and `world_the_end`, disabled in `world_nether`.

### Logging

Two independent toggles:

- `logging.enabled` (default **off**) — log a line each time an enderman's pickup or placement is denied. This repeats every time a pickup/placement is prevented, so it's off by default to avoid spam.
- `logging.removals` (default **on**) — log a line each time a stuck holder is auto-cleared (see "Stuck holders" below). This only ever fires once per enderman, and confirms an actual problem just got fixed, so it defaults to on even with denial logging off.

Bukkit already prefixes console output with the plugin name and a timestamp, so the messages themselves stay short:

```text
[EndermanGriefControl] Denied pickup at (10, 64, -30).
[EndermanGriefControl] cleared a holder at (10, 64, -30).
```

The [Fabric mod](../fabric-mod/) shows similarly short, prefixed messages in chat, if you use both.

### Stuck holders (already-carrying endermen)

Pickup/placement prevention only stops *new* grief - it doesn't touch an enderman that's already carrying a block (from before the plugin was enabled, or from a window where it was toggled off). `default-held-block-handling` / `held-block-worlds` (same per-world override resolution as `default-enabled`/`worlds` above) decides what happens to one, checked every ~2 minutes:

- **`auto-clear`** (the default) - removes the carried block from the enderman outright. Nothing is dropped - endermen only ever carry common terrain blocks, so nothing of value is lost. This is resolved automatically with no configuration needed. A successful clear is logged (`cleared a holder at (...)`) when `logging.removals` above is on (the default).
- **`alert`** - instead of clearing, periodically re-logs the enderman's location (worded distinctly from the denial log above: `holding a block at (...)`), so you can go hunt it down and kill it yourself. For players who'd rather nothing be resolved on their behalf automatically.
- **`off`** - leave it alone entirely.

## Commands & permission

All subcommands live under `/enderman` and require the `endermangriefcontrol.admin` permission (default `op`). Tab-completion is available at every argument position.

| Command | Does |
|---|---|
| `/enderman reload` | Reloads `config.yml` from disk, no restart needed |
| `/enderman status [world]` | Shows current default/logging state, or a specific world's effective state |
| `/enderman toggle <world> [true\|false]` | Sets (or flips, if no value given) a per-world override, persisted to `config.yml` |
| `/enderman held-block <world> <auto-clear\|alert\|off>` | Sets a per-world held-block handling override, persisted to `config.yml` |
| `/enderman set default <true\|false>` | Changes `default-enabled`, persisted to `config.yml` |
| `/enderman set log-denials <true\|false>` | Changes `logging.enabled`, persisted to `config.yml` |
| `/enderman set log-removals <true\|false>` | Changes `logging.removals`, persisted to `config.yml` |
| `/enderman set held-block-default <auto-clear\|alert\|off>` | Changes `default-held-block-handling`, persisted to `config.yml` |

## Building from source

This project uses Maven.

```bash
git clone https://github.com/Jack-Underhill/Enderman-Grief-Control.git
cd Enderman-Grief-Control/paper-plugin
mvn package
```

The compiled jar lands at `target/EndermanGriefControl-mc_1.21-paper-1.0.0.jar` — copy it into your server's `plugins/` folder.

Automated tests (MockBukkit-based) run as part of the same `mvn package`, or on their own via `mvn test`.

<details>
<summary><b>How it works (technical overview)</b></summary>

- The plugin registers a listener for `EntityChangeBlockEvent` at `EventPriority.HIGH`, so protection plugins (e.g. WorldGuard) get to evaluate the event first, while `MONITOR`-priority observers still see the final outcome.
- If the event's entity type is `ENDERMAN` and the plugin is enabled in that world:
  - The event is cancelled, so the block change never happens.
  - If logging is enabled, a short message with the coordinates is written to the console.
- No other entity types are touched, so creepers, villagers, and every other mob behave exactly as in vanilla.

This makes the plugin safe to drop into an existing survival world where you want to preserve terrain from enderman griefing without affecting anything else.

</details>
