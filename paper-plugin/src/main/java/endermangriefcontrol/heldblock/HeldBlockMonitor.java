package endermangriefcontrol.heldblock;

import endermangriefcontrol.EndermanGriefControlPlugin;
import endermangriefcontrol.debug.TestModeLogger;
import org.bukkit.World;
import org.bukkit.entity.Enderman;
import org.bukkit.entity.Entity;
import org.bukkit.entity.EntityType;
import org.bukkit.event.EventHandler;
import org.bukkit.event.Listener;
import org.bukkit.event.entity.EntityDeathEvent;
import org.bukkit.scheduler.BukkitTask;

import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.Map;
import java.util.Set;
import java.util.UUID;

/**
 * Finds and resolves endermen that are stuck holding a block placement can no longer clear (e.g.
 * picked up before the plugin was enabled, or during a window where it was toggled off).
 *
 * "Disabled" is a true kill switch, per world: both {@link #runDiscoveryScan()} and
 * {@link #runResolutionPass()} skip a disabled world entirely - no scanning, no tracking, no
 * resolving there. Unlike the Fabric mod there's no single global flag to gate {@link
 * #armPendingDiscovery()} itself on (enabled state is per-world here), so it keeps arming/polling
 * regardless - the per-world skip inside discovery/resolution is what makes a disabled world
 * genuinely inert, not a check at the arming stage.
 *
 * While enabled: discovery can't just run once at plugin enable or on a settings change, because
 * at that instant no player has necessarily loaded the chunks a legacy holder sits in yet (a scan
 * there can come back empty even though the world is otherwise fine) - but those two events are
 * still the right moments to *want* an instant result, since whoever triggered them is typically
 * already online to see it. So each is handled by {@link #armPendingDiscovery()}: discover and
 * resolve immediately if a player's already online, otherwise poll every few seconds until one is,
 * then run once and stop polling. Resolving right alongside discovery (not just discovering) is
 * what makes re-enabling actually clear/alert on a holder picked up while disabled instead of
 * leaving it sitting until the next periodic pass, up to {@value #RESOLUTION_PERIOD_SECONDS}s
 * later - and it's handled here, at the one spot every command that flips a world's enabled state
 * funnels through, rather than something a caller has to separately remember to trigger.
 * Separately, a slower periodic pass re-scans and resolves on a fixed interval for
 * the entire plugin lifetime - it's the backstop for holders that only become findable long after
 * startup (a relocated base, a chunk that unloaded and reloaded, etc). A UUID is only ever removed
 * explicitly (resolved via clearing, or the enderman died) — never inferred from a lookup miss,
 * since that's ambiguous between "unloaded" and "dead."
 */
public final class HeldBlockMonitor implements Listener {

    private static final long TICKS_PER_SECOND = 20L;
    private static final long RESOLUTION_PERIOD_SECONDS = 60 * 2;
    private static final long RESOLUTION_PERIOD_TICKS = TICKS_PER_SECOND * RESOLUTION_PERIOD_SECONDS;
    private static final long ELIGIBILITY_POLL_PERIOD_SECONDS = 5;
    private static final long ELIGIBILITY_POLL_PERIOD_TICKS = TICKS_PER_SECOND * ELIGIBILITY_POLL_PERIOD_SECONDS;

    private final EndermanGriefControlPlugin plugin;
    private final Set<UUID> knownHolders = new HashSet<>();
    private BukkitTask eligibilityPollTask;

    public HeldBlockMonitor(EndermanGriefControlPlugin plugin) {
        this.plugin = plugin;
    }

    /**
     * Starts the periodic discovery+resolution cycle. Called once from the plugin's onEnable().
     */
    public void start() {
        armPendingDiscovery();
        plugin.getServer().getScheduler().runTaskTimer(
                plugin, this::runDiscoveryAndResolutionPass, RESOLUTION_PERIOD_TICKS, RESOLUTION_PERIOD_TICKS);
    }

    void runDiscoveryAndResolutionPass() {
        runDiscoveryScan();
        runResolutionPass();
    }

