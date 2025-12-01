# Microservices QA CI/CD Lab

A small but realistic lab project for experimenting with local CI/CD-style QA pipelines for Dockerized microservices.

This repository demonstrates how to:

- Run multiple microservice modules (API, UI, etc.) behind a single HTTPS entrypoint (Traefik).
- Attach a per-module sidecar QA runner that executes configuration-driven HTTP tests.
- Gate tests on application health (wait for `/health` to become ready before running checks).
- Track the last known good image per module via `state/last_good.json`, using deterministic timestamp tags.
- Persist structured JSON QA reports under `reports/`.

It is designed as a client-agnostic, open-source abstraction of a real-world microservices QA project and can be used as a starting point for future engagements.

---

## 1. Tech Stack and Versions

This lab intentionally uses a simple and widely available stack:

- **Docker / Docker Compose**
  - Docker Engine: 24.x+ (tested on Linux)
  - Docker Compose v2.x (`docker compose` CLI)

- **Runtime and Services**
  - Python: `python:3.11-slim` (base image)
  - FastAPI (API and UI demo services)
  - Uvicorn (ASGI server)
  - Traefik: `traefik:v2.11` (reverse proxy, single HTTPS entrypoint)
  - Prometheus: `prom/prometheus:latest` (metrics collection)

- **Languages**
  - Python 3.11 for services and QA runner
  - Bash for orchestration scripts

> Versions are intentionally minimal. Pin them in your own fork if you require strict reproducibility.

---

## 2. High-Level Architecture

At a high level, the lab runs multiple microservice modules (such as `api-v1`, `ui-v1`) that each consist of:

- One application container (`app-<module>`) implementing the microservice.
- One sidecar QA runner container (`qa-runner-<module>`) that:
  - Reads `serviceintent.yaml` (what to test and which base URL to use).
  - Reads `test_matrix.yaml` (which HTTP checks to execute).
  - Waits for the service to become healthy before running the test matrix.
  - Executes the tests and writes JSON reports.
  - Updates `state/last_good.json` if the module passes all checks.

All containers are attached to a shared Docker network `qa_net` fronted by Traefik and scraped by Prometheus.

### 2.1 ASCII Architecture Diagram

```text
                          +----------------------------+
                          |     QA Orchestrator       |
                          |  scripts/qa_orchestrator  |
                          +-------------+-------------+
                                        |
                                        v
                          (per module: api-v1, ui-v1, ...)
    +---------------------------------------------------------------+
    | Docker Host                                                   |
    |                                                               |
    |  +---------------------+      +----------------------------+  |
    |  |  Infra Stack        |      |  Module Stack (api-v1)     |  |
    |  |  (infra/*)          |      |  (modules/api-v1/*)        |  |
    |  |                     |      |                            |  |
    |  |  +--------------+   |      |  +----------------------+  |  |
    |  |  |  Traefik     |<---------+->|  app-api-v1          |  |  |
    |  |  |  :443        |   |      |  |  FastAPI service     |  |  |
    |  |  +--------------+   |      |  +----------------------+  |  |
    |  |          ^          |      |               ^            |  |
    |  |  +--------------+   |      |  +----------------------+  |  |
    |  |  | Prometheus   |<---------+->| qa-runner-api-v1     |  |  |
    |  |  | :9090        |   |      |  | (sidecar QA runner)  |  |  |
    |  |  +--------------+   |      |  +----------------------+  |  |
    |  |                     |      |                            |  |
    |  +---------------------+      +----------------------------+  |
    |                                                               |
    |  Shared network: qa_net                                       |
    |                                                               |
    |  Volumes:                                                     |
    |    ./modules/<module>/configs  -> /configs                    |
    |    ./state/last_good.json      -> /state/last_good.json       |
    |    ./reports                   -> /reports                    |
    +---------------------------------------------------------------+
```

Each module follows the same pattern, with its own app container, runner, and configuration, but all share the same infra (Traefik, Prometheus, Docker network).

---

## 3. Repository Layout

A typical layout for this lab:

```text
microservices-qa-ci-cd-lab/
  README.md
  LICENSE
  .env.example

  infra/
    docker-compose.infra.yml
    traefik/
      traefik.yml
    prometheus/
      prometheus.yml

  runner/
    Dockerfile
    qa_runner.py

  services/
    api/
      v1/
        Dockerfile
        app/
          __init__.py
          main.py
    ui/
      v1/
        Dockerfile
        app/
          __init__.py
          server.py

  modules/
    api-v1/
      docker-compose.module.yml
      configs/
        serviceintent.yaml
        test_matrix.yaml
    ui-v1/
      docker-compose.module.yml
      configs/
        serviceintent.yaml
        test_matrix.yaml

  state/
    last_good.json

  reports/
    .gitkeep

  scripts/
    qa_orchestrator.sh
```

---

## 4. How the QA Pipeline Works

