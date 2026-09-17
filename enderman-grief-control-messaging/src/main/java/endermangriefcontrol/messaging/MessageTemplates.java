package endermangriefcontrol.messaging;

import net.kyori.adventure.text.format.NamedTextColor;

/**
 * The full set of announcement templates. {@link #DEFAULTS} matches the wording/colors both
 * platforms hardcoded before this module existed.
 */
public record MessageTemplates(String prefixText, MessageTemplate denied, MessageTemplate heldBlockAlert,
                                MessageTemplate heldBlockCleared) {

    public static final MessageTemplates DEFAULTS = new MessageTemplates(
            "[Enderman] ",
            new MessageTemplate(NamedTextColor.LIGHT_PURPLE, "Denied {action} at ", NamedTextColor.GRAY, NamedTextColor.GREEN),
            new MessageTemplate(NamedTextColor.GOLD, "holding a block at ", NamedTextColor.GRAY, NamedTextColor.GREEN),
            new MessageTemplate(NamedTextColor.AQUA, "holding cleared at ", NamedTextColor.GRAY, NamedTextColor.GREEN)
    );
}
