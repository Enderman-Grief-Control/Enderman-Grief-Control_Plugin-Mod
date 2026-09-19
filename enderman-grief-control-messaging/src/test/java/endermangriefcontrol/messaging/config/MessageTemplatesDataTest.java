package endermangriefcontrol.messaging.config;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import endermangriefcontrol.messaging.MessageTemplates;
import net.kyori.adventure.text.format.NamedTextColor;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;

class MessageTemplatesDataTest {

    private static final Gson GSON = new GsonBuilder().setPrettyPrinting().create();

    @Test
    void defaultsConvertBackToMessageTemplatesDefaults() {
        assertEquals(MessageTemplates.DEFAULTS, MessageTemplatesData.defaults().toMessageTemplates());
    }

    @Test
    void gsonRoundTripPreservesCustomWording() {
        MessageTemplatesData data = MessageTemplatesData.defaults();
        data.prefix = "[Custom] ";
        data.deniedPlacement.body = "Blocked placement near";
        data.deniedPlacement.prefixColor = "red";

        String json = GSON.toJson(data);
        MessageTemplatesData roundTripped = GSON.fromJson(json, MessageTemplatesData.class);

        assertNotNull(roundTripped);
        MessageTemplates templates = roundTripped.toMessageTemplates();
        assertEquals("[Custom] ", templates.prefixText());
        assertEquals("Blocked placement near", templates.deniedPlacement().body());
        assertEquals(NamedTextColor.RED, templates.deniedPlacement().prefixColor());
    }

    @Test
    void unrecognizedColorFallsBackToDefaultInsteadOfFailing() {
        MessageTemplatesData data = MessageTemplatesData.defaults();
        data.deniedPlacement.prefixColor = "not-a-real-color";

        MessageTemplates templates = data.toMessageTemplates();
        assertEquals(MessageTemplates.DEFAULTS.deniedPlacement().prefixColor(), templates.deniedPlacement().prefixColor());
    }

    @Test
    void missingSectionInJsonFallsBackToDefaults() {
        MessageTemplatesData data = GSON.fromJson("{}", MessageTemplatesData.class);

        assertEquals(MessageTemplates.DEFAULTS, data.toMessageTemplates());
    }
}
