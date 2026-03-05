import unittest
from poller import ConfigError, validate_config


class TestConfigValidation(unittest.TestCase):
    # This test checks that config validation fails when "targets" is missing
    def test_missing_targets_rejected(self):
        cfg = {
            "defaults": {
                "snmp_version": "v2c",
                "timeout_s": 2.5,
                "retries": 1,
                "target_budget_s": 10,
                "oids": ["sysUpTime.0"],
            }
            # "targets" is intentionally missing here to trigger a ConfigError
        }

        # validate_config should raise ConfigError for invalid config
        with self.assertRaises(ConfigError):
            validate_config(cfg)


# Allow running this test file directly (optional)
if __name__ == "__main__":
    unittest.main()
