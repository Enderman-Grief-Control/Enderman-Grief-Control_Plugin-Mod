package endermangriefcontrol.messaging;

import net.kyori.adventure.text.Component;

/**
 * Builds the grief-control announcement types from a {@link MessageTemplates} and the dynamic
 * values (denial type + count, or coordinates) each one needs.
 */
public final class GriefControlMessages {

    private GriefControlMessages() {
    }

    public static Component denied(MessageTemplates templates, DenialType type, int count) {
        MessageTemplate template = type == DenialType.PLACEMENT
                ? templates.deniedPlacement()
                : templates.deniedPickup();
        return buildCount(templates, template, count);
    }

    public static Component heldBlockAlert(MessageTemplates templates, String coords) {
        return buildCoords(templates, templates.heldBlockAlert(), coords);
    }

    public static Component heldBlockCleared(MessageTemplates templates, String coords) {
        return buildCoords(templates, templates.heldBlockCleared(), coords);
    }

    private static Component buildCoords(MessageTemplates templates, MessageTemplate template, String coords) {
        return Component.text(templates.prefixText(), template.prefixColor())
                .append(Component.text(template.body(), template.bodyColor()))
                .append(Component.text("(" + coords + ").", template.coordsColor()));
    }

    private static Component buildCount(MessageTemplates templates, MessageTemplate template, int count) {
        return Component.text(templates.prefixText(), template.prefixColor())
                .append(Component.text(template.body(), template.bodyColor()))
                .append(Component.text(" x" + count + ".", template.coordsColor()));
    }
}
