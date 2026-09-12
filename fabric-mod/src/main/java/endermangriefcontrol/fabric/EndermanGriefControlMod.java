package endermangriefcontrol.fabric;

import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback;
import net.minecraft.ChatFormatting;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.MutableComponent;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.monster.EnderMan;
import endermangriefcontrol.fabric.command.EndermanCommand;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

public final class EndermanGriefControlMod implements ModInitializer {

    public static final String MOD_ID = "no-enderman-grief";
    public static final Logger LOGGER = LoggerFactory.getLogger(MOD_ID);

    private static EndermanGriefControlConfig config;

    @Override
    public void onInitialize() {
        config = EndermanGriefControlConfig.load();
        CommandRegistrationCallback.EVENT.register((dispatcher, registryAccess, environment) ->
                EndermanCommand.register(dispatcher));
        LOGGER.info("EndermanGriefControl has been initialized.");
    }

    public static EndermanGriefControlConfig getConfig() {
        return config;
    }

    public static void setConfig(EndermanGriefControlConfig newConfig) {
        config = newConfig;
    }

    /**
     * Called by the pickup/placement mixins whenever a block change was prevented. Logs the same
     * short message to the console/log file that's shown in chat (matching the Paper plugin's log
     * wording), so players — not just admins reading logs — can see it happened.
     */
    public static void announceBlocked(EnderMan enderman, String action) {
        if (!config.loggingEnabled) {
            return;
        }

        String coords = "(" + enderman.getBlockX() + ", " + enderman.getBlockY() + ", " + enderman.getBlockZ() + ")";

        LOGGER.info("[Enderman] Denied " + action + " at " + coords + ".");

        if (enderman.level() instanceof ServerLevel serverLevel) {
            MutableComponent chatMessage = Component.literal("[Enderman] ")
                    .withStyle(ChatFormatting.LIGHT_PURPLE)
                    .append(Component.literal("Denied " + action + " at ")
                            .withStyle(ChatFormatting.GRAY))
                    .append(Component.literal(coords + ".").withStyle(ChatFormatting.GREEN));
            serverLevel.getServer().getPlayerList().broadcastSystemMessage(chatMessage, false);
        }
    }
}
