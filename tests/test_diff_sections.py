import unittest

from ailine.web.diff_sections import split_unified_diff


class DiffSectionsTests(unittest.TestCase):
    def test_splits_multiple_diff_git_blocks(self):
        patch = """diff --git a/foo.py b/foo.py
--- a/foo.py
+++ b/foo.py
@@ -1 +1 @@
-old
+new
diff --git a/bar.py b/bar.py
--- a/bar.py
+++ b/bar.py
@@ -1 +1 @@
-a
+b
"""
        sections = split_unified_diff(patch)
        self.assertEqual(len(sections), 2)
        self.assertEqual(sections[0].title, "foo.py")
        self.assertIn("old", sections[0].body)
        self.assertEqual(sections[1].title, "bar.py")

    def test_single_block_without_second_git_header(self):
        patch = """diff --git a/only.py b/only.py
--- a/only.py
+++ b/only.py
@@ -1 +1 @@
-x
+y
"""
        sections = split_unified_diff(patch)
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].title, "only.py")

    def test_legacy_patch_without_diff_git(self):
        patch = """--- a/legacy.txt
+++ b/legacy.txt
@@ -1 +1 @@
-old
+new
"""
        sections = split_unified_diff(patch)
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].title, "legacy.txt")

    def test_empty_returns_empty(self):
        self.assertEqual(split_unified_diff(""), [])
        self.assertEqual(split_unified_diff("   "), [])


if __name__ == "__main__":
    unittest.main()
