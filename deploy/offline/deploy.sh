#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(pwd)"
COMPOSE_FILE="${ROOT_DIR}/compose/docker-compose.yml"
ENV_FILE="${ROOT_DIR}/compose/.env"
ENV_EXAMPLE="${ROOT_DIR}/compose/.env.deploy.example"
IMAGES_DIR="${ROOT_DIR}/images"
SQL_DIR="${ROOT_DIR}/sql"

log() {
  echo "[INFO] $*"
}

warn() {
  echo "[WARN] $*" >&2
}

die() {
  echo "[ERROR] $*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"
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

validate_env() {
  [[ -f "${ENV_FILE}" ]] || die "Missing env file: ${ENV_FILE}"

  local required_vars=("DJANGO_SECRET_KEY" "MYSQL_ROOT_PASSWORD" "MYSQL_PASSWORD")
  local var
  for var in "${required_vars[@]}"; do
    if ! grep -q "^${var}=" "${ENV_FILE}"; then
      die "Missing required variable in .env: ${var}"
    fi
  done

  if grep -q "replace-with-a-random-secret-key" "${ENV_FILE}" || grep -q "replace-" "${ENV_FILE}"; then
    die ".env still contains placeholder values, please replace them first"
  fi
}

get_env_value() {
  local key="$1"
  local default_value="${2:-}"
  local env_key
  local env_value
  local value=""

  while IFS='=' read -r env_key env_value || [[ -n "${env_key}" ]]; do
    [[ "${env_key}" == "${key}" ]] || continue
    value="${env_value}"
  done < "${ENV_FILE}"

  value="${value%\"}"
  value="${value#\"}"
  value="${value%\'}"
  value="${value#\'}"
  printf '%s' "${value:-${default_value}}"
}

prepare_mysql_data_dir() {
  local mysql_data_dir
  local entries
  mysql_data_dir="$(get_env_value MYSQL_DATA_DIR "/docker/devops/mysql/data")"

  mkdir -p "${mysql_data_dir}"

  shopt -s nullglob dotglob
  entries=("${mysql_data_dir}"/*)
  shopt -u nullglob dotglob

  if (( ${#entries[@]} == 1 )) && [[ "${entries[0]##*/}" == "auto.cnf" ]]; then
    die "MySQL data directory only contains stale auto.cnf: ${mysql_data_dir}. Remove it before first deployment."
  fi

  if command -v chown >/dev/null 2>&1; then
    chown -R 999:999 "${mysql_data_dir}" 2>/dev/null || warn "Could not chown ${mysql_data_dir}; ensure MySQL can write to it"
  fi
}

wait_mysql_healthy() {
  local timeout_sec="${1:-180}"
  local start_ts
  local status
  start_ts="$(date +%s)"

  while true; do
    status="$(docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}unknown{{end}}' devops-mysql 2>/dev/null || true)"
    if [[ "${status}" == "healthy" ]]; then
      log "MySQL is healthy"
      return 0
    fi

    if (( "$(date +%s)" - start_ts > timeout_sec )); then
      docker logs devops-mysql --tail 80 || true
      die "MySQL did not become healthy in ${timeout_sec}s"
    fi
    sleep 3
  done
}

import_sql_files() {
  [[ -d "${SQL_DIR}" ]] || return 0

  shopt -s nullglob
  local sql_files=("${SQL_DIR}"/*.sql)
  shopt -u nullglob

  if (( ${#sql_files[@]} == 0 )); then
    log "No SQL import files found"
    return 0
  fi

  local sql_file
  for sql_file in "${sql_files[@]}"; do
    log "Importing SQL file: ${sql_file}"
    compose exec -T mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"' < "${sql_file}"
  done
}

need_cmd docker
need_cmd grep
need_cmd date
need_cmd mkdir

[[ -f "${COMPOSE_FILE}" ]] || die "Missing compose file: ${COMPOSE_FILE}"
[[ -d "${IMAGES_DIR}" ]] || die "Missing images directory: ${IMAGES_DIR}"

detect_compose

if [[ ! -f "${ENV_FILE}" ]]; then
  [[ -f "${ENV_EXAMPLE}" ]] || die "Missing env template: ${ENV_EXAMPLE}"
  cp "${ENV_EXAMPLE}" "${ENV_FILE}"
  warn "Created ${ENV_FILE} from template. Please edit and rerun."
  exit 1
fi

validate_env
prepare_mysql_data_dir

log "Loading offline images"
shopt -s nullglob
image_archives=("${IMAGES_DIR}"/*.tar)
shopt -u nullglob
(( ${#image_archives[@]} > 0 )) || die "No image tar files found in ${IMAGES_DIR}"

for archive in "${image_archives[@]}"; do
  log "Loading image: ${archive}"
  docker load -i "${archive}"
done

log "Starting mysql service"
compose up -d mysql
wait_mysql_healthy 180

log "Running database migrations"
compose run --rm web python manage.py migrate --noinput

log "Importing SQL data"
import_sql_files

log "Collecting static files"
compose run --rm web python manage.py collectstatic --noinput

log "Starting web service"
compose up -d web

log "Deployment completed"
compose ps
