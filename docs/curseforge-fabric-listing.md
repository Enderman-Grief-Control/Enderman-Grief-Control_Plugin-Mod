Enderman Grief Control prevents Endermen from picking up or placing blocks while leaving the global `mobGriefing` gamerule unchanged.

## Why use Enderman Grief Control?

* **Protect builds and terrain:** Keep Endermen from moving blocks around your world.
* **Preserve vanilla mechanics:** Keep villagers farming, sheep grazing, and other `mobGriefing`-dependent behavior working normally.
* **Optimize mob farms:** Prevent Endermen from introducing spawnable blocks into cleared or spawn-proofed areas, while stopping persistent block-carrying Endermen from accumulating nearby.
* **Handle existing block-carriers:** Choose whether to clear, monitor, or leave Endermen that were already carrying blocks when protection was enabled.

### Enderman Grief Control: `Enabled`
![Enderman Grief Control Enabled to prevent block changes](https://cdn.modrinth.com/data/cached_images/4780bc3d8571840fd08ecb6ddc26a626c8f0e89b.webp)

### Enderman Grief Control: `Disabled`
![Vanilla Enderman Griefing Behavior](https://cdn.modrinth.com/data/cached_images/9ae3b43b6c96cebb81f7ce143be39669e042b02f.webp)

## Which download do I need?

CurseForge has separate listings for **Fabric** and **Paper**. Choose the version that matches your setup.

| Your setup          | Use            |
| ------------------- | -------------- |
| **Paper** server        | **Paper** version  |
| **Fabric** server       | **Fabric** version |
| **Fabric** singleplayer | **Fabric** version |

You're viewing the Fabric mod listing. Running a Paper server? [Get the Paper plugin here](https://www.curseforge.com/minecraft/bukkit-plugins/enderman-grief-control).

Use the Files tab to download a build compatible with your Minecraft version.

## Platform features

* Administrator `/enderman` commands to inspect settings, make changes, and reload configuration without restarting
* Configurable handling of existing block-carrying Endermen
* Optional logging and in-game announcements for blocked attempts and carried-block cleanup
* Command changes take effect immediately and are saved automatically
* Singleplayer and dedicated server support
* Global settings shared across dimensions
* Command argument suggestions
* Optional Mod Menu screen for common settings in local worlds

## Installation

1. Install **Fabric Loader** for your Minecraft version.
2. Download the **Fabric** version from the Files tab.
3. Place the `.jar` file in your `mods` folder.
4. Launch the game or server.

**Fabric API is not required.**

## Configuration

`Enderman Grief Control` has protection enabled by default. No configuration is needed to start protecting your world.

Existing block-carrying Endermen can be handled in three ways: `auto-clear` (default) clears their held blocks without dropping items, `alert` preserves them and periodically reports their coordinates, and `off` leaves them untouched without alerts.

Checks run periodically while protection is enabled and only affect loaded Endermen. Unloaded chunks are never forced to load.

Configuration is located at:

`config/no-enderman-grief.json`

Use `/enderman` to manage global protection, carrier handling, and logging. Commands require permission level 2 and are available in both singleplayer and dedicated server environments when the appropriate permissions are granted.

The optional [Mod Menu](https://www.curseforge.com/minecraft/mc-mods/modmenu) screen provides controls for common settings in local/integrated-server worlds. Changes apply through **Save & Quit**; Cancel discards them. Remote dedicated servers are managed through commands or configuration files.

After manually editing the configuration file, use `/enderman reload` or restart the game/server.

Blocked pickup/placement logging is disabled by default; automatic carried-block cleanup logging is enabled. These settings control server logs and chat announcements in the affected dimension. Carrier alerts remain active in `alert` mode independently of the logging toggles.

Message wording, colors, and the cooldown used to group denied attempts can be customized in the configuration file. These options are not exposed through commands or Mod Menu.

For a full reference of available configuration options, see the [GitHub documentation](https://github.com/Enderman-Grief-Control/Enderman-Grief-Control_Plugin-Mod/blob/master/fabric-mod/README.md).

## Compatibility

* Requires `Fabric Loader` and supports both singleplayer and dedicated servers.

## Source, bugs, and feedback

`Enderman Grief Control` is open source under the **Apache 2.0 License**.

- [View the source code on GitHub](https://github.com/Enderman-Grief-Control/Enderman-Grief-Control_Plugin-Mod)
- [Report a bug or request a feature](https://github.com/Enderman-Grief-Control/Enderman-Grief-Control_Plugin-Mod/issues)

Bug reports and feedback are welcome.
