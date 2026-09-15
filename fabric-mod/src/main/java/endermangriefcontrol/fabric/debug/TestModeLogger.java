package endermangriefcontrol.fabric.debug;

import endermangriefcontrol.fabric.EndermanGriefControlMod;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.minecraft.ChatFormatting;
import net.minecraft.network.chat.Component;
import net.minecraft.server.MinecraftServer;

import java.io.IOException;
import java.io.InputStream;
import java.util.Properties;

/**
 * Dev-only diagnostic logging for watching the held-block discovery/resolution machinery live
 * during manual QA. Never shipped as a supported feature - there's no /enderman command or config
 * key for it, since it's meant purely for testers, not end users.
 *
 * Switched on by the ENDERMAN_GRIEF_TEST_MODE environment variable (true/false) - but baked in at
 * *build* time, not read at runtime: build.gradle's processResources block substitutes it into
 * test-mode.properties when the jar is built, so the variable only needs to be set wherever the
 * build runs, not wherever the resulting jar is later launched. Rebuilding is already required for
 * every deployment, so this piggybacks on that instead of needing its own runtime environment
 * setup on every launch path (IDE run configs, dedicated servers, etc). Same variable name as the
 * Paper plugin, so one setting controls test mode on either platform's build.
 */
public final class TestModeLogger {

    private static boolean enabled;
    private static MinecraftServer server;

    private TestModeLogger() {
    }

    public static void init() {
        enabled = readBakedFlag();
        ServerLifecycleEvents.SERVER_STARTED.register(startedServer -> server = startedServer);
        if (enabled) {
            EndermanGriefControlMod.LOGGER.info("Test-mode diagnostic logging is ON (baked in at build time).");
        }
    }

    private static boolean readBakedFlag() {
        try (InputStream in = TestModeLogger.class.getClassLoader().getResourceAsStream("test-mode.properties")) {
            if (in == null) {
                return false;
            }
            Properties properties = new Properties();
            properties.load(in);
            return Boolean.parseBoolean(properties.getProperty("test-mode", "false"));
        } catch (IOException e) {
            EndermanGriefControlMod.LOGGER.warn("Failed to read baked test-mode.properties, test mode stays off.", e);
            return false;
        }
    }

    public static void log(String message) {
        if (!enabled) {
            return;
        }

        EndermanGriefControlMod.LOGGER.info("[TEST] " + message);

        if (server != null) {
            Component chatMessage = Component.literal("[TEST] " + message).withStyle(ChatFormatting.DARK_GRAY);
            server.getPlayerList().broadcastSystemMessage(chatMessage, false);
        }
    }
}
