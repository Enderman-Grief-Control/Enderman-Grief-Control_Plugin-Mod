package endermangriefcontrol.fabric.debug;

import com.google.gson.Gson;
import endermangriefcontrol.fabric.EndermanGriefControlMod;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.ChatFormatting;
import net.minecraft.network.chat.Component;
import net.minecraft.server.MinecraftServer;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

/**
 * Dev-only diagnostic logging for watching the held-block discovery/resolution machinery live
 * during manual QA. Never shipped as a supported feature - there's no /enderman command or config
 * key for it, since it's meant purely for testers, not end users.
 *
 * Switched on by hand-creating a "no-enderman-grief-debug.json" (key: "testMode": true) in the
 * Fabric config directory. Unlike the real config, this file is never auto-generated or written by
 * the mod itself - a normal install never has one, so there's no in-game way to create or flip it.
 */
public final class TestModeLogger {

    private static final Gson GSON = new Gson();
    private static final Path DEBUG_CONFIG_PATH =
            FabricLoader.getInstance().getConfigDir().resolve("no-enderman-grief-debug.json");

    private static boolean enabled;
    private static MinecraftServer server;

    private TestModeLogger() {
    }

    public static void init() {
        enabled = readEnabledFlag();
        ServerLifecycleEvents.SERVER_STARTED.register(startedServer -> server = startedServer);
        if (enabled) {
            EndermanGriefControlMod.LOGGER.info("Test-mode diagnostic logging is ON.");
        }
    }

    private static boolean readEnabledFlag() {
        if (!Files.exists(DEBUG_CONFIG_PATH)) {
            return false;
        }

        try (var reader = Files.newBufferedReader(DEBUG_CONFIG_PATH)) {
            DebugConfig config = GSON.fromJson(reader, DebugConfig.class);
            return config != null && config.testMode;
        } catch (IOException e) {
            EndermanGriefControlMod.LOGGER.warn("Failed to read {}, test mode stays off.", DEBUG_CONFIG_PATH, e);
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

    private static final class DebugConfig {
        boolean testMode;
    }
}
