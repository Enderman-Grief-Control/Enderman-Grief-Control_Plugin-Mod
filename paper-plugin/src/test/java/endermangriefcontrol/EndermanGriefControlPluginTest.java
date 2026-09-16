package endermangriefcontrol;

import endermangriefcontrol.heldblock.HeldBlockHandling;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockbukkit.mockbukkit.MockBukkit;
import org.mockbukkit.mockbukkit.ServerMock;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class EndermanGriefControlPluginTest {

    private ServerMock server;
    private EndermanGriefControlPlugin plugin;

    @BeforeEach
    void setUp() {
        server = MockBukkit.mock();
        plugin = MockBukkit.load(EndermanGriefControlPlugin.class);
    }

    @AfterEach
    void tearDown() {
        MockBukkit.unmock();
    }

    @Test
    void isWorldEnabled_defaultTrue_noOverride_returnsTrue() {
        assertTrue(plugin.isWorldEnabled("world"));
    }

    @Test
    void isWorldEnabled_defaultFalse_noOverride_returnsFalse() {
        plugin.getConfig().set("default-enabled", false);

        assertFalse(plugin.isWorldEnabled("world"));
    }

    @Test
    void isWorldEnabled_worldsOverrideTrue_overridesDefaultFalse() {
        plugin.getConfig().set("default-enabled", false);
        plugin.getConfig().set("worlds.world_the_end", true);

        assertTrue(plugin.isWorldEnabled("world_the_end"));
    }

    @Test
    void isWorldEnabled_worldsOverrideFalse_overridesDefaultTrue() {
        plugin.getConfig().set("worlds.world_nether", false);

        assertFalse(plugin.isWorldEnabled("world_nether"));
        assertTrue(plugin.isWorldEnabled("world"));
    }

    @Test
    void isLoggingEnabled_reflectsConfigDefaultFalse() {
        assertFalse(plugin.isLoggingEnabled());
    }

    @Test
    void getHeldBlockHandling_noConfigValue_defaultsToAutoClear() {
        assertEquals(HeldBlockHandling.AUTO_CLEAR, plugin.getHeldBlockHandling("world"));
    }

    @Test
    void getHeldBlockHandling_defaultOverride_appliesToAllUnlistedWorlds() {
        plugin.getConfig().set("default-held-block-handling", "off");

        assertEquals(HeldBlockHandling.OFF, plugin.getHeldBlockHandling("world"));
    }

    @Test
    void getHeldBlockHandling_worldOverride_overridesDefault() {
        plugin.getConfig().set("held-block-worlds.world_the_end", "alert");

        assertEquals(HeldBlockHandling.ALERT, plugin.getHeldBlockHandling("world_the_end"));
        assertEquals(HeldBlockHandling.AUTO_CLEAR, plugin.getHeldBlockHandling("world"));
    }

    @Test
    void getHeldBlockHandling_unrecognizedValue_fallsBackToDefault() {
        plugin.getConfig().set("held-block-worlds.world_nether", "bogus");

        assertEquals(HeldBlockHandling.AUTO_CLEAR, plugin.getHeldBlockHandling("world_nether"));
    }
}
