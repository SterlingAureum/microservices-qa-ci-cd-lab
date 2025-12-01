# Microservices QA CI/CD Lab

A small but realistic lab project for experimenting with **local CI/CD-style QA pipelines** for Dockerized microservices.

This repository demonstrates how to:
- Run multiple microservice *modules* (API, UI, etc.) behind a single HTTPS entrypoint (Traefik).
- Attach a per-module **sidecar QA runner** that executes configuration-driven HTTP tests.
- Track the last known good image per module via `state/last_good.json`.
- Persist structured JSON QA reports under `reports/`.

It is designed as a client-agnostic, open-source abstraction of a real-world Upwork project, and can be used as a starting point for future engagements.

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

> Note: versions are intentionally minimal and may be updated over time. Pin them in your own fork if you require strict reproducibility.

---

## 2. High-Level Architecture

At a high level, the lab runs multiple microservice **modules** (such as `api-v1`, `ui-v1`) that each consist of:

- One application container (`app-<module>`) implementing the microservice.
- One sidecar QA runner container (`qa-runner-<module>`) that:
  - Reads `serviceintent.yaml` (what to test and which base URL to use).
  - Reads `test_matrix.yaml` (which HTTP checks to execute).
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
|    ./reports                  -> /reports                     |
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

### 4.1 Configuration

Each **module** (for example `api-v1`) defines its own:

- `modules/api-v1/configs/serviceintent.yaml`
  - Target base URL (inside Docker), health and metrics endpoints.
  - SLA hints such as max latency and acceptable error rate.
  - Candidate image tag metadata (for human inspection).

- `modules/api-v1/configs/test_matrix.yaml`
  - A list of HTTP tests:
    - HTTP method
    - Request path
    - Expected status code
    - Optional max latency in milliseconds

Each module can define a full microservice stack (multiple containers), but the current QA runner targets a single entrypoint per module for simplicity.

The QA runner does not generate tests by itself; it executes whatever is defined in the test matrix. This keeps the runner small, generic, and easy to reason about.

### 4.2 Execution Flow

For each module, the orchestrator performs:

1. **Build step**
   - Build the application image (for example `microservices-qa-ci-cd-lab-app-api-v1`).
   - Build the generic QA runner image (`microservices-qa-ci-cd-lab-runner`).

2. **Bring up the QA stack**
   - Start Traefik and Prometheus (infra stack).
   - Start the application container (`app-<module>`).
   - Start the sidecar QA runner container (`qa-runner-<module>`).

3. **Run QA inside the runner**
   - The runner loads `serviceintent.yaml` and `test_matrix.yaml`.
   - It executes each HTTP test in sequence, collecting:
     - status (pass / fail / error)
     - observed HTTP status code
     - latency in milliseconds
   - Results are written to:
     - `reports/<module>/qa_run_<timestamp>.json`

4. **Exit codes and last_good**
   - If all tests pass:
     - Runner exits with code `0`.
     - Runner updates `state/last_good.json` for that module.
   - If any test fails or throws errors:
     - Runner exits with a non-zero code (`3` for now).
     - `last_good.json` is not updated.

5. **Cleanup**
   - The orchestrator inspects the runner exit code.
   - The compose stack for that module (including infra) is torn down.
   - The orchestrator then moves on to the next module.

At the end of the run, you have:

- Per-module structured QA reports under `reports/`.
- A single `state/last_good.json` file describing the last-known-good images per module.

---

## 5. Quickstart

### 5.1 Prerequisites

- Linux environment (or WSL2) with:
  - Docker Engine 24.x+
  - Docker Compose v2.x
- Basic POSIX shell tools (bash, sed, etc.).

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
- Run the QA stack for `api-v1`, then tear it down.
- Run the QA stack for `ui-v1`, then tear it down.
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

You can also point your browser or curl to the Traefik and Prometheus endpoints while the stack is running (if you expose them), but the lab is primarily designed to be driven by the QA runner and orchestrator.

### 5.5 Example run

```bash
$ bash scripts/qa_orchestrator.sh
[INFO] QA orchestrator started
[INFO] Modules: api-v1,ui-v1
[INFO] === QA for module: api-v1 ===
[INFO] Test baseline_health: pass
[INFO] Test slow_endpoint: pass
[INFO] Test error_endpoint: pass
[INFO] qa-runner-api-v1 exit code: 0
[INFO] === QA for module: ui-v1 ===
...
```
```bash
$ cat state/last_good.json
{
  "modules": {
    "api-v1": {
      "image_tag": "microservices-qa-ci-cd-lab-app-api-v1:latest",
      "updated_at": "2025-12-01T08:30:12Z"
    },
    "ui-v1": {
      "image_tag": "microservices-qa-ci-cd-lab-app-ui-v1:latest",
      "updated_at": "2025-12-01T08:31:05Z"
    }
  }
}

```

---

## 6. Limitations and Roadmap

This lab is intentionally minimal and focuses on clarity:

- The QA runner executes **predefined** HTTP tests; it does not generate tests automatically.
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

- A **portfolio project** to demonstrate experience with:
  - Dockerized microservices
  - Reverse proxies (Traefik)
  - CI/CD-style QA orchestration
  - Configuration-driven testing and environment modeling
- A starting point for:
  - Client-specific QA pipelines
  - Local microservices experimentation
  - Teaching and workshops on DevOps / QA automation

It is intentionally client-agnostic and contains no proprietary logic.

