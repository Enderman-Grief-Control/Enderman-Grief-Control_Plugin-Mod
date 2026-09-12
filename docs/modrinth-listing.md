# Modrinth listing draft

Draft copy for the consolidated listing at https://modrinth.com/plugin/enderman-grief-control (hosts both the Paper plugin and the Fabric mod as separate versions in one project) — mirrors what's live as of 2026-09-07. Paste/adapt into Modrinth's editor, don't upload this file itself.

**Title:** Enderman Grief Control

**Summary** (short tagline field):
> Stop Endermen from stealing and placing blocks without disabling mobGriefing: no world grief, no new persistent block-carrying Endermen.

**Description** (long-form field, below):

---

Enderman Grief Control stops Endermen from picking up or placing blocks without disabling `mobGriefing` for the rest of your world.

It affects only Endermen, preventing new persistent block-carrying Endermen while leaving other vanilla mob behavior unchanged.

## Why use Enderman Grief Control?

- **Endermen only:** Keep the fix focused on the mob causing the problem.
- **Pickup and placement blocked:** Prevent Endermen from taking blocks or placing carried blocks back into the world.
- **No global gamerule tradeoff:** Leave `mobGriefing` enabled for farms, villagers, sheep, and other mechanics that rely on it.
- **Persistent carrier prevention:** Stop new block-carrying Endermen from being created and building up over time.

*(Enabled/Disabled comparison screenshots go here on the live listing.)*

## Which download do I need?

Enderman Grief Control has separate builds for **Paper** and **Fabric**. Choose the file that matches your setup.

| Your setup | Use |
|---|---|
| **Paper** server | **Paper** version |
| **Fabric** server | **Fabric** version |
| **Fabric** singleplayer | **Fabric** version |

Use the Versions tab to download the build that matches your Minecraft version.

## Platform features

### Paper

- Per-world configuration
- Runtime configuration reload
- Optional event logging

### Fabric

- Singleplayer and dedicated server support
- Optional logging
- In-game `/enderman` command and optional Mod Menu screen for changing settings live, no restart

## Installation

### Paper

1. Download the **Paper** version from the Versions tab.
2. Place the `.jar` file in your server's `plugins` folder.
3. Restart the server.

### Fabric

1. Install **Fabric Loader** for your Minecraft version.
2. Download the **Fabric** version from the Versions tab.
3. Place the `.jar` file in your `mods` folder.
4. Launch the game or server.

**Fabric API is not required.**

## Configuration

Enderman Grief Control works out of the box with its default settings. Configuration is only needed if you want to customize its behavior.

### Paper

Configuration is located at: `plugins/EndermanGriefControl/config.yml`

After making changes, reload the configuration with the plugin's reload command or restart the server.

### Fabric

Configuration is located at: `config/no-enderman-grief.json`

Changes apply live, no restart needed, two ways: the in-game `/enderman` command (`reload`, `status`, `toggle`, `set logging`), which works everywhere including dedicated servers; or, in singleplayer/self-hosted worlds only, [Mod Menu](https://modrinth.com/mod/modmenu)'s settings screen for this mod, if installed.

For a full reference of available configuration options, see the [GitHub documentation](https://github.com/Jack-Underhill/Enderman-Grief-Control).

## Compatibility

- **Paper:** Supports `Paper` servers only. `Bukkit`, `Spigot`, and `Purpur` are not officially supported.
- **Fabric:** Requires `Fabric Loader` and works in both singleplayer and on dedicated servers.
- Supported Minecraft versions are listed on the Versions tab for each release.

## Source, bugs, and feedback

Enderman Grief Control is open source under the **Apache 2.0 License**.

- [View the source code on GitHub](https://github.com/Jack-Underhill/Enderman-Grief-Control)
- [Report a bug or request a feature](https://github.com/Jack-Underhill/Enderman-Grief-Control/issues)

Bug reports and feedback are welcome.
