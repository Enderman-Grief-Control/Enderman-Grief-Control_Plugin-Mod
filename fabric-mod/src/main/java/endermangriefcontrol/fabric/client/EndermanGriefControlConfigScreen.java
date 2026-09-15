package endermangriefcontrol.fabric.client;

import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.components.CycleButton;
import net.minecraft.client.gui.components.StringWidget;
import net.minecraft.client.gui.components.Tooltip;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.network.chat.Component;
import endermangriefcontrol.fabric.EndermanGriefControlConfig;
import endermangriefcontrol.fabric.EndermanGriefControlMod;
import endermangriefcontrol.fabric.heldblock.HeldBlockHandling;

/**
 * A hand-rolled vanilla settings screen (no Cloth Config dependency) shown by Mod Menu. Cycling a
 * button only updates this screen's own pending fields, not the live config - nothing is written
 * (and none of HeldBlockMonitor's side effects fire) until "Done" is pressed. This matters
 * specifically for a cycle button like "Stuck Holders": clicking through Auto-Clear -> Alert -> Off
 * to reach Off would otherwise instantly apply Auto-Clear and Alert along the way, actually clearing
 * or alerting on real holders before the player ever meant to commit to anything.
 *
 * Laid out in two groups: "Mode" (Prevent Enderman Grief, Stuck Holders) and "Announcements" (Log
 * Removals, Log Denied Attempts). "Prevent Enderman Grief" is the overall kill switch (see
 * HeldBlockMonitor), so the other three widgets grey out and stop accepting input whenever it's
 * pending-off, to make that visually obvious before "Save & Quit" is even pressed. "Cancel" is the
 * same as closing the screen any other way - discards every pending change.
 */
public final class EndermanGriefControlConfigScreen extends Screen {

    private static final int BUTTON_WIDTH = 200;
    private static final int BUTTON_HEIGHT = 20;
    private static final int SPACING = 24;
    private static final int BOTTOM_ROW_GAP = SPACING;
    private static final int BOTTOM_BUTTON_GAP = 4;
    private static final int BOTTOM_BUTTON_WIDTH = (BUTTON_WIDTH - BOTTOM_BUTTON_GAP) / 2;

    private final Screen parent;

    private boolean pendingEnabled;
    private boolean pendingLogRemovals;
    private boolean pendingLoggingEnabled;
    private HeldBlockHandling pendingHeldBlockHandling;

    private CycleButton<HeldBlockHandling> stuckHoldersButton;
    private CycleButton<Boolean> logRemovalsButton;
    private CycleButton<Boolean> logDeniedButton;

    public EndermanGriefControlConfigScreen(Screen parent) {
        super(Component.literal("EndermanGriefControl"));
        this.parent = parent;
    }

    @Override
    protected void init() {
        EndermanGriefControlConfig config = EndermanGriefControlMod.getConfig();
        pendingEnabled = config.enabled;
        pendingLogRemovals = config.logRemovals;
        pendingLoggingEnabled = config.loggingEnabled;
        pendingHeldBlockHandling = HeldBlockHandling.fromConfig(config.heldBlockHandling, HeldBlockHandling.AUTO_CLEAR);

        int centerX = this.width / 2 - BUTTON_WIDTH / 2;
        int startY = this.height / 2 - SPACING * 3 - BOTTOM_ROW_GAP / 2;

        this.addRenderableWidget(new StringWidget(centerX, startY, BUTTON_WIDTH, BUTTON_HEIGHT,
                Component.literal("Mode"), this.font).alignCenter());

        this.addRenderableWidget(CycleButton.onOffBuilder(pendingEnabled)
                .withTooltip(EndermanGriefControlConfigScreen::preventionTooltip)
                .create(centerX, startY + SPACING, BUTTON_WIDTH, BUTTON_HEIGHT,
                        Component.literal("Prevent Enderman Grief"),
                        (button, value) -> {
                            pendingEnabled = value;
                            updateDependentWidgetsActive();
                        }));

        stuckHoldersButton = this.addRenderableWidget(
                CycleButton.<HeldBlockHandling>builder(EndermanGriefControlConfigScreen::displayName)
                        .withValues(HeldBlockHandling.AUTO_CLEAR, HeldBlockHandling.ALERT, HeldBlockHandling.OFF)
                        .withInitialValue(pendingHeldBlockHandling)
                        .withTooltip    (EndermanGriefControlConfigScreen::heldBlockTooltip)
                        .create(centerX, startY + SPACING * 2, BUTTON_WIDTH, BUTTON_HEIGHT,
                                Component.literal("Stuck Holders"),
                                (button, value) -> pendingHeldBlockHandling = value));

        this.addRenderableWidget(new StringWidget(centerX, startY + SPACING * 3, BUTTON_WIDTH, BUTTON_HEIGHT,
                Component.literal("Announcements"), this.font).alignCenter());

        logRemovalsButton = this.addRenderableWidget(CycleButton.onOffBuilder(pendingLogRemovals)
                .withTooltip(EndermanGriefControlConfigScreen::logRemovalsTooltip)
                .create(centerX, startY + SPACING * 4, BUTTON_WIDTH, BUTTON_HEIGHT,
                        Component.literal("Log Removals"),
                        (button, value) -> pendingLogRemovals = value));

        logDeniedButton = this.addRenderableWidget(CycleButton.onOffBuilder(pendingLoggingEnabled)
                .withTooltip(EndermanGriefControlConfigScreen::logDeniedTooltip)
                .create(centerX, startY + SPACING * 5, BUTTON_WIDTH, BUTTON_HEIGHT,
                        Component.literal("Log Denied Attempts"),
                        (button, value) -> pendingLoggingEnabled = value));

        int bottomRowY = startY + SPACING * 6 + BOTTOM_ROW_GAP;

        this.addRenderableWidget(Button.builder(Component.literal("Cancel"), button -> this.onClose())
                .bounds(centerX, bottomRowY, BOTTOM_BUTTON_WIDTH, BUTTON_HEIGHT)
                .build());

        this.addRenderableWidget(Button.builder(Component.literal("Save & Quit"), button -> {
                    applyPendingChanges();
                    this.onClose();
                })
                .bounds(centerX + BOTTOM_BUTTON_WIDTH + BOTTOM_BUTTON_GAP, bottomRowY, BOTTOM_BUTTON_WIDTH, BUTTON_HEIGHT)
                .build());

        updateDependentWidgetsActive();
    }

