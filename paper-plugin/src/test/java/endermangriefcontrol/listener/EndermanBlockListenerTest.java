package endermangriefcontrol.listener;

import endermangriefcontrol.EndermanGriefControlPlugin;
import org.bukkit.Material;
import org.bukkit.entity.Entity;
import org.bukkit.entity.EntityType;
import org.bukkit.event.entity.EntityChangeBlockEvent;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockbukkit.mockbukkit.MockBukkit;
import org.mockbukkit.mockbukkit.ServerMock;
import org.mockbukkit.mockbukkit.block.BlockMock;
import org.mockbukkit.mockbukkit.world.WorldMock;

import java.util.ArrayList;
import java.util.List;
import java.util.logging.Handler;
import java.util.logging.LogRecord;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class EndermanBlockListenerTest {

    private ServerMock server;
    private EndermanGriefControlPlugin plugin;
    private WorldMock world;

    @BeforeEach
    void setUp() {
        server = MockBukkit.mock();
        plugin = MockBukkit.load(EndermanGriefControlPlugin.class);
        world = server.addSimpleWorld("world");
    }

    @AfterEach
    void tearDown() {
        MockBukkit.unmock();
    }

    private EntityChangeBlockEvent fireBlockChangeEvent(EntityType entityType, Material to) {
        Entity entity = mock(Entity.class);
        when(entity.getType()).thenReturn(entityType);

        BlockMock block = world.getBlockAt(10, 64, -30);
        EntityChangeBlockEvent event = new EntityChangeBlockEvent(entity, block, to.createBlockData());

        server.getPluginManager().callEvent(event);
        return event;
    }

    private EntityChangeBlockEvent firePickupEvent(EntityType entityType) {
        return fireBlockChangeEvent(entityType, Material.AIR);
    }

    @Test
    void endermanBlockChange_isCancelled_whenWorldEnabled() {
        EntityChangeBlockEvent event = firePickupEvent(EntityType.ENDERMAN);

        assertTrue(event.isCancelled());
    }

    @Test
    void endermanBlockChange_isNotCancelled_whenWorldDisabled() {
        plugin.getConfig().set("default-enabled", false);

        EntityChangeBlockEvent event = firePickupEvent(EntityType.ENDERMAN);

        assertFalse(event.isCancelled());
    }

    @Test
    void nonEndermanBlockChange_isNotTouched() {
        EntityChangeBlockEvent event = firePickupEvent(EntityType.SILVERFISH);

        assertFalse(event.isCancelled());
    }

    @Test
    void loggingEnabled_logsPickupMessage() {
        plugin.getConfig().set("logging.enabled", true);
        List<LogRecord> records = captureLogRecords();

        firePickupEvent(EntityType.ENDERMAN);

        assertTrue(records.stream().anyMatch(r -> r.getMessage().equals("Denied pickup at (10, 64, -30).")));
    }

    @Test
    void loggingEnabled_logsPlacementMessage() {
        plugin.getConfig().set("logging.enabled", true);
        List<LogRecord> records = captureLogRecords();

        fireBlockChangeEvent(EntityType.ENDERMAN, Material.DIRT);

        assertTrue(records.stream().anyMatch(r -> r.getMessage().equals("Denied placement at (10, 64, -30).")));
    }

    @Test
    void loggingDisabled_doesNotLog() {
        List<LogRecord> records = captureLogRecords();

        firePickupEvent(EntityType.ENDERMAN);

        assertTrue(records.isEmpty());
    }

    private List<LogRecord> captureLogRecords() {
        List<LogRecord> records = new ArrayList<>();
        Handler handler = new Handler() {
            @Override
            public void publish(LogRecord record) {
                records.add(record);
            }

            @Override
            public void flush() {
            }

            @Override
            public void close() {
            }
        };
        plugin.getLogger().addHandler(handler);
        return records;
    }
}
