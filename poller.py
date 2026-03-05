#!/usr/bin/env python3
# SNMP Poller:
# - Reads targets/OIDs from YAML
# - Polls via Net-SNMP snmpget
# - JSON output + logging + exit codes
# - Retries only on timeouts + per-target time budget

import argparse, json, logging, subprocess, sys, time, yaml
from datetime import datetime, timezone
from pathlib import Path


# Custom exception type for config-related problems
class ConfigError(Exception):
    pass


def setup_logging(level):
    # Configure global logging format and level
    # level is a string like: DEBUG / INFO / WARNING / ERROR
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # Return a named logger used throughout the program
    return logging.getLogger("snmp_poller")


def load_config(path):
    # Load YAML config file and return it as a Python dict (cfg)
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
    except FileNotFoundError:
        # Config path does not exist
        raise ConfigError(f"Config file not found: {path}")
    except yaml.YAMLError as e:
        # YAML syntax is invalid
        raise ConfigError(f"YAML parse error: {e}")

    # Root of YAML must be a mapping/dict
    if not isinstance(cfg, dict):
        raise ConfigError("Config root must be a mapping/object")

    return cfg


def validate_config(cfg):
    # Validate that the config has required keys and correct types
    if "defaults" not in cfg or "targets" not in cfg:
        raise ConfigError("Config must contain 'defaults' and 'targets'")

    d, ts = cfg["defaults"], cfg["targets"]

    # Required keys in defaults
    req = ["snmp_version", "timeout_s", "retries", "target_budget_s", "oids"]
    for k in req:
        if k not in d:
            raise ConfigError(f"'defaults' missing: {k}")

    # Basic validation rules
    if d["snmp_version"] != "v2c":
        raise ConfigError("defaults.snmp_version must be 'v2c'")
    if not isinstance(d["timeout_s"], (int, float)):
        raise ConfigError("defaults.timeout_s must be numeric")
    if not isinstance(d["retries"], int):
        raise ConfigError("defaults.retries must be an integer")
    if not isinstance(d["target_budget_s"], (int, float)):
        raise ConfigError("defaults.target_budget_s must be numeric")
    if not isinstance(d["oids"], list) or not d["oids"]:
        raise ConfigError("'defaults.oids' must be a non-empty list")

    # Targets must be a non-empty list
    if not isinstance(ts, list) or len(ts) < 1:
        raise ConfigError("'targets' must be a non-empty list")

    # Validate each target
    for i, t in enumerate(ts):
        if not isinstance(t, dict):
            raise ConfigError(f"targets[{i}] must be an object")
        if "name" not in t or "ip" not in t:
            raise ConfigError(f"targets[{i}] must contain 'name' and 'ip'")

        # Community string must exist either in target or in defaults
        if "community" not in t and "community" not in d:
            raise ConfigError(f"targets[{i}] missing community")

        # If a target defines extra oids, it must be a list
        if "oids" in t and not isinstance(t["oids"], list):
            raise ConfigError(f"targets[{i}].oids must be a list")


def snmpget_v2c(ip, community, oid, timeout_s):
    # Run one SNMP GET request using Net-SNMP snmpget
    # -Oqv prints only the value (no "SNMPv2-MIB::..." prefix)
    # -t sets timeout per request
    # -r 0 disables snmpget internal retries (we handle retries in Python)
    cmd = ["snmpget", "-v2c", "-c", community, "-t", str(timeout_s), "-r", "0", "-Oqv", ip, oid]

    start = time.time()
    try:
        # subprocess timeout is slightly higher than snmpget timeout as a safety
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s + 1)
    except subprocess.TimeoutExpired:
        # Python-level timeout triggered
        return False, "timeout", round(time.time() - start, 3), "timeout"

    elapsed = round(time.time() - start, 3)

    # Success case
    if p.returncode == 0:
        return True, p.stdout.strip(), elapsed, ""

    # Failure case: classify error
    err = (p.stderr or p.stdout or "").strip()
    s = err.lower()

    # Timeout/unreachable type errors
    if "timeout" in s or "no response" in s:
        return False, err, elapsed, "timeout"

    # Auth/permission type errors (fail-fast behavior)
    if "authentication failure" in s or "authorizationerror" in s or "no access" in s:
        return False, err, elapsed, "auth"

    # Any other SNMP error
    return False, err or "snmp_error", elapsed, "other"


