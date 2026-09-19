package endermangriefcontrol.fabric;

import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback;
import net.kyori.adventure.text.Component;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.monster.EnderMan;
import endermangriefcontrol.fabric.command.EndermanCommand;
import endermangriefcontrol.fabric.debug.TestModeLogger;
import endermangriefcontrol.fabric.heldblock.HeldBlockHandling;
import endermangriefcontrol.fabric.heldblock.HeldBlockMonitor;
import endermangriefcontrol.fabric.message.FabricChatBroadcaster;
import endermangriefcontrol.messaging.DenialRateLimiter;
import endermangriefcontrol.messaging.DenialType;
import endermangriefcontrol.messaging.GriefControlMessages;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public final class EndermanGriefControlMod implements ModInitializer {

    public static final String MOD_ID = "no-enderman-grief";
    public static final Logger LOGGER = LoggerFactory.getLogger(MOD_ID);

    private static EndermanGriefControlConfig config;
    private static DenialRateLimiter denialRateLimiter;
    private static final HeldBlockMonitor HELD_BLOCK_MONITOR = new HeldBlockMonitor();
    private static final FabricChatBroadcaster CHAT_BROADCASTER = new FabricChatBroadcaster();

    @Override
    public void onInitialize() {
        config = EndermanGriefControlConfig.load();
        denialRateLimiter = new DenialRateLimiter(config.denialRateLimitSeconds * 1000L);
        TestModeLogger.init();
        CommandRegistrationCallback.EVENT.register((dispatcher, registryAccess, environment) ->
                EndermanCommand.register(dispatcher));
        HELD_BLOCK_MONITOR.register();
        CHAT_BROADCASTER.register();
        LOGGER.info("EndermanGriefControl has been initialized.");
    }

    public static EndermanGriefControlConfig getConfig() {
        return config;
    }

    public static void setConfig(EndermanGriefControlConfig newConfig) {
        config = newConfig;
        denialRateLimiter = new DenialRateLimiter(newConfig.denialRateLimitSeconds * 1000L);
    }

    /**
     * Finds and resolves endermen already stuck holding a block from before the mod was enabled
     * (or from a window where it was toggled off). Exposed so command handlers can trigger a
     * re-scan whenever "enabled" flips off->on.
     */
    public static HeldBlockMonitor getHeldBlockMonitor() {
        return HELD_BLOCK_MONITOR;
    }

    /**
     * The single place "enabled" actually gets changed - both the command and the Mod Menu screen
     * call this rather than each mutating config.enabled themselves, so the arm-on-re-enable side
     * effect only has to be written once and can't be forgotten at a second call site.
     */
    public static void setEnabled(boolean value) {
        boolean wasEnabled = config.enabled;
        config.enabled = value;
        config.save();
        if (value && !wasEnabled) {
            HELD_BLOCK_MONITOR.armPendingDiscovery(); // May have accumulated stuck holders while disabled.
        }
    }

    /**
     * The single place held-block handling actually gets changed - see {@link #setEnabled}.
     * Re-resolves immediately so the new mode applies to already-tracked holders right away,
     * instead of waiting up to ~2 minutes for the next periodic resolution pass.
     */
    public static void setHeldBlockHandling(HeldBlockHandling mode) {
        config.heldBlockHandling = mode.toConfigValue();
        config.save();
        HELD_BLOCK_MONITOR.runResolutionPass();
    }

    /**
     * Called by the pickup/placement mixins whenever a block change was prevented. Always logs the
     * same short console/server-log message (matching the Paper plugin's wording), so admins see
     * every denial. The chat announcement is rate-limited per {@link DenialType} instead - only
     * fires when {@link #denialRateLimiter} says this denial's type is due, reporting how many of
     * that type happened since the last chat message rather than one line per denial.
     */
    public static void announceBlocked(EnderMan enderman, DenialType type) {
        if (!config.loggingEnabled) {
            return;
        }

        String coords = enderman.getBlockX() + ", " + enderman.getBlockY() + ", " + enderman.getBlockZ();

        LOGGER.info("[Enderman] Denied " + type.actionText() + " at (" + coords + ").");

        denialRateLimiter.recordDenial(type).ifPresent(count -> {
            if (enderman.level() instanceof ServerLevel serverLevel) {
                Component chatMessage = GriefControlMessages.denied(config.messages.toMessageTemplates(), type, count);
                CHAT_BROADCASTER.broadcastToWorld(serverLevel, chatMessage);
            }
        });
    }

    /**
     * Called periodically by HeldBlockMonitor for a stuck holder under "alert" handling. Not gated
     * by loggingEnabled - choosing "alert" as the held-block handling mode is itself the opt-in;
     * requiring the separate, unrelated loggingEnabled toggle too would mean a player who sets
     * "alert" but forgets to also flip loggingEnabled gets silent, useless alerts. Colored red,
     * distinct from every other announcement's purple/green/cyan families, so it stands out as
     * "go hunt this" rather than blending into routine denial spam.
     */
    public static void announceHeldBlockAlert(EnderMan enderman) {
        String coords = enderman.getBlockX() + ", " + enderman.getBlockY() + ", " + enderman.getBlockZ();

        LOGGER.info("[Enderman] holding a block at (" + coords + ").");

        if (enderman.level() instanceof ServerLevel serverLevel) {
            Component chatMessage = GriefControlMessages.heldBlockAlert(config.messages.toMessageTemplates(), coords);
            CHAT_BROADCASTER.broadcastToWorld(serverLevel, chatMessage);
        }
    }

    /**
     * Called by HeldBlockMonitor whenever a stuck holder under "auto-clear" handling is resolved -
     * console/server-log only, every individual clear, regardless of how many endermen a single
     * resolution pass resolves. Gated by logRemovals - a separate toggle from loggingEnabled
     * (which only covers denials), since a clear is a one-time confirmation the actual problem got
     * fixed, not a repeating "still trying and being stopped" signal - most installs will want this
     * on even with denial logging off, hence its own default-true toggle. The chat announcement is
     * handled separately, once per affected level per pass, by {@link
     * #announceHeldBlockClearedBatch(ServerLevel, int)}.
     */
    public static void announceHeldBlockCleared(EnderMan enderman) {
        if (!config.logRemovals) {
            return;
        }

        String coords = enderman.getBlockX() + ", " + enderman.getBlockY() + ", " + enderman.getBlockZ();

        LOGGER.info("[Enderman] holding cleared at (" + coords + ").");
    }

    /**
     * Reports how many stuck holders were auto-cleared in {@code level} during a single resolution
     * pass, as one chat message instead of one per enderman. Gated by logRemovals, same as the
     * per-event console line in {@link #announceHeldBlockCleared(EnderMan)}.
     */
    public static void announceHeldBlockClearedBatch(ServerLevel level, int count) {
        if (!config.logRemovals) {
            return;
        }

        Component chatMessage = GriefControlMessages.heldBlockCleared(config.messages.toMessageTemplates(), count);
        CHAT_BROADCASTER.broadcastToWorld(level, chatMessage);
    }
}
