# Enderman Grief Control (Fabric)

Every mob can be spawn-proofed and optimized around — except endermen. They teleport straight through spawn-proofing into hidden pockets (deep underground, inside your base), and the moment one picks up a block, it sticks around far longer than it should, quietly eating into the mob cap and tanking spawn rates on any mob farm nearby. (And yes, they also just grief your builds overnight.)

This Fabric mod fixes that at the source, for Minecraft 1.21 singleplayer worlds and Fabric servers: endermen simply can't pick up or place blocks anymore, full stop, while every other mob behaves exactly as vanilla intends. This is the Fabric counterpart to the [Paper plugin](../paper-plugin/) in this repo; see the top-level [README](../README.md) for why two separate projects exist.

## Installation

1. Requires [Fabric Loader](https://fabricmc.net/use/) (0.19.3+) and Minecraft 1.21.
2. No Fabric API dependency required.
3. Drop the built jar into your `.minecraft/mods/` folder (or your server's `mods/` folder) and launch.

## How it works

Enderman block pickup and placement are each governed by a private AI goal inside vanilla's `EnderMan` class (`EndermanTakeBlockGoal` and `EndermanLeaveBlockGoal`), whose `canUse()` method already gates on the `mobGriefing` gamerule plus a random chance. This mod injects into `canUse()` on both goals: if vanilla would have returned `true` (meaning gamerule-on and the random check passed) and this mod is enabled, the return value is overridden to `false`, so the goal never activates — the enderman simply never attempts the pickup/placement, rather than attempting it and having it reverted. The global `mobGriefing` gamerule itself is untouched, so other mobs (creepers, silverfish, etc.) are unaffected.

## Configuration

`config/no-enderman-grief.json` (created with defaults on first launch):

```json
{
  "enabled": true,
  "loggingEnabled": false,
  "logRemovals": true,
  "heldBlockHandling": "auto-clear"
}
```

- `enabled` — a true kill switch for the whole mod: not just whether pickup/placement is prevented, but also whether the held-block monitor does anything at all. While off, no discovery scanning, no tracking, no resolving happens - re-enabling is what picks any of that back up (see `armPendingDiscovery` in the code).
- `loggingEnabled` (default **off**) — write every prevented pickup/placement to Minecraft's normal log stream and announce it as a short, color-coded chat message (e.g. `[Enderman] Denied pickup at (10, -60, -13).`). Chat messages are rate-limited and batched; log lines repeat every time, so this is off by default to avoid spam.
- `logRemovals` (default **on**) — announce when a stuck holder is auto-cleared. Separate from `loggingEnabled` - a clear only ever fires once per enderman and confirms an actual problem just got fixed, so it defaults to on even with denial logging off.
- `heldBlockHandling` — how an enderman already stuck holding a block (from before the mod was enabled, or a window where it was toggled off) is handled, checked every ~2 minutes:
  - `"auto-clear"` (the default) — removes the carried block outright, nothing dropped. Resolved automatically, no configuration needed. A successful clear is logged/announced (`[Enderman] cleared a holder at (...)`, aqua) when `logRemovals` is on (the default).
  - `"alert"` — instead of clearing, periodically re-announces the enderman's location (`[Enderman] holding a block at (...)`, gold — distinct from the light-purple denial messages above), so you can hunt it down and kill it yourself. Not gated by either logging toggle - choosing this mode is itself the opt-in. For players who'd rather nothing be resolved on their behalf automatically.
  - `"off"` — leave it alone entirely.

There's no per-world setting (unlike the Paper plugin) — singleplayer doesn't have Bukkit's multi-world-folder concept, so a single global toggle covers it.

All settings can be changed two ways: the `/enderman` command below (applies immediately, works everywhere including dedicated servers), or — singleplayer/self-host only, since it can't reach a separate dedicated server — [Mod Menu](https://modrinth.com/mod/modmenu)'s settings screen for this mod, if installed. That screen groups "Prevent Enderman Grief" and "Stuck Holders" under a "Mode" heading, and "Log Removals"/"Log Denied Attempts" under an "Announcements" heading below it. While "Prevent Enderman Grief" is off, the other three controls grey out (nothing else matters until it's back on). Cycling a button only changes what's displayed - nothing is applied until "Save & Quit" is pressed, so browsing through options (e.g. cycling past "Auto-Clear" on the way to "Off") can't trigger a real clear/alert along the way. "Cancel" discards every pending change, same as closing the screen any other way.

## Commands & permission

All subcommands live under `/enderman` and require permission level 2 (op). Tab-completion is available at every argument position.

| Command | Does |
|---|---|
| `/enderman reload` | Reloads `config/no-enderman-grief.json` from disk |
| `/enderman status` | Shows the current `enabled`/logging/held-block state |
| `/enderman toggle [true\|false]` | Sets (or flips, if no value given) `enabled`, persisted to disk |
| `/enderman set log-denials <true\|false>` | Changes `loggingEnabled`, persisted to disk |
| `/enderman set log-removals <true\|false>` | Changes `logRemovals`, persisted to disk |
| `/enderman set held-block <auto-clear\|alert\|off>` | Changes `heldBlockHandling`, persisted to disk |

## A note on maintenance

Unlike the Paper plugin, which only calls long-stable public Bukkit API, this mod targets Minecraft's internal `EnderMan` AI goal classes via Mixin. Those internals can be restructured on any Minecraft version bump — a new version could rename, merge, or remove these goal classes even if enderman behavior itself doesn't change. If the mod stops building or stops working after a Minecraft update, the fix is to re-locate the equivalent goal classes/methods for the new version (e.g. via Loom's `genSources` task to decompile the new mappings) and update the two mixin target strings in `src/main/resources/enderman-grief-control.mixins.json` and the `@Mixin(targets = "...")` annotations accordingly.

## Manual QA checklist

No MockBukkit-equivalent testing framework exists for Mixin-based mods at this scale, so verification is manual. Run `./gradlew runClient`, then in a disposable singleplayer world:

- [ ] Lure or spawn an enderman near loose blocks (grass, dirt) — confirm no pickup occurs while `enabled: true`.
- [ ] Confirm enderman block placement is also prevented (endermen only place a block they're already carrying — you may need `/summon` with an NBT `carried_block` tag, or wait for a natural pickup to be prevented first and test placement separately by temporarily setting `enabled: false`, letting one pick up a block, then re-enabling and confirming it never places it).
- [ ] Set `enabled: false` in `config/no-enderman-grief.json`, restart — confirm vanilla griefing behavior resumes.
- [ ] Confirm other `mobGriefing`-gated behavior is unaffected: creepers still destroy terrain, villagers still farm.
- [ ] With `loggingEnabled: true`, confirm every prevented pickup/placement appears in Minecraft's normal log stream; confirm chat shows the first denial immediately, then rate-limits and batches repeated denials of the same type in the same dimension. With `false`, confirm both chat and log stay silent.
- [ ] With Mod Menu installed, open its settings screen for this mod, confirm the "Mode" heading groups "Prevent Enderman Grief"/"Stuck Holders" and the "Announcements" heading groups "Log Removals"/"Log Denied Attempts". Toggle "Prevent Enderman Grief" off and confirm the other three controls grey out immediately (before Save & Quit); toggle it back on and confirm they re-enable. Cycle all settings, then press "Cancel" and confirm nothing actually changed (reopen the screen, or check `config/no-enderman-grief.json`) - especially that cycling "Stuck Holders" through "Auto-Clear" on the way to another value did not actually clear a real stuck holder. Then repeat and press "Save & Quit" instead, confirming all pending choices are applied and saved together. Hover each button and confirm the tooltip text matches the currently-selected state.
- [ ] Without Mod Menu installed, confirm the game still launches normally (the integration is compile-time only and must not be required).
- [ ] Run `/enderman status`, `/enderman toggle false`, `/enderman set log-denials true`, `/enderman set log-removals false`, `/enderman set held-block alert`, confirming tab-completion at every argument position (including the `auto-clear`/`alert`/`off` suggestions) and that `config/no-enderman-grief.json` reflects each change on disk.
- [ ] Hand-edit `config/no-enderman-grief.json` externally, then run `/enderman reload` — confirm the change takes effect without restarting.
- [ ] With `enabled: false`, confirm nothing held-block-related happens at all: pick up a block as an enderman (grief prevention is off, so this should work), wait several minutes, and confirm no discovery/resolution/clear ever occurs while still disabled - only re-enabling should pick it up.
- [ ] `/summon minecraft:enderman ~ ~ ~ {carried_block:{Name:"minecraft:dirt"}}` (or toggle `enabled` off, let one pick up naturally, then toggle it back on) to create a stuck holder. With `heldBlockHandling: "auto-clear"` (the default) and `logRemovals: true` (the default), confirm the carried block is removed within ~2 minutes with nothing dropped, and `[Enderman] cleared a holder at (...)` is announced in aqua; confirm nothing is announced/logged with `logRemovals: false` even though the clear still happens, regardless of `loggingEnabled`.
- [ ] With `heldBlockHandling: "alert"` (regardless of either logging toggle), confirm the same stuck enderman instead gets `holding a block at (...)` re-announced in gold every ~2 minutes without ever losing its carried block; killing it stops further alerts immediately.
- [ ] With `heldBlockHandling: "off"`, confirm a stuck holder is neither announced nor cleared.

## Building from source

```bash
git clone https://github.com/Jack-Underhill/Enderman-Grief-Control.git
cd Enderman-Grief-Control/fabric-mod
./gradlew build
```

The built jar lands at `build/libs/EndermanGriefControl-mc_1.21-fabric-1.1.0.jar`.
