package endermangriefcontrol;

import endermangriefcontrol.heldblock.HeldBlockHandling;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockbukkit.mockbukkit.MockBukkit;
import org.mockbukkit.mockbukkit.ServerMock;
import org.mockbukkit.mockbukkit.entity.PlayerMock;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class EndermanGriefControlCommandTest {

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

    private PlayerMock authorizedPlayer() {
        PlayerMock player = server.addPlayer();
        player.addAttachment(plugin, "endermangriefcontrol.admin", true);
        return player;
    }

    @Test
    void reload_withPermission_sendsConfirmation() {
        PlayerMock player = authorizedPlayer();

        server.dispatchCommand(player, "enderman reload");

        player.assertSaid("EndermanGriefControl configuration reloaded.");
    }

    @Test
    void command_withoutPermission_sendsDenialMessage() {
        PlayerMock player = server.addPlayer();

        server.dispatchCommand(player, "enderman reload");

        player.assertSaid("You do not have permission to use this command.");
    }

    @Test
    void noArgs_sendsUsage() {
        PlayerMock player = authorizedPlayer();

        server.dispatchCommand(player, "enderman");

        player.assertSaid("Usage: /enderman <reload|status|toggle|held-block|set>");
    }

    @Test
    void status_withNoOverrides_reportsDefaultsAndLogging() {
        PlayerMock player = authorizedPlayer();

        server.dispatchCommand(player, "enderman status");

        player.assertSaid("Default: enabled, log denials: disabled, log removals: enabled, held-block: auto-clear");
    }

    @Test
    void status_forSpecificWorld_reportsEffectiveState() {
        PlayerMock player = authorizedPlayer();
        plugin.getConfig().set("worlds.world_nether", false);

        server.dispatchCommand(player, "enderman status world_nether");

        player.assertSaid("World 'world_nether': disabled, held-block: auto-clear");
    }

    @Test
    void toggle_withExplicitValue_setsWorldOverrideAndPersists() {
        PlayerMock player = authorizedPlayer();

        server.dispatchCommand(player, "enderman toggle world_nether false");

        player.assertSaid("World 'world_nether' is now disabled.");
        assertFalse(plugin.isWorldEnabled("world_nether"));
    }

    @Test
    void toggle_withoutValue_flipsCurrentEffectiveState() {
        PlayerMock player = authorizedPlayer();

        server.dispatchCommand(player, "enderman toggle world_nether");

        player.assertSaid("World 'world_nether' is now disabled.");
        assertFalse(plugin.isWorldEnabled("world_nether"));
    }

    @Test
    void setLogDenials_updatesConfigAndPersists() {
        PlayerMock player = authorizedPlayer();

        server.dispatchCommand(player, "enderman set log-denials true");

        player.assertSaid("Log denials is now enabled.");
        assertTrue(plugin.isLoggingEnabled());
    }

    @Test
    void setLogRemovals_updatesConfigAndPersists() {
        PlayerMock player = authorizedPlayer();

        server.dispatchCommand(player, "enderman set log-removals false");

        player.assertSaid("Log removals is now disabled.");
        assertFalse(plugin.isRemovalsLoggingEnabled());
    }

    @Test
    void setDefault_updatesConfigAndPersists() {
        PlayerMock player = authorizedPlayer();

        server.dispatchCommand(player, "enderman set default false");

        player.assertSaid("Default is now disabled.");
        assertFalse(plugin.isWorldEnabled("world"));
    }

    @Test
    void heldBlock_setsWorldOverrideAndPersists() {
        PlayerMock player = authorizedPlayer();

        server.dispatchCommand(player, "enderman held-block world_nether alert");

        player.assertSaid("Held-block handling for world 'world_nether' is now alert.");
        assertEquals(HeldBlockHandling.ALERT, plugin.getHeldBlockHandling("world_nether"));
        assertEquals(HeldBlockHandling.AUTO_CLEAR, plugin.getHeldBlockHandling("world"));
    }

    @Test
    void heldBlock_unrecognizedMode_sendsUsage() {
        PlayerMock player = authorizedPlayer();

        server.dispatchCommand(player, "enderman held-block world_nether bogus");

        player.assertSaid("Usage: /enderman held-block <world> <auto-clear|alert|off>");
    }

    @Test
    void setHeldBlockDefault_updatesConfigAndPersists() {
        PlayerMock player = authorizedPlayer();

        server.dispatchCommand(player, "enderman set held-block-default off");

        player.assertSaid("Default held-block handling is now off.");
        assertEquals(HeldBlockHandling.OFF, plugin.getHeldBlockHandling("world"));
    }

    @Test
    void setHeldBlockDefault_unrecognizedMode_sendsUsage() {
        PlayerMock player = authorizedPlayer();

        server.dispatchCommand(player, "enderman set held-block-default bogus");

        player.assertSaid("Usage: /enderman set held-block-default <auto-clear|alert|off>");
    }

    @Test
    void unknownSubcommand_sendsUsage() {
        PlayerMock player = authorizedPlayer();

        server.dispatchCommand(player, "enderman bogus");

        player.assertSaid("Unknown subcommand. Usage: /enderman <reload|status|toggle|held-block|set>");
    }
}
