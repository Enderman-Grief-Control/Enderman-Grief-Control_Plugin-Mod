import json
import tempfile
import unittest
from pathlib import Path

from egc_compat.client import extended, observer
from egc_compat.client.ui import UiBlocked, UiDriver, center, char_codes, gui_to_window, key_lparams, point_lparam
from egc_compat.results import Check

# A slice of the real 1.21 shapes: Mojang names -> obfuscated names (ProGuard), then obfuscated ->
# intermediary (tiny). children() and getX() are first declared on interfaces, so intermediary
# lists them only there; getString() keeps its library name.
PROGUARD = """# comment
net.minecraft.client.Minecraft -> fgo:
# {"fileName":"Minecraft.java","id":"sourceFile"}
    net.minecraft.client.gui.screens.Screen screen -> z
    12:12:net.minecraft.client.Minecraft getInstance() -> Q
    com.mojang.blaze3d.platform.Window getWindow() -> aO
com.mojang.blaze3d.platform.Window -> fhy:
    int getScreenWidth() -> l
    1:1:int getGuiScaledWidth():10:10 -> o
net.minecraft.client.gui.screens.Screen -> fod:
    91:91:java.util.List children() -> aK_
    net.minecraft.network.chat.Component getTitle() -> p
net.minecraft.client.gui.components.events.ContainerEventHandler -> fkh:
    java.util.List children() -> aK_
net.minecraft.client.gui.layouts.LayoutElement -> fmb:
    int getX() -> D
net.minecraft.client.gui.components.AbstractWidget -> fhr:
    boolean active -> j
    net.minecraft.network.chat.Component getMessage() -> A
net.minecraft.client.gui.components.AbstractSelectionList -> fih:
    474:474:int getRowTop(int) -> g
net.minecraft.network.chat.Component -> xp:
    java.lang.String getString() -> getString
"""
TINY_V1 = "\n".join([
    "v1\tofficial\tintermediary",
    "CLASS\tfgo\tnet/minecraft/class_310", "CLASS\tfhy\tnet/minecraft/class_1041", "CLASS\tfod\tnet/minecraft/class_437",
    "CLASS\tfkh\tnet/minecraft/class_4069", "CLASS\tfmb\tnet/minecraft/class_8021", "CLASS\tfhr\tnet/minecraft/class_339",
    "CLASS\tfih\tnet/minecraft/class_350", "CLASS\txp\tnet/minecraft/class_2561",
    "FIELD\tfgo\tLfod;\tz\tfield_1755",
    "METHOD\tfgo\t()Lfgo;\tQ\tmethod_1551",
    "METHOD\tfgo\t()Lfhy;\taO\tmethod_22683",
    "METHOD\tfhy\t()I\tl\tmethod_4480",
    "METHOD\tfhy\t()I\to\tmethod_4486",
    "METHOD\tfod\t()Lxp;\tp\tmethod_25440",
    "METHOD\tfkh\t()Ljava/util/List;\taK_\tmethod_25396",
    "METHOD\tfmb\t()I\tD\tmethod_46426",
    "FIELD\tfhr\tZ\tj\tfield_22763",
    "METHOD\tfhr\t()Lxp;\tA\tmethod_25369",
    "METHOD\tfih\t(I)I\tg\tmethod_25337",
])


