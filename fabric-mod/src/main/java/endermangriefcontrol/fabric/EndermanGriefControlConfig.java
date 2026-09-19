package endermangriefcontrol.fabric;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
import com.google.gson.JsonParseException;
import endermangriefcontrol.messaging.config.MessageTemplatesData;
import net.fabricmc.loader.api.FabricLoader;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

public final class EndermanGriefControlConfig {

    private static final Gson GSON = new GsonBuilder().setPrettyPrinting().create();
    private static final Path CONFIG_PATH =
            FabricLoader.getInstance().getConfigDir().resolve("no-enderman-grief.json");

    public boolean enabled = true;
    public boolean loggingEnabled = false;
    public boolean logRemovals = true;

    // Minimum seconds between denial chat announcements, per dimension and denial type (placement
    // and pickup throttled independently). Denials faster than this are counted and rolled into
    // the next chat message rather than dropped. Doesn't affect the console/server-log line,
    // which always logs every individual denial. 0 announces every single denial in chat (the old
    // behavior).
    public int denialRateLimitSeconds = 10;

    // How a stuck holder (an enderman already carrying a block placement can no longer clear) is
    // handled: "auto-clear" (default, resolves it automatically), "alert" (periodically re-logs
    // its location for manual hunting instead), or "off". See HeldBlockHandling.
    public String heldBlockHandling = "auto-clear";

    // Chat announcement wording/colors - see MessageTemplatesData. Defaults match the wording
    // used before this became configurable.
    public MessageTemplatesData messages = MessageTemplatesData.defaults();

    public static EndermanGriefControlConfig load() {
        if (Files.exists(CONFIG_PATH)) {
            try (var reader = Files.newBufferedReader(CONFIG_PATH)) {
                EndermanGriefControlConfig loaded = GSON.fromJson(reader, EndermanGriefControlConfig.class);
                if (loaded != null) {
                    if (loaded.messages == null) {
                        loaded.messages = MessageTemplatesData.defaults();
                    }
                    return loaded;
                }
            } catch (IOException e) {
                EndermanGriefControlMod.LOGGER.warn("Failed to read {}, using defaults.", CONFIG_PATH, e);
            } catch (JsonParseException e) {
                // Don't overwrite a hand-edited file that just has a typo; fall back in memory only.
                EndermanGriefControlMod.LOGGER.warn("Failed to parse {}, using defaults (file left untouched).", CONFIG_PATH, e);
                return new EndermanGriefControlConfig();
            }
        }

        EndermanGriefControlConfig defaults = new EndermanGriefControlConfig();
        defaults.save();
        return defaults;
    }

    public void save() {
        try (var writer = Files.newBufferedWriter(CONFIG_PATH)) {
            GSON.toJson(this, writer);
        } catch (IOException e) {
            EndermanGriefControlMod.LOGGER.warn("Failed to write {}.", CONFIG_PATH, e);
        }
    }
}
