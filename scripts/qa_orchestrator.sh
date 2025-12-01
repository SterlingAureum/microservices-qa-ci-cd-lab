#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

MODULES="${1:-${QA_DEFAULT_MODULES:-api-v1,ui-v1}}"

echo "[INFO] QA orchestrator started"
echo "[INFO] Project root: ${ROOT_DIR}"
echo "[INFO] Modules: ${MODULES}"

IFS=',' read -r -a module_array <<< "${MODULES}"

overall_exit=0

for module in "${module_array[@]}"; do
  module_trimmed="$(echo "$module" | xargs)"
  echo "[INFO] === QA for module: ${module_trimmed} ==="

  compose_file="modules/${module_trimmed}/docker-compose.module.yml"
  if [[ ! -f "$compose_file" ]]; then
    echo "[WARN] Compose file not found for module ${module_trimmed}: ${compose_file}"
    overall_exit=3
    continue
  fi

  docker compose \
    --project-directory "$ROOT_DIR" \
    --env-file "$ROOT_DIR/.env" \
    -f infra/docker-compose.infra.yml \
    -f "${compose_file}" \
    up --build --abort-on-container-exit

  runner_name="qa-runner-${module_trimmed}"
  exit_code=$(docker inspect -f '{{.State.ExitCode}}' "${runner_name}" 2>/dev/null || echo 3)

  echo "[INFO] ${runner_name} exit code: ${exit_code}"

  if [[ "${exit_code}" != "0" ]]; then
    overall_exit=3
  fi

  docker compose \
    --project-directory "$ROOT_DIR" \
    --env-file "$ROOT_DIR/.env" \
    -f infra/docker-compose.infra.yml \
    -f "${compose_file}" \
    down
done

echo "[INFO] QA orchestrator finished with exit code ${overall_exit}"
exit "${overall_exit}"

