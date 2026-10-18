package endermangriefcontrol.fabric;

import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.kyori.adventure.text.Component;
import net.minecraft.server.MinecraftServer;
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
import endermangriefcontrol.messaging.MessageTemplates;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.ArrayList;
import java.util.List;
import java.util.Objects;
import java.util.WeakHashMap;
import java.util.concurrent.RejectedExecutionException;

public final class EndermanGriefControlMod implements ModInitializer {

    public static final String MOD_ID = "no-enderman-grief";
    public static final Logger LOGGER = LoggerFactory.getLogger(MOD_ID);

    private static EndermanGriefControlConfig config;
    private static MessageTemplates messageTemplates = MessageTemplates.DEFAULTS;
    private static DenialRateLimiter denialRateLimiter;
    private static final HeldBlockMonitor HELD_BLOCK_MONITOR = new HeldBlockMonitor();
    private static final FabricChatBroadcaster CHAT_BROADCASTER = new FabricChatBroadcaster();
    private static final Object SETTINGS_SAVE_LOCK = new Object();
    private static final WeakHashMap<MinecraftServer, PendingSettingsSaves> PENDING_SETTINGS_SAVES =
            new WeakHashMap<>();

    private static final class PendingSettingsSaves {
        private boolean stopping;
        private final List<PendingSettingsSave> saves = new ArrayList<>();
    }

    private static final class PendingSettingsSave {
        private final Settings settings;

        private PendingSettingsSave(Settings settings) {
            this.settings = settings;
        }
    }

    @Override
    public void onInitialize() {
        config = EndermanGriefControlConfig.load();
        messageTemplates = config.messages.toMessageTemplates();
        denialRateLimiter = new DenialRateLimiter(config.denialRateLimitSeconds * 1000L);
        TestModeLogger.init();
        CommandRegistrationCallback.EVENT.register((dispatcher, registryAccess, environment) ->
                EndermanCommand.register(dispatcher));
        ServerLifecycleEvents.SERVER_STOPPING.register(EndermanGriefControlMod::onServerStopping);
        HELD_BLOCK_MONITOR.register();
        CHAT_BROADCASTER.register();
        LOGGER.info("EndermanGriefControl has been initialized.");
    }

    public static EndermanGriefControlConfig getConfig() {
        return config;
    }

    public static void setConfig(EndermanGriefControlConfig newConfig) {
        config = newConfig;
        messageTemplates = newConfig.messages.toMessageTemplates();
        long cooldownMillis = newConfig.denialRateLimitSeconds * 1000L;
        if (denialRateLimiter == null) {
            denialRateLimiter = new DenialRateLimiter(cooldownMillis);
        } else {
            denialRateLimiter.setCooldownMillis(cooldownMillis);
        }
    }

    /**
     * Finds and resolves endermen already stuck holding a block from before the mod was enabled
     * (or from a window where it was toggled off). Exposed so command handlers can trigger a
     * re-scan whenever "enabled" flips off->on.
     */
    public static HeldBlockMonitor getHeldBlockMonitor() {
        return HELD_BLOCK_MONITOR;
    }

    /** An immutable snapshot of the four choices staged by Mod Menu. */
    public record Settings(boolean enabled, HeldBlockHandling heldBlockHandling,
                           boolean logRemovals, boolean loggingEnabled) {
        public Settings {
            Objects.requireNonNull(heldBlockHandling, "heldBlockHandling");
        }
    }

    /**
     * Sends a Mod Menu save to its captured integrated server. The screen calls this on the client
     * thread; the queued commit and any monitor work run together on the server thread. A title-menu
     * save, or a save whose server has begun stopping, changes the shared config without world work.
     */
    public static void saveModMenuSettings(MinecraftServer server, Settings settings) {
        Objects.requireNonNull(settings, "settings");
        if (server == null) {
            synchronized (SETTINGS_SAVE_LOCK) {
                applySettingsWithoutMonitor(settings);
            }
            return;
        }

        PendingSettingsSave save = new PendingSettingsSave(settings);
        synchronized (SETTINGS_SAVE_LOCK) {
            PendingSettingsSaves session = PENDING_SETTINGS_SAVES.computeIfAbsent(
                    server, ignored -> new PendingSettingsSaves());
            if (session.stopping || !server.isRunning()) {
                applySettingsWithoutMonitor(settings);
                return;
            }
            session.saves.add(save);
        }

        try {
            server.execute(() -> finishPendingSettingsSave(server, save));
        } catch (RejectedExecutionException e) {
            synchronized (SETTINGS_SAVE_LOCK) {
                PendingSettingsSaves session = PENDING_SETTINGS_SAVES.get(server);
                if (session != null && session.saves.remove(save)) {
                    applySettingsWithoutMonitor(save.settings);
                }
            }
        }
    }

