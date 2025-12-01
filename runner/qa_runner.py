#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

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
            "status": "io_error",
            "error": str(exc),
            "latency_ms": None,
            "http_status": None,
        }

    status_ok = resp.status_code == expect_status
    latency_ok = True
    if max_latency_ms is not None:
        latency_ok = latency_ms <= max_latency_ms

    if status_ok and latency_ok:
        status_flag = "pass"
    else:
        status_flag = "fail"

    return {
        "name": name,
        "path": path,
        "status": status_flag,
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


def write_report(
    reports_dir: str,
    module_name: str,
    results: List[Dict[str, Any]],
    health_ok: bool,
    health_error: Optional[str],
) -> str:
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    module_dir = os.path.join(reports_dir, module_name)
    os.makedirs(module_dir, exist_ok=True)
    path = os.path.join(module_dir, f"qa_run_{ts}.json")
    payload: Dict[str, Any] = {
        "module": module_name,
        "health_ok": health_ok,
        "health_error": health_error,
        "results": results,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return path


def wait_for_healthy(
    base_url: str,
    health_endpoint: str,
    timeout_seconds: int,
    interval_seconds: int,
) -> (bool, Optional[str]):
    url = base_url.rstrip("/") + health_endpoint
    log(
        f"Waiting for health endpoint {url} "
        f"(timeout={timeout_seconds}s, interval={interval_seconds}s)..."
    )

    deadline = time.time() + timeout_seconds
    last_error: Optional[str] = None

    while time.time() < deadline:
        try:
            resp = requests.get(url, timeout=3.0)
            if resp.status_code == 200:
                log("Healthcheck OK.")
                return True, None
            last_error = f"status={resp.status_code}, body={resp.text[:128]}"
        except Exception as exc:
            last_error = str(exc)
        log(f"Healthcheck not ready yet: {last_error}")
        time.sleep(max(interval_seconds, 1))

    log("Healthcheck did not become ready before timeout.")
    return False, last_error


def classify_exit_code(
    config_error: bool,
    health_ok: bool,
    test_errors: int,
    io_errors: int,
) -> int:
    """
    Map the situation to exit codes:
    0 -> success
    1 -> invalid input / config error
    2 -> model / AI error (not used here)
    3 -> I/O or unexpected error (includes health not ready or test failures)
    """
    if config_error:
        return 1
    if not health_ok:
        return 3
    if io_errors > 0:
        return 3
    if test_errors > 0:
        return 3
    return 0


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

    config_error = False

    try:
        serviceintent = load_yaml(args.serviceintent)
        test_matrix = load_yaml(args.test_matrix)
    except Exception as exc:
        log(f"Failed to load config YAML: {exc}")
        config_error = True
        # We still proceed to write a minimal report below.

        base_url = "http://invalid"
        image_tag = "unknown"
        tests: List[Dict[str, Any]] = []
    else:
        target_cfg = serviceintent.get("target", {})
        base_url = target_cfg.get("base_url")
        if not base_url:
            log("Missing target.base_url in serviceintent.")
            config_error = True

        deployment_cfg = serviceintent.get("deployment", {})
        base_image = deployment_cfg.get("base_image", "unknown")

        qa_tag = os.environ.get("QA_TAG")
        if qa_tag:
            image_tag = f"{base_image}:{qa_tag}"
        else:
            # Fallback for safety; still usable but less precise
            image_tag = f"{base_image}:latest"

        tests = test_matrix.get("tests", [])

    module_name = args.module_name

    health_ok = True
    health_error: Optional[str] = None
    results: List[Dict[str, Any]] = []
    test_errors = 0
    io_errors = 0

    if not config_error:
        health_endpoint = (
            serviceintent.get("target", {}).get("health_endpoint", "/health")
        )
        timeout_seconds = int(
            serviceintent.get("target", {}).get("health_timeout_seconds", 60)
        )
        interval_seconds = int(
            serviceintent.get("target", {}).get("health_check_interval_seconds", 2)
        )

        health_ok, health_error = wait_for_healthy(
            base_url, health_endpoint, timeout_seconds, interval_seconds
        )

        if not health_ok:
            # Health never became OK -> we do not run tests, but still report.
            log("Healthcheck failed to reach OK state; skipping tests.")
        else:
            log(
                f"Starting QA for module={module_name} "
                f"base_url={base_url} tests={len(tests)}"
            )
            for test in tests:
                r = run_http_test(base_url, test)
                results.append(r)
                status_flag = r.get("status")
                if status_flag == "io_error":
                    io_errors += 1
                elif status_flag != "pass":
                    test_errors += 1
                log(f"Test {r['name']}: {status_flag}")

    exit_code = classify_exit_code(
        config_error=config_error,
        health_ok=health_ok,
        test_errors=test_errors,
        io_errors=io_errors,
    )

    if args.log_json:
        summary = {
            "module": module_name,
            "config_error": config_error,
            "health_ok": health_ok,
            "health_error": health_error,
            "tests": len(results),
            "test_errors": test_errors,
            "io_errors": io_errors,
            "exit_code": exit_code,
        }
        print(json.dumps(summary))

    if not args.dry_run:
        report_path = write_report(
            args.reports_dir, module_name, results, health_ok, health_error
        )
        log(f"Report written to {report_path}")
        if exit_code == 0 and not config_error:
            update_last_good(args.state_file, module_name, image_tag)
            log("last_good.json updated for module " + module_name)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())