### 4.1 Per-module configuration

Each module (for example `api-v1`) defines its own:

- `modules/api-v1/configs/serviceintent.yaml`

  Conceptually:

  ```yaml
  service: "api"
  module: "api-v1"

  target:
    base_url: "http://app-api-v1:8000"
    health_endpoint: "/health"
    metrics_endpoint: "/metrics"
    health_timeout_seconds: 60
    health_check_interval_seconds: 2

  sla:
    max_latency_ms: 500
    max_error_rate: 0.01

  deployment:
    base_image: "microservices-qa-ci-cd-lab-app-api-v1"
  ```

  - `target.*`: how to reach the service inside Docker and how long to wait for it to become healthy.
  - `sla.*`: soft hints for latency / error tolerance.
  - `deployment.base_image`: the logical base image name; the actual tag is injected by the orchestrator based on a timestamp.

- `modules/api-v1/configs/test_matrix.yaml`

  Conceptually:

  ```yaml
  tests:
    - name: "baseline_health"
      method: "GET"
      path: "/health"
      expect_status: 200
      max_latency_ms: 500

    - name: "slow_endpoint"
      method: "GET"
      path: "/slow"
      expect_status: 200
      max_latency_ms: 2000

    - name: "error_endpoint"
      method: "GET"
      path: "/error"
      expect_status: 500
  ```

  - Each test describes:
    - HTTP method
    - Request path
    - Expected status code
    - Optional max latency in milliseconds

The QA runner does not generate tests by itself; it executes whatever is defined in the test matrix. This keeps the runner small, generic, and easy to reason about.

### 4.2 Versioning and `last_good.json`

For each module run, the orchestrator:

1. Generates a deterministic tag: `QA_TAG=YYYYMMDD-HHMMSS`.
2. Builds the application image as:

   ```text
   microservices-qa-ci-cd-lab-app-<module>:${QA_TAG}
   ```

3. Injects `QA_TAG` into:
   - The module compose file (for image tags).
   - The QA runner container environment.

The runner uses `deployment.base_image` and `QA_TAG` to compute the final image tag:

```text
<base_image>:<QA_TAG>
```

If all tests pass, `state/last_good.json` is updated with an entry like:

```json
{
  "modules": {
    "api-v1": {
      "image_tag": "microservices-qa-ci-cd-lab-app-api-v1:20251201-151020",
      "updated_at": "2025-12-01T15:10:30Z"
    },
    "ui-v1": {
      "image_tag": "microservices-qa-ci-cd-lab-app-ui-v1:20251201-151034",
      "updated_at": "2025-12-01T15:10:42Z"
    }
  }
}
```

Downstream deployment or rollback scripts can use this file as the source of truth for “last known good” per module.

### 4.3 Execution flow

For each module, the orchestrator performs:

1. **Build**

   - Build the application image with a timestamp tag.
   - Build the generic QA runner image (`microservices-qa-ci-cd-lab-runner`).

2. **Bring up the QA stack**

   - Start Traefik and Prometheus (infra stack).
   - Start the application container (`app-<module>`).
   - Start the sidecar QA runner container (`qa-runner-<module>`).

3. **Health gating**

   Inside the runner:

   - Read `serviceintent.yaml` to determine:
     - `base_url`
     - `health_endpoint`
     - health timeout and interval
   - Poll the health endpoint until it returns `200 OK` or the timeout is reached.
     - If it never becomes healthy, tests are skipped and the run is marked as failed.

4. **Run QA tests**

   - Load `test_matrix.yaml`.
   - Execute each HTTP test in sequence, collecting:
     - `status`: `pass` / `fail` / `io_error`
     - observed HTTP status code
     - latency in milliseconds
   - Write results to:

     ```text
     reports/<module>/qa_run_<timestamp>.json
     ```

5. **Exit codes and `last_good`**

   The runner uses a small exit-code scheme:

   - `0` → success
   - `1` → invalid input / configuration error
   - `2` → reserved for model/AI errors (not used in this lab yet)
   - `3` → I/O or unexpected error (including health not ready or test failures)

   Behavior:

   - If all tests pass:
     - Runner exits with code `0`.
     - `state/last_good.json` is updated for that module with the current `<base_image>:<QA_TAG>`.
   - If any tests fail, there are I/O errors, or health never becomes OK:
     - Runner exits with code `3`.
     - `last_good.json` is **not** updated.

6. **Cleanup**

   - The orchestrator inspects the runner exit code.
   - The compose stack for that module (including infra) is torn down.
   - The orchestrator then moves on to the next module.

At the end of the run, you have:

- Per-module structured QA reports under `reports/`.
- A single `state/last_good.json` file describing the last-known-good images per module.

### 4.4 Runner flags: `--dry-run` and `--log-json`

The runner supports two useful flags (mainly for CI and debugging):

