package endermangriefcontrol.messaging;

import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.serializer.plain.PlainTextComponentSerializer;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;

class GriefControlMessagesTest {

    @Test
    void deniedMatchesLegacyWording() {
        Component message = GriefControlMessages.denied(MessageTemplates.DEFAULTS, "pickup", "1, 2, 3");
        assertEquals("[Enderman] Denied pickup at (1, 2, 3).", plainText(message));
    }

    @Test
    void heldBlockAlertMatchesLegacyWording() {
        Component message = GriefControlMessages.heldBlockAlert(MessageTemplates.DEFAULTS, "1, 2, 3");
        assertEquals("[Enderman] holding a block at (1, 2, 3).", plainText(message));
    }

    @Test
    void heldBlockClearedMatchesLegacyWording() {
        Component message = GriefControlMessages.heldBlockCleared(MessageTemplates.DEFAULTS, "1, 2, 3");
        assertEquals("[Enderman] holding cleared at (1, 2, 3).", plainText(message));
    }

    @Test
    void customTemplateChangesOutput() {
        MessageTemplates custom = new MessageTemplates(
                "[Custom] ",
                new MessageTemplate(net.kyori.adventure.text.format.NamedTextColor.RED, "Blocked {action} near ",
                        net.kyori.adventure.text.format.NamedTextColor.WHITE, net.kyori.adventure.text.format.NamedTextColor.BLUE),
                MessageTemplates.DEFAULTS.heldBlockAlert(),
                MessageTemplates.DEFAULTS.heldBlockCleared()
        );

        Component message = GriefControlMessages.denied(custom, "placement", "4, 5, 6");
        assertEquals("[Custom] Blocked placement near (4, 5, 6).", plainText(message));
    }

    private static String plainText(Component component) {
        return PlainTextComponentSerializer.plainText().serialize(component);
    }
}
