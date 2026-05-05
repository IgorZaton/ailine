"""Tests for the AIline MLflow ``run_context_provider`` plugin."""

import os
import unittest

from ailine.integrations.mlflow_plugin import (
    CORRELATION_ENV,
    CORRELATION_TAG,
    AilineRunContextProvider,
)


class AilineRunContextProviderTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.pop(CORRELATION_ENV, None)
        self.addCleanup(self._restore)

    def _restore(self):
        os.environ.pop(CORRELATION_ENV, None)
        if self._saved is not None:
            os.environ[CORRELATION_ENV] = self._saved

    def test_no_env_means_not_in_context_and_no_tags(self):
        provider = AilineRunContextProvider()
        self.assertFalse(provider.in_context())
        self.assertEqual(provider.tags(), {})

    def test_env_set_yields_tag_with_correlation_id(self):
        os.environ[CORRELATION_ENV] = "abc-123"
        provider = AilineRunContextProvider()
        self.assertTrue(provider.in_context())
        self.assertEqual(provider.tags(), {CORRELATION_TAG: "abc-123"})

    def test_empty_env_value_treated_as_unset(self):
        os.environ[CORRELATION_ENV] = ""
        provider = AilineRunContextProvider()
        self.assertFalse(provider.in_context())
        self.assertEqual(provider.tags(), {})


if __name__ == "__main__":
    unittest.main()