def poll_target(target, defaults, log):
    # Poll all OIDs for ONE target
    # Applies:
    # - per-request timeout
    # - retries only on timeouts
    # - per-target time budget
    # - fail-fast on auth errors

    name, ip = target["name"], target["ip"]

    # Use target overrides if present, otherwise use defaults
    community = target.get("community", defaults.get("community", "public"))
    timeout_s = float(target.get("timeout_s", defaults["timeout_s"]))
    retries = int(target.get("retries", defaults["retries"]))
    budget_s = float(target.get("target_budget_s", defaults["target_budget_s"]))

    # Default OIDs + any target-specific extra OIDs
    oids = list(defaults["oids"]) + target.get("oids", [])

    start = time.time()
    ok_count = fail_count = 0
    results = []

    log.info("Target start: %s (%s), %d OIDs", name, ip, len(oids))

    # Loop through all OIDs for this target
    for i, oid in enumerate(oids):
        # Enforce per-target budget
        if time.time() - start >= budget_s:
            log.warning("Target budget exceeded on %s", name)
            # Mark remaining OIDs as failed due to budget
            for x in oids[i:]:
                results.append({
                    "oid": x, "ok": False, "value": None, "error": "target budget exceeded",
                    "elapsed_s": 0.0, "attempts": 0
                })
                fail_count += 1
            break

        attempts = 0
        total_elapsed = 0.0

        # Retry loop (only timeouts are retried)
        while attempts <= retries:
            attempts += 1

            # If budget is exceeded during retries, stop
            remain = budget_s - (time.time() - start)
            if remain <= 0:
                results.append({
                    "oid": oid, "ok": False, "value": None, "error": "target budget exceeded",
                    "elapsed_s": round(total_elapsed, 3), "attempts": attempts - 1
                })
                fail_count += 1
                break

            # Run snmpget (per-request timeout is limited by remaining budget)
            ok, val, elapsed, kind = snmpget_v2c(ip, community, oid, min(timeout_s, remain))
            total_elapsed += elapsed

            # Success for this OID
            if ok:
                results.append({
                    "oid": oid, "ok": True, "value": val, "error": None,
                    "elapsed_s": round(total_elapsed, 3), "attempts": attempts
                })
                ok_count += 1
                break

            # Auth error: fail-fast for the target (do not retry)
            if kind == "auth":
                log.error("Auth failure on %s (%s)", name, oid)
                results.append({
                    "oid": oid, "ok": False, "value": None, "error": val or "auth failure",
                    "elapsed_s": round(total_elapsed, 3), "attempts": attempts
                })
                fail_count += 1

                # Mark the rest as skipped
                for x in oids[i + 1:]:
                    results.append({
                        "oid": x, "ok": False, "value": None, "error": "skipped due to auth failure",
                        "elapsed_s": 0.0, "attempts": 0
                    })
                    fail_count += 1

                runtime = round(time.time() - start, 3)
                status = "failed" if ok_count == 0 else "partial"
                log.info(
                    "Target end: %s status=%s ok=%d fail=%d duration=%.3fs",
                    name, status, ok_count, fail_count, runtime
                )
                return {
                    "name": name, "ip": ip, "status": status, "runtime_s": runtime,
                    "ok_count": ok_count, "fail_count": fail_count, "oids": results
                }

            # Timeout: retry if attempts remain
            if kind == "timeout" and attempts <= retries:
                log.warning(
                    "Timeout polling %s on %s (attempt %d/%d), retrying",
                    oid, name, attempts, retries + 1
                )
                continue

            # Other error: no retry
            results.append({
                "oid": oid, "ok": False, "value": None, "error": val or "snmp_error",
                "elapsed_s": round(total_elapsed, 3), "attempts": attempts
            })
            fail_count += 1
            break

    # Target summary
    runtime = round(time.time() - start, 3)
    status = "ok" if fail_count == 0 else "partial" if ok_count else "failed"
    log.info("Target end: %s status=%s ok=%d fail=%d duration=%.3fs", name, status, ok_count, fail_count, runtime)

    return {
        "name": name, "ip": ip, "status": status, "runtime_s": runtime,
        "ok_count": ok_count, "fail_count": fail_count, "oids": results
    }


def main():
    # Parse CLI arguments
    ap = argparse.ArgumentParser(description="Ops-grade SNMP poller")
    ap.add_argument("--config", required=True)       # YAML file path
    ap.add_argument("--out", required=True)          # output JSON file path OR "-"
    ap.add_argument("--log-level", default="INFO")   # logging level text
    a = ap.parse_args()

    # Setup logging
    log = setup_logging(a.log_level)

    # Track total run time
    start = time.time()

    # Load and validate config
    try:
        cfg = load_config(a.config)
        validate_config(cfg)
    except ConfigError as e:
        # Config invalid => exit code 2
        log.error("Config invalid: %s", e)
        return 2

    # Poll all targets
    log.info("Run start: %d targets, output=%s", len(cfg["targets"]), a.out)
    targets = [poll_target(t, cfg["defaults"], log) for t in cfg["targets"]]

    # Build final JSON output object
    out = {
        "run": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "config_file": Path(a.config).name,
            "duration_s": round(time.time() - start, 3),
        },
        "targets": targets,
    }

    # Output JSON (stdout or file)
    if a.out == "-":
        json.dump(out, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)

    # Exit code rules:
    # 0: all OK
    # 1: partial success
    # 2: total failure or config invalid
    ok_total = sum(t["ok_count"] for t in targets)
    fail_total = sum(t["fail_count"] for t in targets)
    code = 2 if ok_total == 0 else 1 if fail_total else 0

    log.info("Run end: exit_code=%d duration=%.3fs", code, out["run"]["duration_s"])
    return code


# Standard Python entry point
if __name__ == "__main__":
    raise SystemExit(main())
