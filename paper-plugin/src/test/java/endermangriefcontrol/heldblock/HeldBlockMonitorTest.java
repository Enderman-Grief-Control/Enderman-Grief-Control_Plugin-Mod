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
 * Note: AUTO_CLEAR handling (setCarriedBlock(null), and the "cleared a holder"
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
    void discoveryScan_worldDisabled_doesNotTrackHolder() {
        // Regression test: a disabled world must be genuinely inert - discovery shouldn't even
        // start tracking a holder there, not just skip acting on it during resolution.
        spawnHolder();
        plugin.getConfig().set("worlds.world", false);

        monitor.runDiscoveryScan();

        assertFalse(monitor.isTrackingAnyHolder());
    }

    @Test
    void armPendingDiscovery_playerOnline_runsDiscoveryAndResolutionImmediately() {
        // "alert" (not the AUTO_CLEAR default) to stay clear of the MockBukkit limitation noted
        // above - armPendingDiscovery resolves as well as discovers now, so this exercises that.
        plugin.getConfig().set("default-held-block-handling", "alert");
        server.addPlayer();
        spawnHolder();
        List<LogRecord> records = captureLogRecords();

        monitor.armPendingDiscovery();

        assertTrue(monitor.isTrackingAnyHolder());
        assertTrue(records.stream().anyMatch(r -> r.getMessage().equals("holding a block at (10, 64, -30).")));
    }

    @Test
    void armPendingDiscovery_noPlayerOnline_waitsThenResolvesOncePlayerJoins() {
        plugin.getConfig().set("default-held-block-handling", "alert");
        spawnHolder();
        List<LogRecord> records = captureLogRecords();

        monitor.armPendingDiscovery();
        assertFalse(monitor.isTrackingAnyHolder());

        server.addPlayer();
        server.getScheduler().performTicks(20L * 5 + 1); // past the eligibility poll's first tick

        assertTrue(monitor.isTrackingAnyHolder());
        assertTrue(records.stream().anyMatch(r -> r.getMessage().equals("holding a block at (10, 64, -30).")));
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

        assertTrue(records.stream().anyMatch(r -> r.getMessage().equals("holding a block at (10, 64, -30).")));
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
    void resolutionPass_worldDisabled_leavesHolderUntouchedEvenInAutoClearMode() {
        // Regression test: disabling a world must stop the held-block monitor from acting on it
        // too, not just stop new pickups/placements from being denied there.
        Enderman enderman = spawnHolder();
        monitor.runDiscoveryScan();
        plugin.getConfig().set("worlds.world", false);

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