class NameMapTest(unittest.TestCase):
    def test_tiny_v1_and_v2_parse_the_same(self):
        v2 = "\n".join(["tiny\t2\t0\tofficial\tintermediary", "c\tfgo\tnet/minecraft/class_310",
                        "\tm\t()Lfhy;\taO\tmethod_22683", "\tf\tLfod;\tz\tfield_1755"])
        parsed = observer.parse_tiny(v2)
        self.assertEqual(parsed["members"][("method", "fgo", "aO", "()Lfhy;")], "method_22683")
        self.assertEqual(parsed["members"][("field", "fgo", "z", "Lfod;")], "field_1755")
        with self.assertRaises(ValueError):
            observer.parse_tiny("tiny\t1\tofficial")

    def test_derivation_joins_mojang_obfuscated_and_intermediary_names(self):
        names, missing = observer.derive(PROGUARD, TINY_V1)
        self.assertEqual(names["class.Minecraft"], "net.minecraft.class_310")
        self.assertEqual(names["method.Minecraft.getInstance"], "method_1551")
        self.assertEqual(names["field.Minecraft.screen"], "field_1755")
        self.assertEqual(names["method.Window.getGuiScaledWidth"], "method_4486")  # trailing line numbers parsed
        self.assertEqual(names["method.Screen.children"], "method_25396")  # from ContainerEventHandler
        self.assertEqual(names["method.AbstractWidget.getX"], "method_46426")  # from LayoutElement
        self.assertEqual(names["method.AbstractSelectionList.getRowTop"], "method_25337")
        self.assertEqual(names["method.Component.getString"], "getString")  # kept library name
        self.assertIn("method.AbstractWidget.getY", missing)
        self.assertIn("class.PauseScreen", missing)

    def test_descriptors(self):
        class_map = {"net.minecraft.client.Minecraft": "fgo"}
        self.assertEqual(observer.descriptor_type("int[][]", class_map), "[[I")
        self.assertEqual(observer.descriptor_type("net.minecraft.client.Minecraft", class_map), "Lfgo;")
        self.assertEqual(observer.descriptor_type("java.util.List", class_map), "Ljava/util/List;")

    def test_properties_escape_paths(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "names.properties"
            digest = observer.write_properties(path, {"class.Minecraft": "net.minecraft.class_310"},
                                               Path("C:\\run\\observer.jsonl"))
            text = path.read_text(encoding="utf-8")
            self.assertIn("out=C\\:\\\\run\\\\observer.jsonl", text)
            self.assertEqual(len(digest), 64)

    def test_every_member_the_agent_requires_is_derived(self):
        derived = {f"{kind}.{owner}.{name}" for kind, owner, name, _params, _root in observer.MEMBERS}
        self.assertTrue(set(observer.REQUIRED) <= derived)
        source = observer.SOURCE.read_text(encoding="utf-8")
        for key in observer.REQUIRED:
            self.assertIn(f'"{key}"', source, key)  # the agent's own REQUIRED list matches


def snapshot_line(seq: int, data: dict) -> str:
    return json.dumps({"seq": seq, "t_ms": seq, "snapshot": data}) + "\n"


class SnapshotTest(unittest.TestCase):
    def test_incremental_reading_keeps_partial_lines_for_later(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "observer.jsonl"
            first = snapshot_line(1, {"status": "ok", "screen": None})
            second = snapshot_line(2, {"status": "ok", "screen": {"class": "x"}, "widgets": [
                {"message": "Prevent Enderman Grief: ON", "x": 0, "y": 0, "w": 10, "h": 10}]})
            path.write_text(first + second[:20], encoding="utf-8")
            snapshots = observer.Snapshots(path)
            self.assertEqual(snapshots.refresh().seq, 1)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(second[20:])
            current = snapshots.refresh()
            self.assertEqual((current.seq, current.screen_class), (2, "x"))
            self.assertEqual(extended.config_values(current), {"prevent": "ON"})
            self.assertIsNotNone(snapshots.wait_for(lambda s: s.screen_class == "x", 1, after_seq=1))
            self.assertIsNone(snapshots.wait_for(lambda s: s.screen_class == "x", 0.3, after_seq=2))

    def test_an_unavailable_observer_ends_every_wait(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "observer.jsonl"
            path.write_text(snapshot_line(1, {"status": "unavailable", "missing": ["field.Minecraft.screen"]}),
                            encoding="utf-8")
            self.assertIsNone(observer.Snapshots(path).wait_for(lambda s: True, 5))


GUI = {"scaled_w": 427, "scaled_h": 240, "window_w": 854, "window_h": 480}
PAUSE = {"status": "ok", "gui": GUI, "screen": {"class": "pause"},
         "widgets": [{"message": "Save and Quit to Title", "x": 111, "y": 164, "w": 204, "h": 20}]}


class RetryTest(unittest.TestCase):
    """A click is resent only when the observer stayed live (heartbeats) and the screen did not change."""

    def driver(self, root: str, step_s: float = 2) -> tuple[UiDriver, Path, list]:
        path = Path(root) / "observer.jsonl"
        path.write_text(snapshot_line(1, PAUSE), encoding="utf-8")
        driver = UiDriver(lambda: 1, observer.Snapshots(path), Path(root), Path(root) / "evidence",
                          lambda message: None, step_s=step_s, retry_s=0.3)
        return driver, path, []

    @staticmethod
    def append(path: Path, seq: int, data: dict) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(snapshot_line(seq, data))

    def press(self, driver: UiDriver):
        return driver.press_widget("Save and Quit", lambda s: s.widget(prefix="Save and Quit"),
                                   lambda s: s.screen_class == "title", "the title screen")

    def test_a_lost_click_on_a_live_unchanged_screen_is_sent_again(self):
        with tempfile.TemporaryDirectory() as root:
            driver, path, clicks = self.driver(root)

            def click(x, y, label):
                clicks.append((x, y))
                if len(clicks) == 1:
                    self.append(path, 2, PAUSE)  # a heartbeat: live, and nothing changed
                else:
                    self.append(path, 3, {"status": "ok", "gui": GUI, "screen": {"class": "title"}})

            driver.click = click
            self.assertEqual(self.press(driver).screen_class, "title")
            self.assertEqual(clicks, [(426, 348), (426, 348)])
            actions = [json.loads(line)["action"] for line in driver.actions.read_text(encoding="utf-8").splitlines()]
            self.assertIn("retry", actions)

    def test_a_silent_observer_never_causes_a_resend(self):
        with tempfile.TemporaryDirectory() as root:
            driver, path, clicks = self.driver(root, step_s=0.8)
            driver.click = lambda x, y, label: clicks.append(label)  # the game is busy: no new line at all
            with self.assertRaises(UiBlocked):
                self.press(driver)
            self.assertEqual(len(clicks), 1)

    def test_an_unconfirmed_step_blocks_after_every_attempt(self):
        with tempfile.TemporaryDirectory() as root:
            driver, path, clicks = self.driver(root, step_s=0.3)

            def click(x, y, label):
                clicks.append(label)
                self.append(path, len(clicks) + 1, PAUSE)

            driver.click = click
            with self.assertRaises(UiBlocked):
                self.press(driver)
            self.assertEqual(len(clicks), 3)


class InputEncodingTest(unittest.TestCase):
    def test_key_and_mouse_parameters(self):
        down, up = key_lparams(0x01, False)
        self.assertEqual(down, 0x00010001)
        self.assertEqual(up, 0xC0010001)
        self.assertEqual(key_lparams(0x50, True)[0], 0x01500001)
        self.assertEqual(point_lparam(426, 140), (140 << 16) | 426)

    def test_gui_coordinates_scale_to_window_pixels(self):
        gui = {"scaled_w": 427, "scaled_h": 240, "window_w": 854, "window_h": 480}
        self.assertEqual(gui_to_window(213, 70, gui), (426, 140))
        # The 10F config screen's Save button: x=214, y=204, 98 x 20 at GUI scale 2.
        self.assertEqual(center({"x": 214, "y": 204, "w": 98, "h": 20}, gui), (526, 428))

    def test_typed_text_is_utf16_and_printable(self):
        self.assertEqual(char_codes("egc-world-a"), [ord(ch) for ch in "egc-world-a"])
        with self.assertRaises(ValueError):
            char_codes("a\nb")


def report(world: int, session: int, start: int, now: int, carrying: int = 1) -> str:
    return (f"[12:00:00] [Render thread/INFO]: [CHAT] EGC-RESULT holder world={world} session={session} holders=1 "
            f"carrying={carrying} start={start} now={now}")


def alert(z: int, thread: str = "Server thread", x: int = 6) -> str:
    return f"[12:00:00] [{thread}/INFO]: [Enderman] holding a block at ({x}, 100, {z})."


class ExtendedVerdictTest(unittest.TestCase):
    def session(self, alert_after_tick: int) -> list[str]:
        lines = [report(1, 2, 1000, 1000), alert(6)]  # join discovery
        for now in range(1100, 3801, 100):
            if now - 1000 > alert_after_tick and not any("holding" in line for line in lines[2:]):
                lines.append(alert(6))
            lines.append(report(1, 2, 1000, now))
        return lines

    def test_the_first_periodic_alert_is_bracketed_by_game_time(self):
        periodic = extended.first_periodic_alert(self.session(2400), 1, 2)
        self.assertGreaterEqual(periodic["after_now"] - periodic["start"], extended.RESOLUTION_PERIOD_TICKS)
        early = extended.first_periodic_alert(self.session(900), 1, 2)  # a retained tick counter
        self.assertLess(early["after_now"] - early["start"], extended.RESOLUTION_PERIOD_TICKS)
        quiet = [report(1, 2, 1000, now) for now in range(1000, 3801, 100)]
        self.assertIsNone(extended.first_periodic_alert(quiet, 1, 2))

    def test_carrier_lines_record_thread_kind_and_position(self):
        lines = [alert(6, "Render thread"), "[12:00:01] [Server thread/INFO]: [Enderman] holding cleared at (6, 100, 6)."]
        found = extended.carrier_lines(lines)
        self.assertEqual([(c["thread"], c["kind"], c["pos"]) for c in found],
                         [("Render thread", "alert", (6, 100, 6)), ("Server thread", "clear", (6, 100, 6))])

    def test_report_indexes_point_into_the_whole_log(self):
        lines = [report(2, 1, 1, 4), alert(8), report(1, 2, 229, 232)]
        found = extended.holder_reports(lines, since=2)
        self.assertEqual([r.index for r in found], [2])  # not 0: a join window must not reach back

    def test_save_compares_only_the_edited_settings(self):
        written = {**extended.INITIAL_CONFIG, "enabled": True, "messages": {"prefix": "[Enderman] "}}
        self.assertEqual(extended.settings(written), {**extended.INITIAL_CONFIG, "enabled": True})
        self.assertIsNone(extended.settings(None))
        self.assertTrue(extended.OFFLINE_REALMS.search("Could not authorize you against Realms server: "
                                                       "java.lang.RuntimeException: Failed to parse into SignedJWT: 0"))

    def test_an_observed_contradiction_survives_a_later_block(self):
        failed, quiet, passed = Check("a").start(), Check("b").start(), Check("c").start()
        failed.check("Save: no holder cleared", False, [], ["holding cleared at (6, 100, 6)"])
        quiet.check("entered A", True, 1, 1)
        passed.check("done", True, 1, 1)
        passed.finish()
        extended.finish_after_block([failed, quiet, passed], "not confirmed: the title screen")
        self.assertEqual((failed.status, quiet.status, passed.status), ("fail", "blocked", "pass"))
        self.assertIn("Save: no holder cleared", failed.reason)
        self.assertEqual(quiet.failure_class, "harness_or_protocol")

    def test_javac_defaults_to_the_one_beside_java(self):
        self.assertEqual(extended.javac_for("java", None), "javac")
        self.assertEqual(extended.javac_for("java", "C:/jdk/bin/javac.exe"), "C:/jdk/bin/javac.exe")


if __name__ == "__main__":
    unittest.main()
