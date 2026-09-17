package endermangriefcontrol.messaging.config;

import com.google.gson.annotations.SerializedName;
import endermangriefcontrol.messaging.MessageTemplate;
import endermangriefcontrol.messaging.MessageTemplates;
import net.kyori.adventure.text.format.NamedTextColor;

import java.util.Locale;

/**
 * Gson-friendly mirror of {@link MessageTemplates}, using plain color-name strings (e.g.
 * {@code "light_purple"}) instead of {@link NamedTextColor} so it binds naturally to JSON/YAML
 * config. Unrecognized or missing values tolerantly fall back to {@link MessageTemplates#DEFAULTS}
 * rather than failing config load.
 */
public final class MessageTemplatesData {

    public String prefix = MessageTemplates.DEFAULTS.prefixText();

    @SerializedName("denied")
    public MessageTemplateData denied = MessageTemplateData.from(MessageTemplates.DEFAULTS.denied());

    @SerializedName("held-block-alert")
    public MessageTemplateData heldBlockAlert = MessageTemplateData.from(MessageTemplates.DEFAULTS.heldBlockAlert());

    @SerializedName("held-block-cleared")
    public MessageTemplateData heldBlockCleared = MessageTemplateData.from(MessageTemplates.DEFAULTS.heldBlockCleared());

    public static MessageTemplatesData defaults() {
        return new MessageTemplatesData();
    }

    public MessageTemplates toMessageTemplates() {
        String resolvedPrefix = prefix != null ? prefix : MessageTemplates.DEFAULTS.prefixText();
        return new MessageTemplates(
                resolvedPrefix,
                toTemplateOrDefault(denied, MessageTemplates.DEFAULTS.denied()),
                toTemplateOrDefault(heldBlockAlert, MessageTemplates.DEFAULTS.heldBlockAlert()),
                toTemplateOrDefault(heldBlockCleared, MessageTemplates.DEFAULTS.heldBlockCleared())
        );
    }

    private static MessageTemplate toTemplateOrDefault(MessageTemplateData data, MessageTemplate fallback) {
        return data != null ? data.toMessageTemplate(fallback) : fallback;
    }

    public static final class MessageTemplateData {
        public String prefixColor;
        public String body;
        public String bodyColor;
        public String coordsColor;

        public static MessageTemplateData from(MessageTemplate template) {
            MessageTemplateData data = new MessageTemplateData();
            data.prefixColor = colorName(template.prefixColor());
            data.body = template.body();
            data.bodyColor = colorName(template.bodyColor());
            data.coordsColor = colorName(template.coordsColor());
            return data;
        }

        public MessageTemplate toMessageTemplate(MessageTemplate fallback) {
            return new MessageTemplate(
                    resolveColor(prefixColor, fallback.prefixColor()),
                    body != null ? body : fallback.body(),
                    resolveColor(bodyColor, fallback.bodyColor()),
                    resolveColor(coordsColor, fallback.coordsColor())
            );
        }

        private static String colorName(NamedTextColor color) {
            return NamedTextColor.NAMES.key(color);
        }

        private static NamedTextColor resolveColor(String name, NamedTextColor fallback) {
            if (name == null) {
                return fallback;
            }
            NamedTextColor resolved = NamedTextColor.NAMES.value(name.toLowerCase(Locale.ROOT));
            return resolved != null ? resolved : fallback;
        }
    }
}
