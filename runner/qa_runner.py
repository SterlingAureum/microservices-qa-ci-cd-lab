#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List

import requests
import yaml


def load_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def now_ms() -> int:
    return int(time.time() * 1000)


def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    print(f"[{ts}] {msg}", flush=True)


def run_http_test(base_url: str, test: Dict[str, Any]) -> Dict[str, Any]:
    name = test.get("name", "unnamed")
    path = test.get("path", "/")
    method = test.get("method", "GET").upper()
    expect_status = test.get("expect_status", 200)
    max_latency_ms = test.get("max_latency_ms")

    url = base_url.rstrip("/") + path

    start = now_ms()
    try:
        resp = requests.request(method, url, timeout=5.0)
        latency_ms = now_ms() - start
    except Exception as exc:
        return {
            "name": name,
            "path": path,
            "status": "error",
            "error": str(exc),
            "latency_ms": None,
        }

    status_ok = resp.status_code == expect_status
    latency_ok = True
    if max_latency_ms is not None:
        latency_ok = latency_ms <= max_latency_ms

    status = "pass" if status_ok and latency_ok else "fail"
    return {
        "name": name,
        "path": path,
        "status": status,
        "http_status": resp.status_code,
        "latency_ms": latency_ms,
    }


def update_last_good(state_file: str, module_name: str, image_tag: str) -> None:
    data: Dict[str, Any] = {}
    if os.path.exists(state_file):
        with open(state_file, "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                data = {}

    modules = data.get("modules", {})
    modules[module_name] = {
        "image_tag": image_tag,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    data["modules"] = modules

    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def write_report(reports_dir: str, module_name: str, results: List[Dict[str, Any]]) -> str:
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    module_dir = os.path.join(reports_dir, module_name)
    os.makedirs(module_dir, exist_ok=True)
    path = os.path.join(module_dir, f"qa_run_{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"results": results}, f, indent=2)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generic QA Runner")
    parser.add_argument("--module-name", required=True)
    parser.add_argument("--serviceintent", required=True)
    parser.add_argument("--test-matrix", required=True)
    parser.add_argument("--state-file", required=True)
    parser.add_argument("--reports-dir", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-json", action="store_true")
    args = parser.parse_args()

    module_name = args.module_name
    serviceintent = load_yaml(args.serviceintent)
    test_matrix = load_yaml(args.test_matrix)

    base_url = serviceintent["target"]["base_url"]
    image_tag = serviceintent.get("deployment", {}).get("candidate_image", "unknown")

    tests = test_matrix.get("tests", [])

    results: List[Dict[str, Any]] = []
    errors = 0

    log(f"Starting QA for module={module_name} base_url={base_url}")

    for test in tests:
        r = run_http_test(base_url, test)
        results.append(r)
        if r["status"] != "pass":
            errors += 1
        log(f"Test {r['name']}: {r['status']}")

    # Exit code semantics:
    # 0 -> success
    # 1 -> invalid input (not used yet)
    # 2 -> model/AI error (not used here)
    # 3 -> I/O or unexpected error OR test failures
    exit_code = 0 if errors == 0 else 3

    if args.log_json:
        print(json.dumps({"module": module_name, "errors": errors, "tests": len(results)}))

    if not args.dry_run:
        report_path = write_report(args.reports_dir, module_name, results)
        log(f"Report written to {report_path}")
        if exit_code == 0:
            update_last_good(args.state_file, module_name, image_tag)
            log("last_good.json updated for module " + module_name)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())

