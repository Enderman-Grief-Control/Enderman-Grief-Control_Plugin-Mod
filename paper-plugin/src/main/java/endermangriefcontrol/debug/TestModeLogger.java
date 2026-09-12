package endermangriefcontrol.debug;

import org.bukkit.configuration.file.YamlConfiguration;
import org.bukkit.plugin.java.JavaPlugin;

import java.io.File;

/**
 * Dev-only diagnostic logging for watching the held-block discovery/resolution machinery live
 * during manual QA. Never shipped as a supported feature - there's no /enderman command or
 * config.yml key for it, since it's meant purely for testers, not end users.
 *
 * Switched on by hand-creating a "debug.yml" (key: "test-mode: true") in the plugin's data folder.
 * Unlike config.yml, this file is never bundled as a default resource and never written by the
 * plugin itself - a normal install never has one, so there's no in-game way to create or flip it.
 */
public final class TestModeLogger {

    private static boolean enabled;
    private static JavaPlugin plugin;

    private TestModeLogger() {
    }

    public static void init(JavaPlugin owningPlugin) {
        plugin = owningPlugin;
        File file = new File(plugin.getDataFolder(), "debug.yml");
        enabled = file.exists() && YamlConfiguration.loadConfiguration(file).getBoolean("test-mode", false);
        if (enabled) {
            plugin.getLogger().info("Test-mode diagnostic logging is ON.");
        }
    }

    public static void log(String message) {
        if (!enabled) {
            return;
        }

        plugin.getLogger().info("[TEST] " + message);
        plugin.getServer().broadcastMessage("[TEST] " + message);
    }
}
