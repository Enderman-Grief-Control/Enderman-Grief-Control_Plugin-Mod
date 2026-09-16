package endermangriefcontrol;

import endermangriefcontrol.debug.TestModeLogger;
import endermangriefcontrol.heldblock.HeldBlockHandling;
import endermangriefcontrol.heldblock.HeldBlockMonitor;
import endermangriefcontrol.listener.EndermanBlockListener;
import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.format.NamedTextColor;
import org.bukkit.World;
import org.bukkit.block.Block;
import org.bukkit.command.Command;
import org.bukkit.command.CommandSender;
import org.bukkit.configuration.ConfigurationSection;
import org.bukkit.entity.Enderman;
import org.bukkit.entity.Player;
import org.bukkit.plugin.java.JavaPlugin;

import java.util.Collections;
import java.util.List;
import java.util.Locale;
import java.util.stream.Collectors;

/**
 * Main plugin entry point.
 *
 * This class is created and managed by the Paper/Spigot server.
 * It must match the "main" value in plugin.yml:
 *   endermangriefcontrol.EndermanGriefControlPlugin
 */
public class EndermanGriefControlPlugin extends JavaPlugin {

    private HeldBlockMonitor heldBlockMonitor;

    @Override
    public void onEnable() {
        // Ensure default config.yml is saved to the plugin data folder
        // (plugins/EndermanGriefControl/config.yml) if it does not exist.
        saveDefaultConfig();
        TestModeLogger.init(this);

        getLogger().info("EndermanGriefControl is enabling...");

        // Register our event listener so we can intercept enderman block changes.
        getServer().getPluginManager().registerEvents(
                new EndermanBlockListener(this),
                this
        );

        // Finds and resolves endermen already stuck holding a block from before the plugin
        // was enabled (or from a window where it was toggled off).
        heldBlockMonitor = new HeldBlockMonitor(this);
        getServer().getPluginManager().registerEvents(heldBlockMonitor, this);
        heldBlockMonitor.start();

        getLogger().info("EndermanGriefControl has been enabled.");
    }

    @Override
    public void onDisable() {
        getLogger().info("EndermanGriefControl has been disabled.");
    }

    /**
     * Checks whether the plugin is enabled for a specific world.
     *
     * We first look for an explicit entry under "worlds.<worldName>".
     * If there is none, we fall back to the "default-enabled" flag.
     */
    public boolean isWorldEnabled(String worldName) {
        boolean defaultEnabled = getConfig().getBoolean("default-enabled", true);

        ConfigurationSection worldsSection = getConfig().getConfigurationSection("worlds");
        if (worldsSection != null && worldsSection.contains(worldName)) {
            return worldsSection.getBoolean(worldName);
        }

        return defaultEnabled;
    }

    /**
     * Whether denied pickup/placement attempts are logged.
     */
    public boolean isLoggingEnabled() {
        return getConfig().getBoolean("logging.enabled", false);
    }

    /**
     * Whether an auto-cleared stuck holder is logged. Separate from {@link #isLoggingEnabled()} -
     * a clear is a one-time confirmation the actual problem got fixed, not a repeating denial, so
     * this defaults to on even though denial logging defaults off.
     */
    public boolean isRemovalsLoggingEnabled() {
        return getConfig().getBoolean("logging.removals", true);
    }

    /**
     * How stuck held-block endermen are handled in a world, same default/override resolution as
     * {@link #isWorldEnabled(String)}. Defaults to {@link HeldBlockHandling#AUTO_CLEAR} - this
     * problem is meant to be resolved with no configuration needed; alerting for manual hunting is
     * an opt-in alternative for players who don't want it resolved for them automatically.
     */
    public HeldBlockHandling getHeldBlockHandling(String worldName) {
        HeldBlockHandling defaultHandling = getDefaultHeldBlockHandling();

        ConfigurationSection heldBlockWorldsSection = getConfig().getConfigurationSection("held-block-worlds");
        if (heldBlockWorldsSection != null && heldBlockWorldsSection.contains(worldName)) {
            return HeldBlockHandling.fromConfig(heldBlockWorldsSection.getString(worldName), defaultHandling);
        }

        return defaultHandling;
    }

