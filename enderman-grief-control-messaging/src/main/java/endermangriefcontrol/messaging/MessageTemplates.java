package endermangriefcontrol.messaging;

import net.kyori.adventure.text.format.NamedTextColor;

/**
 * The full set of announcement templates. {@link #DEFAULTS} matches the wording/colors both
 * platforms hardcoded before this module existed, plus the placement/pickup denial split and the
 * count-based coloring added afterward.
 */
public record MessageTemplates(String prefixText, MessageTemplate deniedPlacement, MessageTemplate deniedPickup,
                                MessageTemplate heldBlockAlert, MessageTemplate heldBlockCleared) {

    public static final MessageTemplates DEFAULTS = new MessageTemplates(
            "[Enderman] ",
            new MessageTemplate(NamedTextColor.DARK_PURPLE, "Denied placement", NamedTextColor.GRAY, NamedTextColor.DARK_GREEN),
            new MessageTemplate(NamedTextColor.LIGHT_PURPLE, "Denied pickup", NamedTextColor.GRAY, NamedTextColor.DARK_GREEN),
            new MessageTemplate(NamedTextColor.GOLD, "holding a block at ", NamedTextColor.GRAY, NamedTextColor.GREEN),
            new MessageTemplate(NamedTextColor.AQUA, "holding block cleared", NamedTextColor.GRAY, NamedTextColor.DARK_GREEN)
    );
}
