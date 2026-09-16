package endermangriefcontrol.fabric.heldblock;

import java.util.Locale;

/**
 * How a stuck holder (an enderman already carrying a block placement can no longer clear) is
 * handled once found, per the mod's single global config (no per-world concept, matching the rest
 * of this mod's config shape). These are mutually exclusive - alerting ("go hunt it yourself") and
 * auto-clearing ("resolved for you") are contradictory intents for the same enderman, not
 * independent settings to stack. Mirrors the Paper plugin's enum of the same name and purpose;
 * duplicated rather than shared since Paper and Fabric are independent projects with no shared
 * module.
 */
public enum HeldBlockHandling {
    /** Leave it alone - don't log, don't clear. */
    OFF,
    /** Periodically re-announce its location so it can be manually hunted down and killed. */
    ALERT,
    /** Periodically remove the carried block from it outright, no drop. */
    AUTO_CLEAR;

    /**
     * Parses a config string ("off" / "alert" / "auto-clear"), falling back on anything missing
     * or unrecognized rather than failing config load over a typo.
     */
    public static HeldBlockHandling fromConfig(String value, HeldBlockHandling fallback) {
        if (value == null) {
            return fallback;
        }
        try {
            return HeldBlockHandling.valueOf(value.toUpperCase(Locale.ROOT).replace('-', '_'));
        } catch (IllegalArgumentException e) {
            return fallback;
        }
    }

    /**
     * The config/command string form, e.g. {@code AUTO_CLEAR} -> {@code "auto-clear"}.
     */
    public String toConfigValue() {
        return name().toLowerCase(Locale.ROOT).replace('_', '-');
    }
}
