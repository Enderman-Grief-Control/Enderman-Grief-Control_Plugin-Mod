package endermangriefcontrol.message;

import endermangriefcontrol.messaging.MessageTemplates;
import endermangriefcontrol.messaging.config.MessageTemplatesData;
import org.bukkit.configuration.ConfigurationSection;

/**
 * Tolerantly parses the "messages" section of config.yml, falling back to
 * {@link MessageTemplates#DEFAULTS} for anything missing or unrecognized rather than failing
 * config load over a typo - same convention as {@link endermangriefcontrol.heldblock.HeldBlockHandling#fromConfig}.
 */
public final class PaperMessageTemplateLoader {

    private PaperMessageTemplateLoader() {
    }

    public static MessageTemplates load(ConfigurationSection config) {
        ConfigurationSection section = config.getConfigurationSection("messages");
        if (section == null) {
            return MessageTemplates.DEFAULTS;
        }

        MessageTemplatesData data = MessageTemplatesData.defaults();
        data.prefix = section.getString("prefix", data.prefix);
        data.deniedPlacement = loadTemplate(section.getConfigurationSection("denied-placement"), data.deniedPlacement);
        data.deniedPickup = loadTemplate(section.getConfigurationSection("denied-pickup"), data.deniedPickup);
        data.heldBlockAlert = loadTemplate(section.getConfigurationSection("held-block-alert"), data.heldBlockAlert);
        data.heldBlockCleared = loadTemplate(section.getConfigurationSection("held-block-cleared"), data.heldBlockCleared);
        return data.toMessageTemplates();
    }

    private static MessageTemplatesData.MessageTemplateData loadTemplate(
            ConfigurationSection section, MessageTemplatesData.MessageTemplateData fallback) {
        if (section == null) {
            return fallback;
        }
        MessageTemplatesData.MessageTemplateData data = new MessageTemplatesData.MessageTemplateData();
        data.prefixColor = section.getString("prefix-color", fallback.prefixColor);
        data.body = section.getString("body", fallback.body);
        data.bodyColor = section.getString("body-color", fallback.bodyColor);
        data.coordsColor = section.getString("coords-color", fallback.coordsColor);
        return data;
    }
}
