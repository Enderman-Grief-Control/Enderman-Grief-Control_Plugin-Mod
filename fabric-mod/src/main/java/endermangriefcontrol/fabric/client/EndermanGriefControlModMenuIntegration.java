package endermangriefcontrol.fabric.client;

import com.terraformersmc.modmenu.api.ConfigScreenFactory;
import com.terraformersmc.modmenu.api.ModMenuApi;

/**
 * Only ever invoked by Mod Menu itself, which is client-only — never loaded on a dedicated
 * server, and never invoked at all if the player doesn't have Mod Menu installed (see the
 * modCompileOnly dependency comment in build.gradle).
 */
public final class EndermanGriefControlModMenuIntegration implements ModMenuApi {

    @Override
    public ConfigScreenFactory<?> getModConfigScreenFactory() {
        return EndermanGriefControlConfigScreen::new;
    }
}