    /**
     * The fallback handling used for any world not explicitly listed under "held-block-worlds".
     */
    public HeldBlockHandling getDefaultHeldBlockHandling() {
        return HeldBlockHandling.fromConfig(
                getConfig().getString("default-held-block-handling"), HeldBlockHandling.AUTO_CLEAR);
    }

    /**
     * The single place a world's enabled state actually gets changed - command handlers call this
     * rather than each mutating config themselves, so the arm-on-re-enable side effect only has to
     * be written once and can't be forgotten at a second call site.
     */
    public void setWorldEnabled(String world, boolean value) {
        boolean wasEnabled = isWorldEnabled(world);
        getConfig().set("worlds." + world, value);
        saveConfig();
        if (value && !wasEnabled) {
            heldBlockMonitor.armPendingDiscovery(); // May have accumulated stuck holders while disabled.
        }
    }

    /**
     * The single place the default enabled state actually gets changed - see {@link #setWorldEnabled}.
     */
    public void setDefaultEnabled(boolean value) {
        boolean wasEnabled = getConfig().getBoolean("default-enabled", true);
        getConfig().set("default-enabled", value);
        saveConfig();
        if (value && !wasEnabled) {
            heldBlockMonitor.armPendingDiscovery(); // May have accumulated stuck holders while disabled.
        }
    }

    /**
     * The single place a world's held-block handling actually gets changed - see
     * {@link #setWorldEnabled}. Re-resolves immediately so the new mode applies to already-tracked
     * holders right away, instead of waiting up to ~2 minutes for the next periodic pass.
     */
    public void setWorldHeldBlockHandling(String world, HeldBlockHandling mode) {
        getConfig().set("held-block-worlds." + world, mode.toConfigValue());
        saveConfig();
        heldBlockMonitor.runResolutionPass();
    }

    /**
     * The single place the default held-block handling actually gets changed - see
     * {@link #setWorldHeldBlockHandling}.
     */
    public void setDefaultHeldBlockHandling(HeldBlockHandling mode) {
        getConfig().set("default-held-block-handling", mode.toConfigValue());
        saveConfig();
        heldBlockMonitor.runResolutionPass();
    }

    /**
     * Logs that an enderman's block pickup or placement was denied - to the console (Bukkit's
     * logger already prefixes output with "[EndermanGriefControl]" and its own timestamp, so the
     * message itself stays short) and, matching the Fabric mod's chat announcements, to every
     * player currently in that world.
     */
    public void logEndermanBlockCancel(Block block, String action) {
        String coords = block.getX() + ", " + block.getY() + ", " + block.getZ();
        getLogger().info("Denied " + action + " at (" + coords + ").");

        broadcastToWorld(block.getWorld(), Component.text("[Enderman] ", NamedTextColor.LIGHT_PURPLE)
                .append(Component.text("Denied " + action + " at ", NamedTextColor.GRAY))
                .append(Component.text("(" + coords + ").", NamedTextColor.GREEN)));
    }

    /**
     * Logs that an enderman is still stuck holding a block it can no longer place - deliberately
     * worded distinctly from {@link #logEndermanBlockCancel} so it doesn't blend into routine
     * denial logging when read in a console/log file or in chat. Not gated by
     * {@link #isLoggingEnabled()} - choosing "alert" as the held-block handling mode is itself the
     * opt-in.
     */
    public void logHeldBlockAlert(Enderman enderman) {
        String coords = enderman.getLocation().getBlockX() + ", " + enderman.getLocation().getBlockY()
                + ", " + enderman.getLocation().getBlockZ();
        getLogger().info("holding a block at (" + coords + ").");

        broadcastToWorld(enderman.getWorld(), Component.text("[Enderman] ", NamedTextColor.GOLD)
                .append(Component.text("holding a block at ", NamedTextColor.GRAY))
                .append(Component.text("(" + coords + ").", NamedTextColor.GREEN)));
    }

    /**
     * Logs that a stuck holder was auto-cleared. Gated by {@link #isRemovalsLoggingEnabled()} -
     * separate from denial logging, and on by default.
     */
    public void logHeldBlockCleared(Enderman enderman) {
        if (!isRemovalsLoggingEnabled()) {
            return;
        }

        String coords = enderman.getLocation().getBlockX() + ", " + enderman.getLocation().getBlockY()
                + ", " + enderman.getLocation().getBlockZ();
        getLogger().info("cleared a holder at (" + coords + ").");

        broadcastToWorld(enderman.getWorld(), Component.text("[Enderman] ", NamedTextColor.AQUA)
                .append(Component.text("holding cleared at ", NamedTextColor.GRAY))
                .append(Component.text("(" + coords + ").", NamedTextColor.GREEN)));
    }

