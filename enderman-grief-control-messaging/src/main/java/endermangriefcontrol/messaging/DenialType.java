package endermangriefcontrol.messaging;

/**
 * Which kind of enderman block change was denied. Carries its own console/log wording so that
 * text stays identical regardless of which {@link MessageTemplate} colors the chat announcement.
 */
public enum DenialType {
    PLACEMENT("placement"),
    PICKUP("pickup");

    private final String actionText;

    DenialType(String actionText) {
        this.actionText = actionText;
    }

    public String actionText() {
        return actionText;
    }
}
