import unittest

from ailine.web.code_browser import (
    build_path_tree,
    detect_language,
    safe_relpath,
    truncate_text,
)


class CodeBrowserHelpersTests(unittest.TestCase):
    def test_safe_relpath_accepts_known_path(self):
        self.assertEqual(safe_relpath("a/b.py", {"a/b.py", "c.py"}), "a/b.py")

    def test_safe_relpath_rejects_traversal(self):
        for raw in ["../etc/passwd", "a/../../b", "/abs/path", "..", "."]:
            self.assertIsNone(safe_relpath(raw, {"a/b"}), raw)

    def test_safe_relpath_normalizes_redundant_segments(self):
        # `a/./b` normalizes to `a/b` and is allowed if present in the allow-list.
        self.assertEqual(safe_relpath("a/./b", {"a/b"}), "a/b")

    def test_safe_relpath_rejects_unknown(self):
        self.assertIsNone(safe_relpath("missing.py", {"a/b.py"}))

    def test_safe_relpath_handles_empty(self):
        self.assertIsNone(safe_relpath(None, {"a"}))
        self.assertIsNone(safe_relpath("", {"a"}))

    def test_detect_language(self):
        self.assertEqual(detect_language("train.py"), "python")
        self.assertEqual(detect_language("Dockerfile"), "dockerfile")
        self.assertEqual(detect_language("config.yml"), "yaml")
        self.assertEqual(detect_language("notes.unknown"), "")

    def test_truncate_text_no_op(self):
        text, truncated = truncate_text("hello", max_bytes=10)
        self.assertEqual(text, "hello")
        self.assertFalse(truncated)

    def test_truncate_text_caps(self):
        text, truncated = truncate_text("a" * 100, max_bytes=10)
        self.assertEqual(len(text), 10)
        self.assertTrue(truncated)

    def test_build_path_tree_groups_dirs_then_files(self):
        tree = build_path_tree(["a/b.py", "a/c.py", "z.py"])
        self.assertEqual([n["name"] for n in tree], ["a", "z.py"])
        a_node = tree[0]
        self.assertEqual(a_node["type"], "dir")
        self.assertEqual([c["name"] for c in a_node["children"]], ["b.py", "c.py"])


if __name__ == "__main__":
    unittest.main()