    /**
     * Sends a chat message to every player currently in the given world - grief events are
     * inherently per-world here (unlike the Fabric mod, which has no multi-world concept and just
     * broadcasts server-wide), so a denial/alert/clear in one world shouldn't spam players in
     * another.
     */
    private void broadcastToWorld(World world, Component message) {
        for (Player player : world.getPlayers()) {
            player.sendMessage(message);
        }
    }

    private static final List<String> SUBCOMMANDS = List.of("reload", "status", "toggle", "held-block", "set");
    private static final List<String> SET_KEYS = List.of("default", "log-denials", "log-removals", "held-block-default");
    private static final List<String> BOOLEANS = List.of("true", "false");
    private static final List<String> HELD_BLOCK_MODES = List.of("auto-clear", "alert", "off");

    /**
     * Command handler for: /enderman <reload|status|toggle|held-block|set>
     * Lets admins reload config.yml, inspect current settings, and change them
     * in-game, all without restarting the server.
     */
    @Override
    public boolean onCommand(CommandSender sender, Command command, String label, String[] args) {
        if (!command.getName().equalsIgnoreCase("enderman")) {
            return false; // Not our command.
        }

        if (!sender.hasPermission("endermangriefcontrol.admin")) {
            sender.sendMessage("You do not have permission to use this command.");
            return true;
        }

        if (args.length == 0) {
            sender.sendMessage("Usage: /enderman <reload|status|toggle|held-block|set>");
            return true;
        }

        switch (args[0].toLowerCase(Locale.ROOT)) {
            case "reload" -> handleReload(sender);
            case "status" -> handleStatus(sender, args);
            case "toggle" -> handleToggle(sender, args);
            case "held-block" -> handleHeldBlock(sender, args);
            case "set" -> handleSet(sender, args);
            default -> sender.sendMessage("Unknown subcommand. Usage: /enderman <reload|status|toggle|held-block|set>");
        }
        return true;
    }

    private void handleReload(CommandSender sender) {
        reloadConfig();
        heldBlockMonitor.armPendingDiscovery(); // Config may have re-enabled worlds by hand-edit.
        sender.sendMessage("EndermanGriefControl configuration reloaded.");
        getLogger().info("Configuration reloaded by " + sender.getName());
    }

    private void handleStatus(CommandSender sender, String[] args) {
        if (args.length >= 2) {
            String world = args[1];
            sender.sendMessage("World '" + world + "': " + (isWorldEnabled(world) ? "enabled" : "disabled")
                    + ", held-block: " + getHeldBlockHandling(world).toConfigValue());
            return;
        }

        boolean defaultEnabled = getConfig().getBoolean("default-enabled", true);
        sender.sendMessage("Default: " + (defaultEnabled ? "enabled" : "disabled")
                + ", log denials: " + (isLoggingEnabled() ? "enabled" : "disabled")
                + ", log removals: " + (isRemovalsLoggingEnabled() ? "enabled" : "disabled")
                + ", held-block: " + getDefaultHeldBlockHandling().toConfigValue());

        ConfigurationSection worldsSection = getConfig().getConfigurationSection("worlds");
        if (worldsSection != null) {
            for (String world : worldsSection.getKeys(false)) {
                sender.sendMessage("  " + world + ": " + (worldsSection.getBoolean(world) ? "enabled" : "disabled"));
            }
        }

        ConfigurationSection heldBlockWorldsSection = getConfig().getConfigurationSection("held-block-worlds");
        if (heldBlockWorldsSection != null) {
            for (String world : heldBlockWorldsSection.getKeys(false)) {
                sender.sendMessage("  " + world + " held-block: " + getHeldBlockHandling(world).toConfigValue());
            }
        }
    }

