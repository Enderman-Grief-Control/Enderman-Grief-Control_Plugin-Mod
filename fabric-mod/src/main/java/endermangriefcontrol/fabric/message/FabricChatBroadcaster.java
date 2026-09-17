package endermangriefcontrol.fabric.message;

import endermangriefcontrol.messaging.ChatBroadcaster;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.kyori.adventure.platform.fabric.FabricServerAudiences;
import net.kyori.adventure.text.Component;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;

/**
 * Sends chat messages to a {@link ServerLevel}'s players via the Adventure bridge, so this
 * platform builds messages through the same {@code endermangriefcontrol.messaging} model the
 * Paper plugin uses. Scopes broadcasts to the given level's players - a deliberate change from
 * the previous server-wide broadcast, matching the Paper plugin's per-world scoping so a
 * denial/alert/clear in one dimension doesn't spam players in another.
 */
public final class FabricChatBroadcaster implements ChatBroadcaster<ServerLevel> {

    private MinecraftServer server;
    private FabricServerAudiences audiences;

    public void register() {
        ServerLifecycleEvents.SERVER_STARTED.register(startedServer -> {
            server = startedServer;
            audiences = FabricServerAudiences.of(startedServer);
        });
    }

    @Override
    public void broadcastToWorld(ServerLevel level, Component message) {
        audiences.audience(level.players()).sendMessage(message);
    }

    @Override
    public Iterable<ServerLevel> allWorlds() {
        return server.getAllLevels();
    }
}
