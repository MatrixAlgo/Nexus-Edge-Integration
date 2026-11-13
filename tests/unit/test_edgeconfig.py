import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import testpaths  # noqa: F401,E402
from common.edgeconfig import DEFAULT_CONFIG, merged_with_defaults, validate_config  # noqa: E402


class ConfigValidation(unittest.TestCase):
    def test_default_is_valid(self):
        self.assertEqual(validate_config(DEFAULT_CONFIG), [])

    def test_rejects_bad_values(self):
        self.assertTrue(validate_config({"ackTimeoutSec": 1}))
        self.assertTrue(validate_config({"priorityRules": {"PR": "URGENT"}}))
        self.assertTrue(validate_config({"rm -rf /": True}))
        self.assertTrue(validate_config({"retryBaseSec": 50, "retryMaxSec": 10}))
        self.assertTrue(validate_config({"syncEnabled": "yes"}))

    def test_partial_config_merges_with_defaults(self):
        m = merged_with_defaults({"priorityRules": {"PR": "CRITICAL"}})
        self.assertEqual(m["priorityRules"]["PR"], "CRITICAL")
        self.assertEqual(m["priorityRules"]["GRN"], "NORMAL")
        self.assertEqual(m["ackTimeoutSec"], DEFAULT_CONFIG["ackTimeoutSec"])


if __name__ == "__main__":
    unittest.main()
