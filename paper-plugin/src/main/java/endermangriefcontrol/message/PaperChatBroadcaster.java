package endermangriefcontrol.message;

import endermangriefcontrol.messaging.ChatBroadcaster;
import net.kyori.adventure.text.Component;
import org.bukkit.World;
import org.bukkit.entity.Player;
import org.bukkit.plugin.java.JavaPlugin;

/**
 * Sends chat messages to a Bukkit {@link World}'s players - grief events are inherently
 * per-world here (unlike the Fabric mod, which has no multi-world concept and used to broadcast
 * server-wide), so a denial/alert/clear in one world shouldn't spam players in another.
 */
public final class PaperChatBroadcaster implements ChatBroadcaster<World> {

    private final JavaPlugin plugin;

    public PaperChatBroadcaster(JavaPlugin plugin) {
        this.plugin = plugin;
    }

    @Override
    public void broadcastToWorld(World world, Component message) {
        for (Player player : world.getPlayers()) {
            player.sendMessage(message);
        }
    }

    @Override
    public Iterable<World> allWorlds() {
        return plugin.getServer().getWorlds();
    }
}
