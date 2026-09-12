package endermangriefcontrol.fabric.command;

import com.mojang.brigadier.CommandDispatcher;
import com.mojang.brigadier.arguments.BoolArgumentType;
import com.mojang.brigadier.arguments.StringArgumentType;
import com.mojang.brigadier.context.CommandContext;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.commands.SharedSuggestionProvider;
import net.minecraft.network.chat.Component;
import endermangriefcontrol.fabric.EndermanGriefControlConfig;
import endermangriefcontrol.fabric.EndermanGriefControlMod;
import endermangriefcontrol.fabric.heldblock.HeldBlockHandling;

import java.util.List;

/**
 * Registers /enderman <reload|status|toggle|set>, mirroring the Paper plugin's command shape.
 * There's no per-world concept here (unlike Paper) since singleplayer/Fabric servers don't have
 * Bukkit's multi-world-folder structure, so "toggle"/"set" both act on the single global config.
 */
public final class EndermanCommand {

    private static final int PERMISSION_LEVEL = 2; // op
    private static final List<String> HELD_BLOCK_MODES = List.of("auto-clear", "alert", "off");

    private EndermanCommand() {
    }

    public static void register(CommandDispatcher<CommandSourceStack> dispatcher) {
        dispatcher.register(Commands.literal("enderman")
                .requires(source -> source.hasPermission(PERMISSION_LEVEL))
                .then(Commands.literal("reload").executes(EndermanCommand::reload))
                .then(Commands.literal("status").executes(EndermanCommand::status))
                .then(Commands.literal("toggle")
                        .executes(ctx -> setEnabled(ctx, !EndermanGriefControlMod.getConfig().enabled))
                        .then(Commands.argument("enabled", BoolArgumentType.bool())
                                .executes(ctx -> setEnabled(ctx, BoolArgumentType.getBool(ctx, "enabled")))))
                .then(Commands.literal("set")
                        .then(Commands.literal("logging")
                                .then(Commands.argument("value", BoolArgumentType.bool())
                                        .executes(ctx -> setLogging(ctx, BoolArgumentType.getBool(ctx, "value")))))
                        .then(Commands.literal("held-block")
                                .then(Commands.argument("mode", StringArgumentType.word())
                                        .suggests((ctx, builder) -> SharedSuggestionProvider.suggest(HELD_BLOCK_MODES, builder))
                                        .executes(ctx -> setHeldBlockHandling(
                                                ctx, StringArgumentType.getString(ctx, "mode")))))));
    }

    private static int reload(CommandContext<CommandSourceStack> ctx) {
        EndermanGriefControlMod.setConfig(EndermanGriefControlConfig.load());
        EndermanGriefControlMod.getHeldBlockMonitor().armPendingDiscovery(); // Config may have re-enabled by hand-edit.
        ctx.getSource().sendSuccess(() -> Component.literal("EndermanGriefControl configuration reloaded."), true);
        return 1;
    }

    private static int status(CommandContext<CommandSourceStack> ctx) {
        EndermanGriefControlConfig config = EndermanGriefControlMod.getConfig();
        HeldBlockHandling heldBlockHandling = HeldBlockHandling.fromConfig(
                config.heldBlockHandling, HeldBlockHandling.AUTO_CLEAR);
        ctx.getSource().sendSuccess(() -> Component.literal(
                "Prevention: " + (config.enabled ? "enabled" : "disabled")
                        + ", logging: " + (config.loggingEnabled ? "enabled" : "disabled")
                        + ", held-block: " + heldBlockHandling.toConfigValue()), false);
        return 1;
    }

    private static int setEnabled(CommandContext<CommandSourceStack> ctx, boolean value) {
        EndermanGriefControlConfig config = EndermanGriefControlMod.getConfig();
        boolean wasEnabled = config.enabled;
        config.enabled = value;
        config.save();
        if (value && !wasEnabled) {
            EndermanGriefControlMod.getHeldBlockMonitor().armPendingDiscovery(); // May have accumulated stuck holders.
        }
        ctx.getSource().sendSuccess(() -> Component.literal(
                "Enderman grief prevention is now " + (value ? "enabled" : "disabled") + "."), true);
        return 1;
    }

    private static int setLogging(CommandContext<CommandSourceStack> ctx, boolean value) {
        EndermanGriefControlConfig config = EndermanGriefControlMod.getConfig();
        config.loggingEnabled = value;
        config.save();
        ctx.getSource().sendSuccess(() -> Component.literal(
                "Logging is now " + (value ? "enabled" : "disabled") + "."), true);
        return 1;
    }

    private static int setHeldBlockHandling(CommandContext<CommandSourceStack> ctx, String value) {
        HeldBlockHandling mode = HeldBlockHandling.fromConfig(value, null);
        if (mode == null) {
            ctx.getSource().sendFailure(Component.literal("Usage: /enderman set held-block <auto-clear|alert|off>"));
            return 0;
        }

        EndermanGriefControlConfig config = EndermanGriefControlMod.getConfig();
        config.heldBlockHandling = mode.toConfigValue();
        config.save();
        ctx.getSource().sendSuccess(() -> Component.literal(
                "Held-block handling is now " + mode.toConfigValue() + "."), true);
        return 1;
    }
}
