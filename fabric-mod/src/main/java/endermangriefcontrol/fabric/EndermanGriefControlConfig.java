package endermangriefcontrol.fabric;

import com.google.gson.Gson;
import com.google.gson.GsonBuilder;
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

    public static EndermanGriefControlConfig load() {
        if (Files.exists(CONFIG_PATH)) {
            try (var reader = Files.newBufferedReader(CONFIG_PATH)) {
                EndermanGriefControlConfig loaded = GSON.fromJson(reader, EndermanGriefControlConfig.class);
                if (loaded != null) {
                    return loaded;
                }
            } catch (IOException e) {
                EndermanGriefControlMod.LOGGER.warn("Failed to read {}, using defaults.", CONFIG_PATH, e);
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
