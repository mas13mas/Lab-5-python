import unittest
from poller import ConfigError, validate_config


class TestConfigValidation(unittest.TestCase):
    def test_missing_targets_rejected(self):
        cfg = {
            "defaults": {
                "snmp_version": "v2c",
                "timeout_s": 2.5,
                "retries": 1,
                "target_budget_s": 10,
                "oids": ["sysUpTime.0"],
            }
            # targets key intentionally missing
        }

        with self.assertRaises(ConfigError):
            validate_config(cfg)


if __name__ == "__main__":
    unittest.main()