    /**
     * "Prevent Enderman Grief" is the overall kill switch - while it's pending-off, nothing else on
     * this screen does anything meaningful, so grey those three widgets out and block interaction
     * with them rather than let the player set values that won't take effect.
     */
    private void updateDependentWidgetsActive() {
        stuckHoldersButton.active = pendingEnabled;
        logRemovalsButton.active = pendingEnabled;
        logDeniedButton.active = pendingEnabled;
    }

    /**
     * Commits every pending change at once. "enabled" and "held-block handling" go through
     * EndermanGriefControlMod's setters - the same ones EndermanCommand's subcommands call - since
     * each has a HeldBlockMonitor side effect (re-arming discovery, re-resolving immediately) that
     * only needs to be written once. The two logging flags have no such side effect, so they're
     * saved directly.
     */
    private void applyPendingChanges() {
        EndermanGriefControlMod.setEnabled(pendingEnabled);

        EndermanGriefControlConfig config = EndermanGriefControlMod.getConfig();
        config.logRemovals = pendingLogRemovals;
        config.loggingEnabled = pendingLoggingEnabled;
        config.save();

        EndermanGriefControlMod.setHeldBlockHandling(pendingHeldBlockHandling);
    }

    private static Component displayName(HeldBlockHandling handling) {
        return switch (handling) {
            case AUTO_CLEAR -> Component.literal("Auto-Clear");
            case ALERT -> Component.literal("Alert");
            case OFF -> Component.literal("Off");
        };
    }

    private static Tooltip preventionTooltip(boolean enabled) {
        return Tooltip.create(Component.literal(enabled
                ? "Endermen cannot pick up or place blocks. Every other mob is unaffected."
                : "Vanilla enderman griefing behavior is restored."));
    }

    private static Tooltip logRemovalsTooltip(boolean logRemovals) {
        return Tooltip.create(Component.literal(logRemovals
                ? "Announces when a stuck holder is auto-cleared, in chat and the log file."
                : "A stuck holder being auto-cleared is not announced or logged."));
    }

    private static Tooltip logDeniedTooltip(boolean loggingEnabled) {
        return Tooltip.create(Component.literal(loggingEnabled
                ? "Announces each prevented pickup/placement attempt in chat and the log file."
                : "Prevented pickup/placement attempts are not announced or logged."));
    }

    private static Tooltip heldBlockTooltip(HeldBlockHandling handling) {
        return Tooltip.create(Component.literal(switch (handling) {
            case AUTO_CLEAR -> "Removes the block a stuck enderman is holding. Nothing is dropped.";
            case ALERT -> "Leaves the block, but periodically announces the enderman's location so you can hunt it down yourself.";
            case OFF -> "Leaves stuck holders alone entirely.";
        }));
    }

    @Override
    public void onClose() {
        if (this.minecraft != null) {
            this.minecraft.setScreen(this.parent);
        }
    }
}
