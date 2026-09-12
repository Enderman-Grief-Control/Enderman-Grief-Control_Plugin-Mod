package endermangriefcontrol.fabric.heldblock;

import endermangriefcontrol.fabric.EndermanGriefControlMod;
import endermangriefcontrol.fabric.debug.TestModeLogger;
import net.fabricmc.fabric.api.entity.event.v1.ServerLivingEntityEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.damagesource.DamageSource;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.entity.LivingEntity;
import net.minecraft.world.entity.monster.EnderMan;
import net.minecraft.world.level.entity.EntityTypeTest;

import java.util.HashSet;
import java.util.Iterator;
import java.util.Set;
import java.util.UUID;

/**
 * Finds and resolves endermen that are stuck holding a block placement can no longer clear (e.g.
 * picked up before the mod was enabled, or during a window where it was toggled off).
 *
 * Discovery can't just run once at server start or on a settings change, because at that instant no
 * player has necessarily loaded the chunks a legacy holder sits in yet (a scan there can come back
 * empty even though the world is otherwise fine) - but those two events are still the right moments
 * to *want* an instant result, since whoever triggered them is typically already online to see it.
 * So each is handled by {@link #armPendingDiscovery()}: run immediately if a player's already
 * online, otherwise poll every few seconds until one is, then run once and stop polling. Separately,
 * a slower periodic pass re-scans and resolves on a fixed interval for the entire server's lifetime
 * regardless of that - it's the backstop for holders that only become findable long after startup (a
 * relocated base, a chunk that unloaded and reloaded, etc). A UUID is only ever removed explicitly
 * (resolved via clearing, or the enderman died) - never inferred from a lookup miss, since that's
 * ambiguous between "unloaded" and "dead."
 */
public final class HeldBlockMonitor {

    private static final long TICKS_PER_SECOND = 20L;
    private static final long RESOLUTION_PERIOD_SECONDS = 60 * 2;
    private static final long RESOLUTION_PERIOD_TICKS = TICKS_PER_SECOND * RESOLUTION_PERIOD_SECONDS;
    private static final long ELIGIBILITY_POLL_PERIOD_SECONDS = 5;
    private static final long ELIGIBILITY_POLL_PERIOD_TICKS = TICKS_PER_SECOND * ELIGIBILITY_POLL_PERIOD_SECONDS;

    private final Set<UUID> knownHolders = new HashSet<>();
    private MinecraftServer server;
    private long tickCounter;
    private boolean pendingEligibilityCheck;

    public void register() {
        ServerLifecycleEvents.SERVER_STARTED.register(startedServer -> {
            server = startedServer;
            armPendingDiscovery();
        });
        ServerTickEvents.END_SERVER_TICK.register(this::onServerTick);
        ServerLivingEntityEvents.AFTER_DEATH.register(this::onEntityDeath);
    }

    /**
     * Requests a discovery scan for "as soon as it can actually find anything" rather than right
     * now: runs immediately if a player is already online (true for basically every settings-change
     * call site, since an admin has to be connected to trigger one), otherwise arms a short poll -
     * every {@value #ELIGIBILITY_POLL_PERIOD_SECONDS}s, checked from {@link #onServerTick} - that
     * fires the scan the moment a player joins, then stops. Calling this again while a poll is
     * already armed is a no-op; it doesn't start a second one.
     */
    public void armPendingDiscovery() {
        if (server != null && !server.getPlayerList().getPlayers().isEmpty()) {
            TestModeLogger.log("armPendingDiscovery: player already online, running discovery now.");
            runDiscoveryScan();
            return;
        }

        if (pendingEligibilityCheck) {
            TestModeLogger.log("armPendingDiscovery: eligibility poll already in progress, no-op.");
            return;
        }

        TestModeLogger.log("armPendingDiscovery: no player online yet, starting eligibility poll.");
        pendingEligibilityCheck = true;
    }

    /**
     * Scans all currently loaded endermen for a carried block and adds any found to the
     * known-holders set. Never removes anything - absence from this scan doesn't mean resolved, it
     * could just mean unloaded. A no-op before the server has started (nothing to scan yet).
     */
    public void runDiscoveryScan() {
        if (server == null) {
            return;
        }

        int foundThisPass = 0;
        for (ServerLevel level : server.getAllLevels()) {
            for (EnderMan enderman : level.getEntities(EntityTypeTest.forClass(EnderMan.class), e -> true)) {
                if (enderman.getCarriedBlock() != null && knownHolders.add(enderman.getUUID())) {
                    foundThisPass++;
                }
            }
        }
        TestModeLogger.log("Discovery scan ran, found " + foundThisPass + " new holder(s) ("
                + knownHolders.size() + " tracked total).");
    }

    private void onServerTick(MinecraftServer tickedServer) {
        tickCounter++;

        if (pendingEligibilityCheck
                && tickCounter % ELIGIBILITY_POLL_PERIOD_TICKS == 0
                && !tickedServer.getPlayerList().getPlayers().isEmpty()) {
            TestModeLogger.log("armPendingDiscovery: eligibility poll succeeded, running discovery.");
            runDiscoveryScan();
            pendingEligibilityCheck = false;
        }

        if (tickCounter % RESOLUTION_PERIOD_TICKS == 0) {
            runDiscoveryScan();
            runResolutionPass();
        }
    }

    /**
     * Re-checks each known-holder UUID directly (not a full re-scan) and acts per the mod's held-
     * block handling mode. A UUID that can't currently be resolved to a loaded entity is left in
     * the set as-is; it'll resolve itself once that chunk loads again.
     */
    private void runResolutionPass() {
        int resolved = 0;
        int alerted = 0;
        int leftUntouched = 0;

        Iterator<UUID> iterator = knownHolders.iterator();
        while (iterator.hasNext()) {
            UUID id = iterator.next();
            Entity found = findEntity(id);
            if (!(found instanceof EnderMan enderman) || enderman.getCarriedBlock() == null) {
                if (found != null) {
                    iterator.remove(); // Found, but no longer holding anything - resolved.
                }
                continue;
            }

            HeldBlockHandling handling = HeldBlockHandling.fromConfig(
                    EndermanGriefControlMod.getConfig().heldBlockHandling, HeldBlockHandling.AUTO_CLEAR);

            switch (handling) {
                case ALERT -> {
                    EndermanGriefControlMod.announceHeldBlockAlert(enderman);
                    alerted++;
                }
                case AUTO_CLEAR -> {
                    enderman.setCarriedBlock(null);
                    EndermanGriefControlMod.announceHeldBlockCleared(enderman);
                    iterator.remove();
                    resolved++;
                }
                case OFF -> leftUntouched++; // Leave it tracked and untouched; picked up again if the mode later changes.
            }
        }

        TestModeLogger.log("Resolution pass ran: " + resolved + " resolved, " + alerted + " alerted, "
                + leftUntouched + " left untouched.");
    }

    private Entity findEntity(UUID id) {
        for (ServerLevel level : server.getAllLevels()) {
            Entity entity = level.getEntity(id);
            if (entity != null) {
                return entity;
            }
        }
        return null;
    }

    private void onEntityDeath(LivingEntity entity, DamageSource damageSource) {
        if (entity instanceof EnderMan) {
            knownHolders.remove(entity.getUUID());
        }
    }

    public boolean isTrackingAnyHolder() {
        return !knownHolders.isEmpty();
    }
}
