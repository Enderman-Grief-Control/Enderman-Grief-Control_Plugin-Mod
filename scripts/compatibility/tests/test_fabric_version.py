import unittest

from egc_compat.fabric_version import compare, matches, parse_version


class VersionTest(unittest.TestCase):
    def test_ordering_follows_loader_semantic_versions(self):
        ordered = ["1.21.4-", "1.21.4-beta.1", "1.21.4-rc.3", "1.21.4", "1.21.5-", "1.21.5", "1.21.10", "26"]
        parsed = [parse_version(text) for text in ordered]
        for low, high in zip(parsed, parsed[1:]):
            self.assertLess(compare(low, high), 0, (low, high))
        self.assertEqual(compare(parse_version("1.21"), parse_version("1.21.0")), 0)
        self.assertEqual(compare(parse_version("0.128.2+1.21.5"), parse_version("0.128.2")), 0)

    def test_non_semantic_text_is_rejected(self):
        for text in ("", "latest", "1.21a", "1.x.2", "-beta"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_version(text, allow_wildcard=True)


class PredicateTest(unittest.TestCase):
    def test_fabric_api_ranges_accept_only_their_release(self):
        self.assertTrue(matches(">=1.21.4- <1.21.5-", "1.21.4"))
        self.assertFalse(matches(">=1.21.4- <1.21.5-", "1.21.5"))
        self.assertTrue(matches(">=1.21- <1.21.2-", "1.21.1"))
        self.assertFalse(matches(">=1.21- <1.21.2-", "1.21.2"))

    def test_mod_menu_style_predicates(self):
        self.assertTrue(matches(">=1.21-beta.2", "1.21"))
        self.assertTrue(matches("~1.21.6", "1.21.8"))
        self.assertFalse(matches("~1.21.6", "1.21.5"))
        self.assertFalse(matches("~1.21.6", "1.22"))
        self.assertTrue(matches(">=1.21.11 <26", "1.21.11"))
        self.assertTrue(matches(">=0.15.10", "0.19.5"))
        self.assertFalse(matches(">=0.20.0", "0.19.5"))

    def test_project_descriptor_range(self):
        self.assertTrue(matches("~1.21", "1.21"))
        self.assertTrue(matches("~1.21", "1.21.11"))
        self.assertFalse(matches("~1.21", "1.22"))

    def test_other_forms(self):
        self.assertTrue(matches("*", "1.21.3"))
        self.assertTrue(matches(None, "1.21.3"))
        self.assertTrue(matches("1.21.x", "1.21.9"))
        self.assertFalse(matches("1.21.x", "1.22.0"))
        self.assertTrue(matches("^1.2.0", "1.9.0"))
        self.assertFalse(matches("^1.2.0", "2.0.0"))
        self.assertTrue(matches(["1.20.6", ">=1.21.2"], "1.21.3"))
        self.assertFalse(matches(["1.20.6", ">=1.21.2"], "1.21.1"))
        self.assertTrue(matches("=1.21.1", "1.21.1"))

    def test_unsupported_predicates_are_errors_not_matches(self):
        for predicate in ({"minecraft": "1.21"}, [], [1], ">=latest"):
            with self.subTest(predicate=predicate), self.assertRaises(ValueError):
                matches(predicate, "1.21")


if __name__ == "__main__":
    unittest.main()
