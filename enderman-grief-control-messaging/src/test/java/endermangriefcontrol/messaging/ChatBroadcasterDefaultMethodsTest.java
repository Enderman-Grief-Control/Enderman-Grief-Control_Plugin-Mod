package endermangriefcontrol.messaging;

import net.kyori.adventure.text.Component;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;

class ChatBroadcasterDefaultMethodsTest {

    @Test
    void broadcastToAllWorldsDelegatesToBroadcastToWorldOncePerWorld() {
        List<String> worlds = List.of("overworld", "nether", "the_end");
        Map<String, List<Component>> sent = new java.util.HashMap<>();
        ChatBroadcaster<String> broadcaster = new ChatBroadcaster<>() {
            @Override
            public void broadcastToWorld(String world, Component message) {
                sent.computeIfAbsent(world, w -> new ArrayList<>()).add(message);
            }

            @Override
            public Iterable<String> allWorlds() {
                return worlds;
            }
        };

        Component message = Component.text("hello");
        broadcaster.broadcastToAllWorlds(message);

        for (String world : worlds) {
            assertEquals(List.of(message), sent.get(world));
        }
        assertEquals(worlds.size(), sent.size());
    }
}
