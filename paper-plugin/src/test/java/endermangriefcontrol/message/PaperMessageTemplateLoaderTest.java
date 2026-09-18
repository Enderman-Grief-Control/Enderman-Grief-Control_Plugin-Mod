package endermangriefcontrol.message;

import endermangriefcontrol.messaging.MessageTemplates;
import net.kyori.adventure.text.format.NamedTextColor;
import org.bukkit.configuration.file.YamlConfiguration;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;

class PaperMessageTemplateLoaderTest {

    @Test
    void noMessagesSection_fallsBackToDefaults() {
        YamlConfiguration config = new YamlConfiguration();

        assertEquals(MessageTemplates.DEFAULTS, PaperMessageTemplateLoader.load(config));
    }

    @Test
    void customConfig_overridesWordingAndColors() {
        YamlConfiguration config = new YamlConfiguration();
        config.set("messages.prefix", "[Custom] ");
        config.set("messages.denied-placement.prefix-color", "red");
        config.set("messages.denied-placement.body", "Blocked placement near");
        config.set("messages.denied-placement.body-color", "white");
        config.set("messages.denied-placement.coords-color", "blue");

        MessageTemplates templates = PaperMessageTemplateLoader.load(config);

        assertEquals("[Custom] ", templates.prefixText());
        assertEquals(NamedTextColor.RED, templates.deniedPlacement().prefixColor());
        assertEquals("Blocked placement near", templates.deniedPlacement().body());
        assertEquals(NamedTextColor.WHITE, templates.deniedPlacement().bodyColor());
        assertEquals(NamedTextColor.BLUE, templates.deniedPlacement().coordsColor());
        // Untouched templates keep their defaults.
        assertEquals(MessageTemplates.DEFAULTS.deniedPickup(), templates.deniedPickup());
        assertEquals(MessageTemplates.DEFAULTS.heldBlockAlert(), templates.heldBlockAlert());
        assertEquals(MessageTemplates.DEFAULTS.heldBlockCleared(), templates.heldBlockCleared());
    }

    @Test
    void unrecognizedColor_fallsBackToDefaultInsteadOfFailing() {
        YamlConfiguration config = new YamlConfiguration();
        config.set("messages.denied-placement.prefix-color", "not-a-real-color");

        MessageTemplates templates = PaperMessageTemplateLoader.load(config);

        assertEquals(MessageTemplates.DEFAULTS.deniedPlacement().prefixColor(), templates.deniedPlacement().prefixColor());
    }

    @Test
    void fullExampleYaml_roundTripsExactlyToDefaults() {
        String yaml = """
                messages:
                  prefix: "[Enderman] "
                  denied-placement:
                    prefix-color: dark_purple
                    body: "Denied placement"
                    body-color: gray
                    coords-color: dark_green
                  denied-pickup:
                    prefix-color: light_purple
                    body: "Denied pickup"
                    body-color: gray
                    coords-color: dark_green
                  held-block-alert:
                    prefix-color: gold
                    body: "holding a block at "
                    body-color: gray
                    coords-color: green
                  held-block-cleared:
                    prefix-color: aqua
                    body: "holding cleared at "
                    body-color: gray
                    coords-color: green
                """;
        YamlConfiguration config = YamlConfiguration.loadConfiguration(new java.io.StringReader(yaml));

        assertEquals(MessageTemplates.DEFAULTS, PaperMessageTemplateLoader.load(config));
    }
}