    private void handleToggle(CommandSender sender, String[] args) {
        if (args.length < 2) {
            sender.sendMessage("Usage: /enderman toggle <world> [true|false]");
            return;
        }

        String world = args[1];
        boolean newValue = args.length >= 3 ? Boolean.parseBoolean(args[2]) : !isWorldEnabled(world);
        setWorldEnabled(world, newValue);
        sender.sendMessage("World '" + world + "' is now " + (newValue ? "enabled" : "disabled") + ".");
    }

    private void handleHeldBlock(CommandSender sender, String[] args) {
        if (args.length < 3) {
            sender.sendMessage("Usage: /enderman held-block <world> <auto-clear|alert|off>");
            return;
        }

        String world = args[1];
        HeldBlockHandling mode = HeldBlockHandling.fromConfig(args[2], null);
        if (mode == null) {
            sender.sendMessage("Usage: /enderman held-block <world> <auto-clear|alert|off>");
            return;
        }

        setWorldHeldBlockHandling(world, mode);
        sender.sendMessage("Held-block handling for world '" + world + "' is now " + mode.toConfigValue() + ".");
    }

    private void handleSet(CommandSender sender, String[] args) {
        if (args.length < 3) {
            sender.sendMessage("Usage: /enderman set <default|log-denials|log-removals|held-block-default> <value>");
            return;
        }

        switch (args[1].toLowerCase(Locale.ROOT)) {
            case "default" -> {
                boolean value = Boolean.parseBoolean(args[2]);
                setDefaultEnabled(value);
                sender.sendMessage("Default is now " + (value ? "enabled" : "disabled") + ".");
            }
            case "log-denials" -> {
                boolean value = Boolean.parseBoolean(args[2]);
                getConfig().set("logging.enabled", value);
                saveConfig();
                sender.sendMessage("Log denials is now " + (value ? "enabled" : "disabled") + ".");
            }
            case "log-removals" -> {
                boolean value = Boolean.parseBoolean(args[2]);
                getConfig().set("logging.removals", value);
                saveConfig();
                sender.sendMessage("Log removals is now " + (value ? "enabled" : "disabled") + ".");
            }
            case "held-block-default" -> {
                HeldBlockHandling mode = HeldBlockHandling.fromConfig(args[2], null);
                if (mode == null) {
                    sender.sendMessage("Usage: /enderman set held-block-default <auto-clear|alert|off>");
                    return;
                }
                setDefaultHeldBlockHandling(mode);
                sender.sendMessage("Default held-block handling is now " + mode.toConfigValue() + ".");
            }
            default -> sender.sendMessage("Usage: /enderman set <default|log-denials|log-removals|held-block-default> <value>");
        }
    }

    @Override
    public List<String> onTabComplete(CommandSender sender, Command command, String alias, String[] args) {
        if (!command.getName().equalsIgnoreCase("enderman") || !sender.hasPermission("endermangriefcontrol.admin")) {
            return Collections.emptyList();
        }

        if (args.length == 1) {
            return startingWith(SUBCOMMANDS, args[0]);
        }

        String subcommand = args[0].toLowerCase(Locale.ROOT);
        if (args.length == 2
                && (subcommand.equals("toggle") || subcommand.equals("status") || subcommand.equals("held-block"))) {
            List<String> worlds = getServer().getWorlds().stream().map(World::getName).collect(Collectors.toList());
            return startingWith(worlds, args[1]);
        }
        if (args.length == 2 && subcommand.equals("set")) {
            return startingWith(SET_KEYS, args[1]);
        }
        if (args.length == 3 && subcommand.equals("toggle")) {
            return startingWith(BOOLEANS, args[2]);
        }
        if (args.length == 3 && subcommand.equals("held-block")) {
            return startingWith(HELD_BLOCK_MODES, args[2]);
        }
        if (args.length == 3 && subcommand.equals("set")) {
            if (args[1].equalsIgnoreCase("held-block-default")) {
                return startingWith(HELD_BLOCK_MODES, args[2]);
            }
            return startingWith(BOOLEANS, args[2]);
        }

        return Collections.emptyList();
    }

    private List<String> startingWith(List<String> options, String prefix) {
        String lowerPrefix = prefix.toLowerCase(Locale.ROOT);
        return options.stream()
                .filter(option -> option.toLowerCase(Locale.ROOT).startsWith(lowerPrefix))
                .collect(Collectors.toList());
    }
}
