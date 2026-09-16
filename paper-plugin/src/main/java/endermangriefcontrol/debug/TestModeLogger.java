package endermangriefcontrol.debug;

import org.bukkit.plugin.java.JavaPlugin;

import java.io.IOException;
import java.io.InputStream;
import java.util.Properties;

/**
 * Dev-only diagnostic logging for watching the held-block discovery/resolution machinery live
 * during manual QA. Never shipped as a supported feature - there's no /enderman command or
 * config.yml key for it, since it's meant purely for testers, not end users.
 *
 * Switched on by the ENDERMAN_GRIEF_TEST_MODE environment variable (true/false) - but baked in at
 * *build* time, not read at runtime: pom.xml's resource filtering substitutes it into
 * test-mode.properties when the jar is built, so the variable only needs to be set wherever
 * `mvn package` runs, not wherever the resulting jar is later launched. Rebuilding is already
 * required for every deployment, so this piggybacks on that instead of needing its own runtime
 * environment setup on every launch path (IDE run configs, dedicated servers, etc). Same variable
 * name as the Fabric mod, so one setting controls test mode on either platform's build.
 *
 * Console-only, unlike Fabric's equivalent - Paper's dedicated-server console is always available,
 * so there's no need for the chat broadcast Fabric uses to reach a singleplayer tester with no
 * console. That also avoids these messages landing in a command sender's message queue ahead of
 * the actual command response, which is exactly what a broadcast would do and what broke
 * {@code mvn test} the one time this was tried with a chat broadcast during test-mode build.
 */
public final class TestModeLogger {

    private static boolean enabled;
    private static JavaPlugin plugin;

    private TestModeLogger() {
    }

    public static void init(JavaPlugin owningPlugin) {
        plugin = owningPlugin;
        enabled = readBakedFlag();
        if (enabled) {
            plugin.getLogger().info("Test-mode diagnostic logging is ON (baked in at build time).");
        }
    }

    private static boolean readBakedFlag() {
        try (InputStream in = plugin.getResource("test-mode.properties")) {
            if (in == null) {
                return false;
            }
            Properties properties = new Properties();
            properties.load(in);
            return Boolean.parseBoolean(properties.getProperty("test-mode", "false"));
        } catch (IOException e) {
            return false;
        }
    }

    public static void log(String message) {
        if (!enabled) {
            return;
        }

        plugin.getLogger().info("[TEST] " + message);
    }
}
