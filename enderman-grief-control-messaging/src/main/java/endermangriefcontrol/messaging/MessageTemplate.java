package endermangriefcontrol.messaging;

import net.kyori.adventure.text.format.NamedTextColor;

/**
 * The wording/coloring for one announcement type. {@code body} is used verbatim; the coordinates
 * segment is always appended separately, colored by {@code coordsColor}.
 */
public record MessageTemplate(
    NamedTextColor prefixColor,
    String body,
    NamedTextColor bodyColor,
    NamedTextColor coordsColor
) {}