    private static void finishPendingSettingsSave(MinecraftServer server, PendingSettingsSave save) {
        synchronized (SETTINGS_SAVE_LOCK) {
            PendingSettingsSaves session = PENDING_SETTINGS_SAVES.get(server);
            if (session == null || !session.saves.remove(save)) {
                return; // The stopping callback already committed this snapshot.
            }
            if (session.stopping || !server.isRunning()) {
                applySettingsWithoutMonitor(save.settings);
            } else {
                applySettings(save.settings);
            }
        }
    }

    private static void onServerStopping(MinecraftServer server) {
        synchronized (SETTINGS_SAVE_LOCK) {
            PendingSettingsSaves session = PENDING_SETTINGS_SAVES.computeIfAbsent(
                    server, ignored -> new PendingSettingsSaves());
            session.stopping = true;
            for (PendingSettingsSave save : session.saves) {
                applySettingsWithoutMonitor(save.settings);
            }
            session.saves.clear();
        }
    }

    /**
     * Commits a Mod Menu selection with live monitor actions. The caller must run this on the
     * active server thread; all final values are installed and saved before monitor work starts.
     */
    public static void applySettings(Settings settings) {
        commitSettings(settings, true);
    }

    /** Commits a selection without monitor work when no active server can safely handle it. */
    public static void applySettingsWithoutMonitor(Settings settings) {
        commitSettings(settings, false);
    }

    private static void commitSettings(Settings settings, boolean runMonitorAction) {
        Objects.requireNonNull(settings, "settings");
        boolean wasEnabled = config.enabled;
        HeldBlockHandling previousHandling = HeldBlockHandling.fromConfig(
                config.heldBlockHandling, HeldBlockHandling.AUTO_CLEAR);

        config.enabled = settings.enabled();
        config.heldBlockHandling = settings.heldBlockHandling().toConfigValue();
        config.logRemovals = settings.logRemovals();
        config.loggingEnabled = settings.loggingEnabled();
        config.save();

        if (!runMonitorAction || !settings.enabled()) {
            return;
        }
        if (!wasEnabled) {
            HELD_BLOCK_MONITOR.armPendingDiscovery();
        } else if (previousHandling != settings.heldBlockHandling()) {
            HELD_BLOCK_MONITOR.runResolutionPass();
        }
    }

    /**
     * Applies a command's enabled change immediately. Mod Menu uses {@link #applySettings(Settings)}
     * so its other final choices are visible before discovery starts.
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
     * Applies a command's carrier-mode change immediately, including a resolution pass. Mod Menu
     * uses {@link #applySettings(Settings)} to apply its whole selection first.
     */
    public static void setHeldBlockHandling(HeldBlockHandling mode) {
        config.heldBlockHandling = mode.toConfigValue();
        config.save();
        HELD_BLOCK_MONITOR.runResolutionPass();
    }

    /**
     * Called by the pickup/placement mixins whenever a block change was prevented. Always logs the
     * same short console/server-log message (matching the Paper plugin's wording), so admins see
     * every denial. The chat announcement is rate-limited per dimension and {@link DenialType}
     * instead - only fires when {@link #denialRateLimiter} says this dimension's denial type is
     * due, reporting how many of that type happened there since the last chat message rather than
     * one line per denial.
     */
    public static void announceBlocked(EnderMan enderman, DenialType type) {
        if (!config.loggingEnabled) {
            return;
        }

        String coords = enderman.getBlockX() + ", " + enderman.getBlockY() + ", " + enderman.getBlockZ();

        LOGGER.info("[Enderman] Denied " + type.actionText() + " at (" + coords + ").");

        if (enderman.level() instanceof ServerLevel serverLevel) {
            String scope = serverLevel.dimension().location().toString();
            denialRateLimiter.recordDenial(scope, type).ifPresent(count -> {
                Component chatMessage = GriefControlMessages.denied(messageTemplates, type, count);
                CHAT_BROADCASTER.broadcastToWorld(serverLevel, chatMessage);
            });
        }
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
            Component chatMessage = GriefControlMessages.heldBlockAlert(messageTemplates, coords);
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

        Component chatMessage = GriefControlMessages.heldBlockCleared(messageTemplates, count);
        CHAT_BROADCASTER.broadcastToWorld(level, chatMessage);
    }
}
