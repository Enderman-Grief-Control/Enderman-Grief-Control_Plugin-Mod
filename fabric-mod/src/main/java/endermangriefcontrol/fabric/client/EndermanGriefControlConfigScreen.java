package endermangriefcontrol.fabric.client;

import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.components.CycleButton;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.network.chat.Component;
import endermangriefcontrol.fabric.EndermanGriefControlConfig;
import endermangriefcontrol.fabric.EndermanGriefControlMod;

/**
 * A hand-rolled vanilla settings screen (no Cloth Config dependency, just two booleans) shown by
 * Mod Menu. Mutates the shared static config instance in place and saves on every change, so
 * effects are immediate: the pickup/placement mixins already re-read that same instance on every
 * check, so no restart or reload is needed.
 */
public final class EndermanGriefControlConfigScreen extends Screen {

    private static final int BUTTON_WIDTH = 200;
    private static final int BUTTON_HEIGHT = 20;
    private static final int SPACING = 24;

    private final Screen parent;

    public EndermanGriefControlConfigScreen(Screen parent) {
        super(Component.literal("EndermanGriefControl"));
        this.parent = parent;
    }

    @Override
    protected void init() {
        EndermanGriefControlConfig config = EndermanGriefControlMod.getConfig();
        int centerX = this.width / 2 - BUTTON_WIDTH / 2;
        int startY = this.height / 2 - SPACING;

        this.addRenderableWidget(CycleButton.onOffBuilder(config.enabled)
                .create(centerX, startY, BUTTON_WIDTH, BUTTON_HEIGHT,
                        Component.literal("Prevent Enderman Grief"),
                        (button, value) -> {
                            config.enabled = value;
                            config.save();
                        }));

        this.addRenderableWidget(CycleButton.onOffBuilder(config.loggingEnabled)
                .create(centerX, startY + SPACING, BUTTON_WIDTH, BUTTON_HEIGHT,
                        Component.literal("Log Blocked Actions"),
                        (button, value) -> {
                            config.loggingEnabled = value;
                            config.save();
                        }));

        this.addRenderableWidget(Button.builder(Component.literal("Done"), button -> this.onClose())
                .bounds(centerX, startY + SPACING * 2, BUTTON_WIDTH, BUTTON_HEIGHT)
                .build());
    }

    @Override
    public void onClose() {
        if (this.minecraft != null) {
            this.minecraft.setScreen(this.parent);
        }
    }
}
