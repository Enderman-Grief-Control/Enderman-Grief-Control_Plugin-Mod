# Enderman Grief Control: _Disabled_

<img width="1920" height="1080" alt="2026-09-04_16 27 17" src="https://github.com/user-attachments/assets/f6986074-cf5d-46f2-adff-b42783476606" />

# Enderman Grief Control: _Enabled_

<img width="1920" height="1080" alt="2026-09-04_15 39 30" src="https://github.com/user-attachments/assets/d36db65c-d363-45f2-b617-69f1ffbf449c" />

Every mob in Minecraft can be spawn-proofed and optimized around — except endermen. They teleport straight through spawn-proofing into hidden pockets (deep underground, inside your own base), and the moment one picks up a block, it sticks around far longer than it should, quietly eating into your mob cap and tanking spawn rates on any mob farm nearby. Run a base with several farms, and endermen become the one mob you can't design around — no matter how well everything else is optimized.

Enderman Grief Control fixes that at the source: endermen simply can't pick up or place blocks anymore, full stop. No more untouchable, teleporting endermen souring your farm's spawn rates — and as a bonus, your builds stop getting quietly grief'd overnight too. Every other mob behaves exactly as vanilla intends; only endermen are affected, and the `mobGriefing` gamerule itself is never touched.

This repo has two independent builds of the same idea, one for each way people play Minecraft:

| | [`paper-plugin/`](paper-plugin/) | [`fabric-mod/`](fabric-mod/) |
|---|---|---|
| **For** | Server admins | Singleplayer (or Fabric server) players |
| **Platform** | Paper | Fabric |
| **Minecraft** | 1.21.x | 1.21 |
| **Install into** | `plugins/` | `mods/` |
| **Config format** | `config.yml`, per-world | `config.json`, single toggle |
| **Reload without restart** | Yes (`/enderman reload`) | Yes (`/enderman reload`, or live via Mod Menu) |
| **Settings UI** | — | Mod Menu (optional, singleplayer/self-host only) |

If you just want to download and install one of these, go straight to whichever README fits how you play — [`paper-plugin/README.md`](paper-plugin/README.md) or [`fabric-mod/README.md`](fabric-mod/README.md) — each has full install and configuration instructions for that platform. This page is a map of the repo, not a full manual.

## Why two separate projects?

A Bukkit/Paper plugin and a Fabric mod solve the same problem but can't share code or a build system: Paper plugins only run on a Bukkit/Paper *server*, and vanilla singleplayer has no plugin API at all — it runs Mojang's own integrated server, which only mods (via Fabric, Forge, etc.) can hook into. So this repo is two small, independent projects living side by side:

- **`paper-plugin/`** — a Maven project, targets the Paper server API, listens for the game's own `EntityChangeBlockEvent` and cancels it for endermen.
- **`fabric-mod/`** — a Gradle/Fabric Loom project, targets vanilla Minecraft directly via Mixin, since Fabric doesn't expose a built-in way to override griefing per mob type the way the Paper plugin's approach does.

Neither depends on the other, and they can be built, tested, and released independently.

## Installing

- **Running a server?** Use the Paper plugin: [`paper-plugin/README.md`](paper-plugin/README.md#installation).
- **Playing singleplayer** (or hosting a small Fabric server for friends)? Use the Fabric mod: [`fabric-mod/README.md`](fabric-mod/README.md#installation).

## Building from source

Each project builds independently with its own toolchain:

```bash
git clone https://github.com/Jack-Underhill/Enderman-Grief-Control.git

# Paper plugin (Maven, Java 21)
cd Enderman-Grief-Control/paper-plugin
mvn package

# Fabric mod (Gradle, Java 21) — from the repo root instead
cd Enderman-Grief-Control/fabric-mod
./gradlew build
```

See each subproject's README for exactly where the built jar ends up and how it's tested (`paper-plugin/` has an automated MockBukkit test suite; `fabric-mod/` is verified with a manual QA checklist, since no automated testing framework fits its scale of Mixin-based code well right now).

## Contributing

Both projects are small and deliberately minimal — see each README's "How it works" section for the actual implementation approach before making changes. Open an issue or PR on GitHub.

## License

[MIT](LICENSE)
