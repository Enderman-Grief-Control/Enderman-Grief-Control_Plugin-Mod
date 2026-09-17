package endermangriefcontrol.messaging;

import net.kyori.adventure.text.Component;

/**
 * Builds the three grief-control announcement types from a {@link MessageTemplates} and the
 * dynamic values (action text, coordinates) each one needs.
 */
public final class GriefControlMessages {

    private GriefControlMessages() {
    }

    public static Component denied(MessageTemplates templates, String action, String coords) {
        return build(templates, templates.denied(), action, coords);
    }

    public static Component heldBlockAlert(MessageTemplates templates, String coords) {
        return build(templates, templates.heldBlockAlert(), null, coords);
    }

    public static Component heldBlockCleared(MessageTemplates templates, String coords) {
        return build(templates, templates.heldBlockCleared(), null, coords);
    }

    private static Component build(MessageTemplates templates, MessageTemplate template, String action, String coords) {
        String body = action == null ? template.body() : template.body().replace("{action}", action);
        return Component.text(templates.prefixText(), template.prefixColor())
                .append(Component.text(body, template.bodyColor()))
                .append(Component.text("(" + coords + ").", template.coordsColor()));
    }
}
