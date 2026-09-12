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
  "loggingEnabled": false
}
```

- `enabled` — whether enderman block pickup/placement is prevented.
- `loggingEnabled` — announce every prevented pickup/placement, both in the log file and as a short, color-coded chat message (e.g. `[Enderman] Denied pickup at (10, -60, -13).`), so it's visible without checking logs.

There's no per-world setting (unlike the Paper plugin) — singleplayer doesn't have Bukkit's multi-world-folder concept, so a single global toggle covers it.

Both settings apply live, no restart needed, two ways: the `/enderman` command below (works everywhere, including dedicated servers), or — singleplayer/self-host only, since it can't reach a separate dedicated server — [Mod Menu](https://modrinth.com/mod/modmenu)'s settings screen for this mod, if installed.

## Commands & permission

All subcommands live under `/enderman` and require permission level 2 (op). Tab-completion is available at every argument position.

| Command | Does |
|---|---|
| `/enderman reload` | Reloads `config/no-enderman-grief.json` from disk |
| `/enderman status` | Shows the current `enabled`/logging state |
| `/enderman toggle [true\|false]` | Sets (or flips, if no value given) `enabled`, persisted to disk |
| `/enderman set logging <true\|false>` | Changes `loggingEnabled`, persisted to disk |

## A note on maintenance

Unlike the Paper plugin, which only calls long-stable public Bukkit API, this mod targets Minecraft's internal `EnderMan` AI goal classes via Mixin. Those internals can be restructured on any Minecraft version bump — a new version could rename, merge, or remove these goal classes even if enderman behavior itself doesn't change. If the mod stops building or stops working after a Minecraft update, the fix is to re-locate the equivalent goal classes/methods for the new version (e.g. via Loom's `genSources` task to decompile the new mappings) and update the two mixin target strings in `src/main/resources/no-enderman-grief.mixins.json` and the `@Mixin(targets = "...")` annotations accordingly.

## Manual QA checklist

No MockBukkit-equivalent testing framework exists for Mixin-based mods at this scale, so verification is manual. Run `./gradlew runClient`, then in a disposable singleplayer world:

- [ ] Lure or spawn an enderman near loose blocks (grass, dirt) — confirm no pickup occurs while `enabled: true`.
- [ ] Confirm enderman block placement is also prevented (endermen only place a block they're already carrying — you may need `/summon` with an NBT `carried_block` tag, or wait for a natural pickup to be prevented first and test placement separately by temporarily setting `enabled: false`, letting one pick up a block, then re-enabling and confirming it never places it).
- [ ] Set `enabled: false` in `config/no-enderman-grief.json`, restart — confirm vanilla griefing behavior resumes.
- [ ] Confirm other `mobGriefing`-gated behavior is unaffected: creepers still destroy terrain, villagers still farm.
- [ ] With `loggingEnabled: true`, confirm a color-coded message appears in chat and the same message appears in the log file (`logs/latest.log`) for each prevented pickup/placement; with `false`, confirm both stay silent.
- [ ] With Mod Menu installed, open its settings screen for this mod, toggle both settings, and confirm the change to enderman behavior applies immediately (no restart, no reopening the world).
- [ ] Without Mod Menu installed, confirm the game still launches normally (the integration is compile-time only and must not be required).
- [ ] Run `/enderman status`, `/enderman toggle false`, `/enderman set logging true`, confirming tab-completion at every argument position and that `config/no-enderman-grief.json` reflects each change on disk.
- [ ] Hand-edit `config/no-enderman-grief.json` externally, then run `/enderman reload` — confirm the change takes effect without restarting.

## Building from source

```bash
git clone https://github.com/Jack-Underhill/Enderman-Grief-Control.git
cd Enderman-Grief-Control/fabric-mod
./gradlew build
```

The built jar lands at `build/libs/EndermanGriefControl-mc_1.21-fabric-1.0.0.jar`.