- `--dry-run`
  - Executes the health gating and test logic but does **not**:
    - write reports
    - update `last_good.json`
  - Useful for quick validations or experimentation.

- `--log-json`
  - Emits a single JSON summary line to stdout with keys such as:
    - `module`
    - `config_error`
    - `health_ok`
    - `health_error`
    - `tests`
    - `test_errors`
    - `io_errors`
    - `exit_code`
  - This makes it easy to integrate with log aggregators or CI parsers.

In normal use via `qa_orchestrator.sh`, flags are wired inside the runner container command and you don’t need to call them directly, but they can be handy if you want to run the runner manually.

---

## 5. Quickstart

### 5.1 Prerequisites

- Linux environment (or WSL2) with:
  - Docker Engine 24.x+
  - Docker Compose v2.x
- Basic POSIX shell tools (`bash`, `sed`, etc.).

### 5.2 Setup

1. Clone the repository:

   ```bash
   git clone https://github.com/SterlingAureum/microservices-qa-ci-cd-lab.git
   cd microservices-qa-ci-cd-lab
   ```

2. Create a `.env` file from the example:

   ```bash
   cp .env.example .env
   ```

   Example content:

   ```bash
   QA_BASE_DOMAIN=qa.local
   QA_DOCKER_NETWORK=qa_net
   QA_DEFAULT_MODULES=api-v1,ui-v1
   ```

3. (Optional) Verify the module configurations:

   - `modules/api-v1/configs/serviceintent.yaml`
   - `modules/api-v1/configs/test_matrix.yaml`
   - `modules/ui-v1/configs/serviceintent.yaml`
   - `modules/ui-v1/configs/test_matrix.yaml`

### 5.3 Run the QA pipeline

From the project root:

```bash
bash scripts/qa_orchestrator.sh
```

This will:

- Build and start the infra stack (Traefik + Prometheus).
- Generate a `QA_TAG` and run the QA stack for `api-v1`, then tear it down.
- Generate another `QA_TAG` and run the QA stack for `ui-v1`, then tear it down.
- Exit with a non-zero code if any module runner reports failures.

### 5.4 Inspect results

- State of last-known-good modules:

  ```bash
  cat state/last_good.json
  ```

- Per-module QA reports:

  ```bash
  ls reports/api-v1
  cat reports/api-v1/qa_run_*.json
  ```

While a module stack is running, you can also point your browser or `curl` to the Traefik and Prometheus endpoints (depending on how you expose them), but the lab is primarily designed to be driven by the QA runner and orchestrator.

### 5.5 Example run

```bash
$ bash scripts/qa_orchestrator.sh
[INFO] QA orchestrator started
[INFO] Modules: api-v1,ui-v1
[INFO] === QA for module: api-v1 ===
[INFO] Using QA_TAG=20251201-151020 for module api-v1
[INFO] Waiting for health endpoint http://app-api-v1:8000/health ...
[INFO] Healthcheck OK.
[INFO] Test baseline_health: pass
[INFO] Test slow_endpoint: pass
[INFO] Test error_endpoint: pass
[INFO] qa-runner-api-v1 exit code: 0
[INFO] === QA for module: ui-v1 ===
...

$ cat state/last_good.json
{
  "modules": {
    "api-v1": {
      "image_tag": "microservices-qa-ci-cd-lab-app-api-v1:20251201-151020",
      "updated_at": "2025-12-01T15:10:30Z"
    },
    "ui-v1": {
      "image_tag": "microservices-qa-ci-cd-lab-app-ui-v1:20251201-151034",
      "updated_at": "2025-12-01T15:10:42Z"
    }
  }
}
```

---

## 6. Limitations and Roadmap

This lab is intentionally minimal and focuses on clarity:

- The QA runner executes predefined HTTP tests; it does not generate tests automatically.
- The demo services expose simple endpoints to simulate:
  - Normal responses (`/health`, `/`)
  - Slow responses (`/slow`)
  - Error responses (`/error`)
- Fault injection is currently encoded in the demo endpoints rather than via full-blown:
  - network-level chaos
  - container pause/unpause
  - database failures

Planned and potential future improvements:

- Multi-phase test matrices (baseline → slow → error → recovery).
- Application-level fault injection via dedicated control endpoints.
- More detailed integration with Prometheus (per-test metrics, histograms).
- Optional parallel execution of multiple modules.
- Pluggable output sinks (S3, object storage, CI artifacts) for reports.

---

## 7. Use Cases

This repository can be used as:

- A portfolio project to demonstrate experience with:
  - Dockerized microservices
  - Reverse proxies (Traefik)
  - CI/CD-style QA orchestration
  - Configuration-driven testing and environment modeling
- A starting point for:
  - Client-specific QA pipelines
  - Local microservices experimentation
  - Teaching and workshops on DevOps / QA automation

It is intentionally client-agnostic and contains no proprietary logic.
