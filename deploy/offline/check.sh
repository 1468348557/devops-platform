#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(pwd)"
COMPOSE_FILE="${ROOT_DIR}/compose/docker-compose.yml"
ENV_FILE="${ROOT_DIR}/compose/.env"

die() {
  echo "[ERROR] $*" >&2
  exit 1
}

detect_compose() {
  if docker compose version >/dev/null 2>&1; then
    COMPOSE_BIN="docker compose"
  elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE_BIN="docker-compose"
  else
    die "Neither 'docker compose' nor 'docker-compose' is available"
  fi
}

compose() {
  if [[ "${COMPOSE_BIN}" == "docker compose" ]]; then
    docker compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}" "$@"
  else
    docker-compose -f "${COMPOSE_FILE}" --env-file "${ENV_FILE}" "$@"
  fi
}

[[ -f "${COMPOSE_FILE}" ]] || die "Missing compose file: ${COMPOSE_FILE}"
[[ -f "${ENV_FILE}" ]] || die "Missing env file: ${ENV_FILE}"

detect_compose

echo "===== compose ps ====="
compose ps
echo

echo "===== mysql health ====="
docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}no-healthcheck{{end}}' devops-mysql 2>/dev/null || true
echo

echo "===== web last logs ====="
docker logs --tail 80 devops-web 2>/dev/null || true
