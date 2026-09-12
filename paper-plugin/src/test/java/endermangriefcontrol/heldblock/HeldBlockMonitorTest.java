package endermangriefcontrol.heldblock;

import endermangriefcontrol.EndermanGriefControlPlugin;
import org.bukkit.Location;
import org.bukkit.Material;
import org.bukkit.damage.DamageSource;
import org.bukkit.damage.DamageType;
import org.bukkit.entity.Enderman;
import org.bukkit.event.entity.EntityDeathEvent;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockbukkit.mockbukkit.MockBukkit;
import org.mockbukkit.mockbukkit.ServerMock;
import org.mockbukkit.mockbukkit.world.WorldMock;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.logging.Handler;
import java.util.logging.LogRecord;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * Note: AUTO_CLEAR handling (setCarriedBlock(null), and the "Cleared a persisted holder"
 * confirmation log that follows it) can't be exercised here - MockBukkit v4.108.0's EndermanMock
 * throws on a null carried-block argument even though the real Bukkit API documents
 * setCarriedBlock/getCarriedBlock as nullable, and WorldMock.addEntity() (the only way to inject a
 * working substitute double) is unimplemented in this version. That path - which includes the
 * default, unconfigured behavior, since AUTO_CLEAR is the default handling - is covered by the
 * manual QA checklist instead. Tests below that call runResolutionPass() explicitly set the mode
 * to "alert" or "off" to stay clear of it.
 */
class HeldBlockMonitorTest {

    private ServerMock server;
    private EndermanGriefControlPlugin plugin;
    private WorldMock world;
    private HeldBlockMonitor monitor;

    @BeforeEach
    void setUp() {
        server = MockBukkit.mock();
        plugin = MockBukkit.load(EndermanGriefControlPlugin.class);
        world = server.addSimpleWorld("world");
        monitor = new HeldBlockMonitor(plugin);
    }

    @AfterEach
    void tearDown() {
        MockBukkit.unmock();
    }

    private Enderman spawnHolder() {
        Enderman enderman = world.spawn(new Location(world, 10, 64, -30), Enderman.class);
        enderman.setCarriedBlock(Material.DIRT.createBlockData());
        return enderman;
    }

    @Test
    void discoveryScan_findsHolder_startsTracking() {
        spawnHolder();

        monitor.runDiscoveryScan();

        assertTrue(monitor.isTrackingAnyHolder());
    }

    @Test
    void discoveryScan_emptyWorld_tracksNothing() {
        monitor.runDiscoveryScan();

        assertFalse(monitor.isTrackingAnyHolder());
    }

    @Test
    void discoveryScan_calledAgainLater_stillFindsPreExistingHolder() {
        // Regression test: discovery must not be a one-shot - a holder that only becomes visible
        // (chunk loaded) after the first scan must still be picked up by a later scan.
        monitor.runDiscoveryScan();
        assertFalse(monitor.isTrackingAnyHolder());

        spawnHolder();
        monitor.runDiscoveryScan();

        assertTrue(monitor.isTrackingAnyHolder());
    }

    @Test
    void armPendingDiscovery_playerOnline_runsDiscoveryImmediately() {
        server.addPlayer();
        spawnHolder();

        monitor.armPendingDiscovery();

        assertTrue(monitor.isTrackingAnyHolder());
    }

    @Test
    void armPendingDiscovery_noPlayerOnline_waitsThenFindsHolderOncePlayerJoins() {
        spawnHolder();

        monitor.armPendingDiscovery();
        assertFalse(monitor.isTrackingAnyHolder());

        server.addPlayer();
        server.getScheduler().performTicks(20L * 5 + 1); // past the eligibility poll's first tick

        assertTrue(monitor.isTrackingAnyHolder());
    }

    @Test
    void armPendingDiscovery_calledAgainWhilePolling_doesNotScheduleASecondPoll() {
        monitor.armPendingDiscovery();
        int pendingAfterFirstArm = server.getScheduler().getPendingTasks().size();

        monitor.armPendingDiscovery();

        assertEquals(pendingAfterFirstArm, server.getScheduler().getPendingTasks().size());
    }

    @Test
    void resolutionPass_alertMode_logsDistinctMessage_andKeepsTracking() {
        plugin.getConfig().set("default-held-block-handling", "alert");
        spawnHolder();
        monitor.runDiscoveryScan();
        List<LogRecord> records = captureLogRecords();

        monitor.runResolutionPass();

        assertTrue(records.stream().anyMatch(r -> r.getMessage().equals("Still holding a block at (10, 64, -30).")));
        assertTrue(monitor.isTrackingAnyHolder());
    }

    @Test
    void resolutionPass_offMode_leavesHolderUntouchedButTracked() {
        plugin.getConfig().set("default-held-block-handling", "off");
        Enderman enderman = spawnHolder();
        monitor.runDiscoveryScan();

        monitor.runResolutionPass();

        assertNotNull(enderman.getCarriedBlock());
        assertTrue(monitor.isTrackingAnyHolder());
    }

    @Test
    void endermanDeath_removesFromTracking() {
        Enderman enderman = spawnHolder();
        monitor.runDiscoveryScan();

        monitor.onEntityDeath(new EntityDeathEvent(
                enderman, DamageSource.builder(DamageType.GENERIC).build(), Collections.emptyList()));

        assertFalse(monitor.isTrackingAnyHolder());
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
