package endermangriefcontrol.messaging;

import net.kyori.adventure.text.Component;

/**
 * Sends a chat message to the players of a platform-specific world/level type {@code W}
 * (Bukkit {@code World} on Paper, {@code ServerLevel} on Fabric). Kept generic so this module
 * has no dependency on either platform's runtime.
 */
public interface ChatBroadcaster<W> {

    /** Sends {@code message} to every player currently in {@code world}. */
    void broadcastToWorld(W world, Component message);

    /** Every world/level this platform currently knows about. */
    Iterable<W> allWorlds();

    /**
     * Sends {@code message} to every player on the server, across every world. Delegates to
     * {@link #broadcastToWorld} once per world, so per-world sending semantics only need to be
     * implemented once per platform.
     */
    default void broadcastToAllWorlds(Component message) {
        for (W world : allWorlds()) {
            broadcastToWorld(world, message);
        }
    }
}
