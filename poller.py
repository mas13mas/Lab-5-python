#!/usr/bin/env python3
import argparse, json, logging, subprocess, sys, time, yaml
from datetime import datetime, timezone
from pathlib import Path


class ConfigError(Exception):
    pass


def setup_logging(level):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return logging.getLogger("snmp_poller")


def load_config(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {path}")
    except yaml.YAMLError as e:
        raise ConfigError(f"YAML parse error: {e}")
    if not isinstance(cfg, dict):
        raise ConfigError("Config root must be a mapping/object")
    return cfg


def validate_config(cfg):
    if "defaults" not in cfg or "targets" not in cfg:
        raise ConfigError("Config must contain 'defaults' and 'targets'")

    d, ts = cfg["defaults"], cfg["targets"]
    req = ["snmp_version", "timeout_s", "retries", "target_budget_s", "oids"]
    for k in req:
        if k not in d:
            raise ConfigError(f"'defaults' missing: {k}")

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
    if not isinstance(ts, list) or len(ts) < 1:
        raise ConfigError("'targets' must be a non-empty list")

    for i, t in enumerate(ts):
        if not isinstance(t, dict):
            raise ConfigError(f"targets[{i}] must be an object")
        if "name" not in t or "ip" not in t:
            raise ConfigError(f"targets[{i}] must contain 'name' and 'ip'")
        if "community" not in t and "community" not in d:
            raise ConfigError(f"targets[{i}] missing community")
        if "oids" in t and not isinstance(t["oids"], list):
            raise ConfigError(f"targets[{i}].oids must be a list")


def snmpget_v2c(ip, community, oid, timeout_s):
    cmd = ["snmpget", "-v2c", "-c", community, "-t", str(timeout_s), "-r", "0", "-Oqv", ip, oid]
    start = time.time()
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s + 1)
    except subprocess.TimeoutExpired:
        return False, "timeout", round(time.time() - start, 3), "timeout"

    elapsed = round(time.time() - start, 3)
    if p.returncode == 0:
        return True, p.stdout.strip(), elapsed, ""

    err = (p.stderr or p.stdout or "").strip()
    s = err.lower()
    if "timeout" in s or "no response" in s:
        return False, err, elapsed, "timeout"
    if "authentication failure" in s or "authorizationerror" in s or "no access" in s:
        return False, err, elapsed, "auth"
    return False, err or "snmp_error", elapsed, "other"


def poll_target(target, defaults, log):
    name, ip = target["name"], target["ip"]
    community = target.get("community", defaults.get("community", "public"))
    timeout_s = float(target.get("timeout_s", defaults["timeout_s"]))
    retries = int(target.get("retries", defaults["retries"]))
    budget_s = float(target.get("target_budget_s", defaults["target_budget_s"]))
    oids = list(defaults["oids"]) + target.get("oids", [])

    start = time.time()
    ok_count = fail_count = 0
    results = []

    log.info("Target start: %s (%s), %d OIDs", name, ip, len(oids))

    for i, oid in enumerate(oids):
        if time.time() - start >= budget_s:
            log.warning("Target budget exceeded on %s", name)
            for x in oids[i:]:
                results.append({
                    "oid": x, "ok": False, "value": None, "error": "target budget exceeded",
                    "elapsed_s": 0.0, "attempts": 0
                })
                fail_count += 1
            break

        attempts = 0
        total_elapsed = 0.0

        while attempts <= retries:
            attempts += 1
            remain = budget_s - (time.time() - start)
            if remain <= 0:
                results.append({
                    "oid": oid, "ok": False, "value": None, "error": "target budget exceeded",
                    "elapsed_s": round(total_elapsed, 3), "attempts": attempts - 1
                })
                fail_count += 1
                break

            ok, val, elapsed, kind = snmpget_v2c(ip, community, oid, min(timeout_s, remain))
            total_elapsed += elapsed

            if ok:
                results.append({
                    "oid": oid, "ok": True, "value": val, "error": None,
                    "elapsed_s": round(total_elapsed, 3), "attempts": attempts
                })
                ok_count += 1
                break

            if kind == "auth":
                log.error("Auth failure on %s (%s)", name, oid)
                results.append({
                    "oid": oid, "ok": False, "value": None, "error": val or "auth failure",
                    "elapsed_s": round(total_elapsed, 3), "attempts": attempts
                })
                fail_count += 1
                for x in oids[i + 1:]:
                    results.append({
                        "oid": x, "ok": False, "value": None, "error": "skipped due to auth failure",
                        "elapsed_s": 0.0, "attempts": 0
                    })
                    fail_count += 1
                runtime = round(time.time() - start, 3)
                status = "failed" if ok_count == 0 else "partial"
                log.info("Target end: %s status=%s ok=%d fail=%d duration=%.3fs", name, status, ok_count, fail_count, runtime)
                return {
                    "name": name, "ip": ip, "status": status, "runtime_s": runtime,
                    "ok_count": ok_count, "fail_count": fail_count, "oids": results
                }

            if kind == "timeout" and attempts <= retries:
                log.warning("Timeout polling %s on %s (attempt %d/%d), retrying", oid, name, attempts, retries + 1)
                continue

            results.append({
                "oid": oid, "ok": False, "value": None, "error": val or "snmp_error",
                "elapsed_s": round(total_elapsed, 3), "attempts": attempts
            })
            fail_count += 1
            break

    runtime = round(time.time() - start, 3)
    status = "ok" if fail_count == 0 else "partial" if ok_count else "failed"
    log.info("Target end: %s status=%s ok=%d fail=%d duration=%.3fs", name, status, ok_count, fail_count, runtime)
    return {
        "name": name, "ip": ip, "status": status, "runtime_s": runtime,
        "ok_count": ok_count, "fail_count": fail_count, "oids": results
    }


def main():
    ap = argparse.ArgumentParser(description="Ops-grade SNMP poller")
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--log-level", default="INFO")
    a = ap.parse_args()

    log = setup_logging(a.log_level)
    start = time.time()

    try:
        cfg = load_config(a.config)
        validate_config(cfg)
    except ConfigError as e:
        log.error("Config invalid: %s", e)
        return 2

    log.info("Run start: %d targets, output=%s", len(cfg["targets"]), a.out)
    targets = [poll_target(t, cfg["defaults"], log) for t in cfg["targets"]]

    out = {
        "run": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "config_file": Path(a.config).name,
            "duration_s": round(time.time() - start, 3),
        },
        "targets": targets,
    }

    if a.out == "-":
        json.dump(out, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)

    ok_total = sum(t["ok_count"] for t in targets)
    fail_total = sum(t["fail_count"] for t in targets)
    code = 2 if ok_total == 0 else 1 if fail_total else 0
    log.info("Run end: exit_code=%d duration=%.3fs", code, out["run"]["duration_s"])
    return code


if __name__ == "__main__":
    raise SystemExit(main())
