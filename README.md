# Lab W8: Ops-grade Python SNMP Poller

## Files
- `poller.py` — main SNMP poller
- `config.yml` — YAML configuration for defaults and targets
- `test_config.py` — unit test for config validation
- `README.md` — run instructions, example logs, example JSON, and exit codes

## Requirements
- Python 3
- Net-SNMP tools (`snmpget`)
- PyYAML

## Setup (Linux)
```bash
sudo apt-get update
sudo apt-get install -y snmp python3 python3-venv
mkdir -p ~/w8_snmp_poller && cd ~/w8_snmp_poller
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install pyyaml
```

---

## Usage

Run and write JSON to a file:
```bash
python3 poller.py --config config.yml --out out.json --log-level INFO
python3 poller.py --config config.yml --out out.json --log-level WARNING
```



---

Logging levels
The program uses Python logging:

- `INFO`: normal run messages (run start/end, target start/end) + warnings + errors  
- `WARNING`: only warnings + errors  
- `ERROR`: only errors  
- `DEBUG`: most verbose (shows debug messages if present)

```bash
python3 poller.py --config config.yml --out - --log-level INFO
```



Key functions: inputs and outputs 

###` load_config(path)`
- **Input:** `path` (string) — path to the YAML file,  `config.yml`
- **Output:** `cfg` (dict) — the YAML parsed into a Python dictionary  
- **If something is wrong:** raises `ConfigError` (file not found / YAML parse error / wrong format)

### `validate_config(cfg)`
- **Input:** `cfg` (dict) — the config dictionary from `load_config()`
- **Output:** nothing (returns `None`)
- **If something is wrong:** raises `ConfigError` (missing keys, wrong types, etc.)


### `poll_target(target, defaults, log)`
-  **Input:**
  - `target` (dict) — one target entry from config (`name`, `ip`, optional overrides)
  - `defaults` (dict) — defaults section from config
  - `log` (Logger) — logger for INFO/WARNING/ERROR messages
- **Output:** dict (one target result block) containing:
-  `name`, `ip`
-   `status` (`ok|partial|failed`)
-   `runtime_s`
- `ok_count`, `fail_count`
-  `oids` (list of per-OID results)

###  `main()`
-  **Input:** CLI arguments:
  - --config` path to YAML
  - --out` output file path or `-` for stdout
  - --log-level` logging level
- **Output:** exit code:
- `0` all OK, `1` partial success, `2` total failure/invalid config
- **Also outputs:** JSON to stdout or to a file
