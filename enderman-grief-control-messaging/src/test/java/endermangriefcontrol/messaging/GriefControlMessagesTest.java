package endermangriefcontrol.messaging;

import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.serializer.plain.PlainTextComponentSerializer;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;

class GriefControlMessagesTest {

    @Test
    void deniedPickupUsesCountInsteadOfCoords() {
        Component message = GriefControlMessages.denied(MessageTemplates.DEFAULTS, DenialType.PICKUP, 4);
        assertEquals("[Enderman] Denied pickup x4.", plainText(message));
    }

    @Test
    void deniedPlacementUsesCountInsteadOfCoords() {
        Component message = GriefControlMessages.denied(MessageTemplates.DEFAULTS, DenialType.PLACEMENT, 1);
        assertEquals("[Enderman] Denied placement x1.", plainText(message));
    }

    @Test
    void heldBlockAlertMatchesLegacyWording() {
        Component message = GriefControlMessages.heldBlockAlert(MessageTemplates.DEFAULTS, "1, 2, 3");
        assertEquals("[Enderman] holding a block at (1, 2, 3).", plainText(message));
    }

    @Test
    void heldBlockClearedUsesCountInsteadOfCoords() {
        Component message = GriefControlMessages.heldBlockCleared(MessageTemplates.DEFAULTS, 3);
        assertEquals("[Enderman] holding block cleared x3.", plainText(message));
    }

    @Test
    void customTemplateChangesOutput() {
        MessageTemplates custom = new MessageTemplates(
                "[Custom] ",
                new MessageTemplate(net.kyori.adventure.text.format.NamedTextColor.RED, "Blocked placement",
                        net.kyori.adventure.text.format.NamedTextColor.WHITE, net.kyori.adventure.text.format.NamedTextColor.BLUE),
                MessageTemplates.DEFAULTS.deniedPickup(),
                MessageTemplates.DEFAULTS.heldBlockAlert(),
                MessageTemplates.DEFAULTS.heldBlockCleared()
        );

        Component message = GriefControlMessages.denied(custom, DenialType.PLACEMENT, 2);
        assertEquals("[Custom] Blocked placement x2.", plainText(message));
    }

    private static String plainText(Component component) {
        return PlainTextComponentSerializer.plainText().serialize(component);
    }
}