    /**
     * Requests a discovery scan for "as soon as it can actually find anything" rather than right
     * now: runs immediately if a player is already online (true for basically every settings-change
     * call site, since an admin has to be connected to trigger one), otherwise arms a short poll -
     * every {@value #ELIGIBILITY_POLL_PERIOD_SECONDS}s - that fires the scan the moment a player
     * joins, then stops. Calling this again while a poll is already armed is a no-op; it doesn't
     * start a second one.
     */
    public void armPendingDiscovery() {
        if (!plugin.getServer().getOnlinePlayers().isEmpty()) {
            TestModeLogger.log("armPendingDiscovery: player already online, running discovery now.");
            runDiscoveryAndResolutionPass();
            return;
        }

        if (eligibilityPollTask != null) {
            TestModeLogger.log("armPendingDiscovery: eligibility poll already in progress, no-op.");
            return;
        }

        TestModeLogger.log("armPendingDiscovery: no player online yet, starting eligibility poll.");
        eligibilityPollTask = plugin.getServer().getScheduler().runTaskTimer(
                plugin, this::pollEligibility, ELIGIBILITY_POLL_PERIOD_TICKS, ELIGIBILITY_POLL_PERIOD_TICKS);
    }

    private void pollEligibility() {
        if (plugin.getServer().getOnlinePlayers().isEmpty()) {
            return;
        }

        TestModeLogger.log("armPendingDiscovery: eligibility poll succeeded, running discovery.");
        runDiscoveryAndResolutionPass();
        eligibilityPollTask.cancel();
        eligibilityPollTask = null;
    }

    /**
     * Scans all currently loaded endermen for a carried block and adds any found to the
     * known-holders set. Never removes anything — absence from this scan doesn't mean resolved,
     * it could just mean unloaded. Skips disabled worlds entirely - "disabled" means the plugin
     * doesn't act on that world at all, not just that it stops denying new pickups/placements.
     */
    public void runDiscoveryScan() {
        int foundThisPass = 0;
        for (World world : plugin.getServer().getWorlds()) {
            if (!plugin.isWorldEnabled(world.getName())) {
                continue;
            }
            for (Enderman enderman : world.getEntitiesByClass(Enderman.class)) {
                if (enderman.getCarriedBlock() != null && knownHolders.add(enderman.getUniqueId())) {
                    foundThisPass++;
                }
            }
        }
        TestModeLogger.log("Discovery scan ran, found " + foundThisPass + " new holder(s) ("
                + knownHolders.size() + " tracked total).");
    }

    /**
     * Re-checks each known-holder UUID directly (not a full re-scan) and acts per that enderman's
     * world handling mode. A UUID that can't currently be resolved to a loaded entity is left in
     * the set as-is; it'll resolve itself once that chunk loads again. A holder in a currently
     * disabled world is left tracked and untouched too - "disabled" means the plugin doesn't act
     * on that world at all, not just that it stops denying new pickups/placements.
     */
    public void runResolutionPass() {
        int resolved = 0;
        int alerted = 0;
        int leftUntouched = 0;
        int skippedDisabled = 0;
        Map<World, Integer> clearedPerWorld = new HashMap<>();

        Iterator<UUID> iterator = knownHolders.iterator();
        while (iterator.hasNext()) {
            UUID id = iterator.next();
            Entity entity = plugin.getServer().getEntity(id);
            if (!(entity instanceof Enderman enderman) || enderman.getCarriedBlock() == null) {
                if (entity != null) {
                    iterator.remove(); // Found, but no longer holding anything - resolved.
                }
                continue;
            }

            if (!plugin.isWorldEnabled(enderman.getWorld().getName())) {
                skippedDisabled++;
                continue;
            }

            switch (plugin.getHeldBlockHandling(enderman.getWorld().getName())) {
                case ALERT -> {
                    plugin.logHeldBlockAlert(enderman);
                    alerted++;
                }
                case AUTO_CLEAR -> {
                    enderman.setCarriedBlock(null);
                    plugin.logHeldBlockCleared(enderman);
                    clearedPerWorld.merge(enderman.getWorld(), 1, Integer::sum);
                    iterator.remove();
                    resolved++;
                }
                case OFF -> leftUntouched++; // Leave it tracked and untouched; picked up again if the mode later changes.
            }
        }

        for (Map.Entry<World, Integer> entry : clearedPerWorld.entrySet()) {
            plugin.announceHeldBlockClearedBatch(entry.getKey(), entry.getValue());
        }

        TestModeLogger.log("Resolution pass ran: " + resolved + " resolved, " + alerted + " alerted, "
                + leftUntouched + " left untouched, " + skippedDisabled + " skipped (world disabled).");
    }

    @EventHandler
    public void onEntityDeath(EntityDeathEvent event) {
        if (event.getEntityType() == EntityType.ENDERMAN) {
            knownHolders.remove(event.getEntity().getUniqueId());
        }
    }

    public boolean isTrackingAnyHolder() {
        return !knownHolders.isEmpty();
    }
}
