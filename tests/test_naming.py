import re
import unittest

from ailine.naming.petname import default_record_name, validate_record_name


class NamingTests(unittest.TestCase):
    def test_default_name_shape(self):
        n = default_record_name()
        self.assertRegex(n, r"^[a-z0-9]+-[a-z0-9]+$")
        a, b = n.split("-", 1)
        self.assertGreaterEqual(len(a), 2)
        self.assertGreaterEqual(len(b), 2)

    def test_validate_accepts_reasonable_labels(self):
        self.assertEqual(validate_record_name("  my-baseline  "), "my-baseline")
        self.assertEqual(validate_record_name("Run #42 (v2)"), "Run #42 (v2)")

    def test_validate_rejects_empty(self):
        with self.assertRaises(ValueError):
            validate_record_name("")
        with self.assertRaises(ValueError):
            validate_record_name("   ")

    def test_validate_rejects_newlines(self):
        with self.assertRaises(ValueError):
            validate_record_name("bad\nname")

    def test_validate_rejects_too_long(self):
        with self.assertRaises(ValueError):
            validate_record_name("x" * 121)


if __name__ == "__main__":
    unittest.main()
