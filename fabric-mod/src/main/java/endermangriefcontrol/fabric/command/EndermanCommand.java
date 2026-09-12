package endermangriefcontrol.fabric.command;

import com.mojang.brigadier.CommandDispatcher;
import com.mojang.brigadier.arguments.BoolArgumentType;
import com.mojang.brigadier.context.CommandContext;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.network.chat.Component;
import endermangriefcontrol.fabric.EndermanGriefControlConfig;
import endermangriefcontrol.fabric.EndermanGriefControlMod;

/**
 * Registers /enderman <reload|status|toggle|set>, mirroring the Paper plugin's command shape.
 * There's no per-world concept here (unlike Paper) since singleplayer/Fabric servers don't have
 * Bukkit's multi-world-folder structure, so "toggle"/"set" both act on the single global config.
 */
public final class EndermanCommand {

    private static final int PERMISSION_LEVEL = 2; // op

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
                                        .executes(ctx -> setLogging(ctx, BoolArgumentType.getBool(ctx, "value")))))));
    }

    private static int reload(CommandContext<CommandSourceStack> ctx) {
        EndermanGriefControlMod.setConfig(EndermanGriefControlConfig.load());
        ctx.getSource().sendSuccess(() -> Component.literal("EndermanGriefControl configuration reloaded."), true);
        return 1;
    }

    private static int status(CommandContext<CommandSourceStack> ctx) {
        EndermanGriefControlConfig config = EndermanGriefControlMod.getConfig();
        ctx.getSource().sendSuccess(() -> Component.literal(
                "Prevention: " + (config.enabled ? "enabled" : "disabled")
                        + ", logging: " + (config.loggingEnabled ? "enabled" : "disabled")), false);
        return 1;
    }

    private static int setEnabled(CommandContext<CommandSourceStack> ctx, boolean value) {
        EndermanGriefControlConfig config = EndermanGriefControlMod.getConfig();
        config.enabled = value;
        config.save();
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
}
