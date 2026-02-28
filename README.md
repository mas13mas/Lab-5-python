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
