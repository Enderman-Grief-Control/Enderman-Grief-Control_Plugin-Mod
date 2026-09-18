package endermangriefcontrol.message;

import endermangriefcontrol.messaging.MessageTemplate;
import endermangriefcontrol.messaging.MessageTemplates;
import net.kyori.adventure.text.format.NamedTextColor;
import org.bukkit.configuration.ConfigurationSection;

import java.util.Locale;

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

        return new MessageTemplates(
                section.getString("prefix", MessageTemplates.DEFAULTS.prefixText()),
                loadTemplate(section.getConfigurationSection("denied-placement"), MessageTemplates.DEFAULTS.deniedPlacement()),
                loadTemplate(section.getConfigurationSection("denied-pickup"), MessageTemplates.DEFAULTS.deniedPickup()),
                loadTemplate(section.getConfigurationSection("held-block-alert"), MessageTemplates.DEFAULTS.heldBlockAlert()),
                loadTemplate(section.getConfigurationSection("held-block-cleared"), MessageTemplates.DEFAULTS.heldBlockCleared())
        );
    }

    private static MessageTemplate loadTemplate(ConfigurationSection section, MessageTemplate fallback) {
        if (section == null) {
            return fallback;
        }
        return new MessageTemplate(
                color(section.getString("prefix-color"), fallback.prefixColor()),
                section.getString("body", fallback.body()),
                color(section.getString("body-color"), fallback.bodyColor()),
                color(section.getString("coords-color"), fallback.coordsColor())
        );
    }

    private static NamedTextColor color(String name, NamedTextColor fallback) {
        if (name == null) {
            return fallback;
        }
        NamedTextColor resolved = NamedTextColor.NAMES.value(name.toLowerCase(Locale.ROOT));
        return resolved != null ? resolved : fallback;
    }
}
