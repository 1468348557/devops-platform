#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${ROOT_DIR}/dist"
BUILD_TS="$(date +%Y%m%d-%H%M%S)"
BUNDLE_NAME="devops-platform-offline-${BUILD_TS}"
BUNDLE_DIR="${DIST_DIR}/${BUNDLE_NAME}"
IMAGES_DIR="${BUNDLE_DIR}/images"
SQL_SOURCE_DIR="${ROOT_DIR}/sql"
SQL_BUNDLE_DIR="${BUNDLE_DIR}/sql"

WEB_IMAGE="devops-platform-web:1.0.0"
MYSQL_IMAGE="mysql:8.4"
TARGET_PLATFORM="${TARGET_PLATFORM:-linux/amd64}"
export DOCKER_DEFAULT_PLATFORM="${TARGET_PLATFORM}"

log() {
  echo "[INFO] $*"
}

die() {
  echo "[ERROR] $*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Missing command: $1"
}

need_cmd docker
need_cmd tar
need_cmd cp
need_cmd mkdir
need_cmd rm

if ! docker compose version >/dev/null 2>&1; then
  die "docker compose plugin is required"
fi

log "Preparing directories"
rm -rf "${BUNDLE_DIR}"
mkdir -p "${IMAGES_DIR}" "${BUNDLE_DIR}/compose" "${BUNDLE_DIR}/deploy" "${SQL_BUNDLE_DIR}"

log "Target image platform: ${TARGET_PLATFORM}"

log "Building Django application image: ${WEB_IMAGE}"
(
  cd "${ROOT_DIR}"
  docker compose --env-file .env.deploy.example build web
)

log "Pulling MySQL image: ${MYSQL_IMAGE}"
docker pull --platform "${TARGET_PLATFORM}" "${MYSQL_IMAGE}"

log "Saving images to tar files"
docker save -o "${IMAGES_DIR}/devops-platform-web_1.0.0.tar" "${WEB_IMAGE}"
docker save -o "${IMAGES_DIR}/mysql_8.4.tar" "${MYSQL_IMAGE}"

log "Copying deployment manifests and scripts"
cp "${ROOT_DIR}/docker-compose.yml" "${BUNDLE_DIR}/compose/docker-compose.yml"
cp "${ROOT_DIR}/.env.deploy.example" "${BUNDLE_DIR}/compose/.env.deploy.example"
cp "${ROOT_DIR}/deploy/offline/deploy.sh" "${BUNDLE_DIR}/deploy/deploy.sh"
cp "${ROOT_DIR}/deploy/offline/check.sh" "${BUNDLE_DIR}/deploy/check.sh"
cp "${ROOT_DIR}/deploy/offline/README.md" "${BUNDLE_DIR}/README.md"
cp "${ROOT_DIR}/deploy/offline/install_docker_kylin.md" "${BUNDLE_DIR}/install_docker_kylin.md"

if [[ -d "${SQL_SOURCE_DIR}" ]]; then
  shopt -s nullglob
  sql_files=("${SQL_SOURCE_DIR}"/*.sql)
  shopt -u nullglob
  if (( ${#sql_files[@]} > 0 )); then
    log "Copying SQL import files"
    cp "${sql_files[@]}" "${SQL_BUNDLE_DIR}/"
  fi
fi

chmod +x "${BUNDLE_DIR}/deploy/deploy.sh" "${BUNDLE_DIR}/deploy/check.sh"

log "Creating bundle archive"
(
  cd "${DIST_DIR}"
  tar -czf "${BUNDLE_NAME}.tar.gz" "${BUNDLE_NAME}"
)

log "Bundle created successfully"
echo "${DIST_DIR}/${BUNDLE_NAME}.tar.gz"
