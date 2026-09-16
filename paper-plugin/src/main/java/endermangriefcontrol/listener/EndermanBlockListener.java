package endermangriefcontrol.listener;

import endermangriefcontrol.EndermanGriefControlPlugin;
import org.bukkit.Material;
import org.bukkit.block.Block;
import org.bukkit.entity.EntityType;
import org.bukkit.event.EventHandler;
import org.bukkit.event.EventPriority;
import org.bukkit.event.Listener;
import org.bukkit.event.entity.EntityChangeBlockEvent;

/**
 * Listens for block changes caused by entities and cancels
 * them when the entity is an enderman, based on configuration.
 *
 * This prevents endermen from picking up or placing blocks,
 * while leaving all other entity behavior untouched.
 */
public final class EndermanBlockListener implements Listener {

    private final EndermanGriefControlPlugin plugin;

    public EndermanBlockListener(EndermanGriefControlPlugin plugin) {
        this.plugin = plugin;
    }

    @EventHandler(priority = EventPriority.HIGH, ignoreCancelled = true)
    public void onEndermanChangeBlock(EntityChangeBlockEvent event) {
        // Fast-exit if the entity is not an enderman.
        if (event.getEntityType() != EntityType.ENDERMAN) {
            return;
        }

        Block block = event.getBlock();
        String worldName = block.getWorld().getName();

        // Check whether the plugin is enabled for this world.
        if (!plugin.isWorldEnabled(worldName)) {
            return; // Do nothing in worlds where the plugin is disabled.
        }

        // Cancel the block change: enderman cannot pick up or place a block.
        event.setCancelled(true);

        // Optional debug logging. Enderman block-change events always go either to air
        // (pickup) or from air to a block (placement), so getTo() tells us which.
        if (plugin.isLoggingEnabled()) {
            String action = event.getTo() == Material.AIR ? "pickup" : "placement";
            plugin.logEndermanBlockCancel(block, action);
        }
    }
}
