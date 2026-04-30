#!/usr/bin/env bash
# =============================================================================
# DevOps Platform - x86_64 离线部署包打包脚本
# =============================================================================
# 在本地机器（Apple Silicon / x86_64 均可）构建 linux/amd64 镜像，并生成可
# 上传到服务器后一键部署的离线包。
#
# 使用方法：
#   cd offline-deploy
#   bash package-x86_64.sh
#
# 可选环境变量：
#   WEB_IMAGE=devops-platform-web:latest
#   MYSQL_IMAGE=mysql:8.4
#   OUTPUT_DIR=../dist/offline
#   PRIVATE_REGISTRY=registry.example.com
#   BUILDER_NAME=desktop-linux
# =============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'

log()  { echo -e "${BLUE}[INFO]${NC} $(date '+%H:%M:%S') $*"; }
ok()   { echo -e "${GREEN}[ OK ]${NC} $(date '+%H:%M:%S') $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $(date '+%H:%M:%S') $*"; }
die()  { echo -e "${RED}[FAIL]${NC} $(date '+%H:%M:%S') $*" >&2; exit 1; }

need_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "缺少命令: $1"
}

image_arch() {
    docker image inspect "$1" --format '{{.Architecture}}' 2>/dev/null || true
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

PLATFORM="linux/amd64"
EXPECTED_ARCH="amd64"
WEB_IMAGE="${WEB_IMAGE:-devops-platform-web:latest}"
MYSQL_IMAGE="${MYSQL_IMAGE:-mysql:8.4}"
PYTHON_IMAGE="${PYTHON_IMAGE:-python:3.12-slim}"
PRIVATE_REGISTRY="${PRIVATE_REGISTRY:-}"
BUILDER_NAME="${BUILDER_NAME:-}"
OUTPUT_DIR="${OUTPUT_DIR:-../dist/offline}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
PACKAGE_ROOT="offline-deploy"
PACKAGE_NAME="devops-platform-x86_64-${TIMESTAMP}.tar.gz"
OUTPUT_DIR_ABS="$(cd "${SCRIPT_DIR}" && mkdir -p "${OUTPUT_DIR}" && cd "${OUTPUT_DIR}" && pwd)"
PACKAGE_PATH="${OUTPUT_DIR_ABS}/${PACKAGE_NAME}"
STAGING_PARENT="${SCRIPT_DIR}/.package-tmp"
PACKAGE_DIR="${STAGING_PARENT}/${PACKAGE_ROOT}"

cleanup() {
    rm -rf "${STAGING_PARENT}"
}
trap cleanup EXIT

log "═══════════════════════════════════════════════════════"
log "  DevOps Platform - 本地打包 x86_64 离线部署包"
log "═══════════════════════════════════════════════════════"
echo ""

log "Step 1/7: 检查本地环境..."
need_cmd docker
need_cmd tar
need_cmd date
need_cmd mkdir
need_cmd rm
need_cmd du
need_cmd awk

if ! docker info >/dev/null 2>&1; then
    die "Docker 守护进程未运行，请先启动 Docker Desktop 或 Docker Engine"
fi

if ! docker buildx version >/dev/null 2>&1; then
    die "Docker Buildx 不可用，请升级 Docker Desktop 或安装 buildx 插件"
fi
ok "Docker / Buildx 可用"

log "Step 2/7: 初始化 Buildx builder..."
if [[ -z "${BUILDER_NAME}" ]]; then
    BUILDER_NAME="$(docker context show 2>/dev/null || echo default)"
fi
if docker buildx inspect "${BUILDER_NAME}" >/dev/null 2>&1; then
    docker buildx use "${BUILDER_NAME}" >/dev/null
else
    warn "未找到 builder: ${BUILDER_NAME}，改用 docker-container builder"
    BUILDER_NAME="devops-platform-amd64-builder"
    if ! docker buildx inspect "${BUILDER_NAME}" >/dev/null 2>&1; then
        docker buildx create --name "${BUILDER_NAME}" --driver docker-container --use >/dev/null
    else
        docker buildx use "${BUILDER_NAME}" >/dev/null
    fi
fi
docker buildx inspect --bootstrap >/dev/null
BUILDER_DRIVER="$(docker buildx inspect "${BUILDER_NAME}" | awk -F': ' '/^Driver:/ {print $2; exit}')"
ok "Buildx builder 已就绪: ${BUILDER_NAME} (${BUILDER_DRIVER:-unknown})"

log "Step 3/7: 构建 Web 镜像 (${PLATFORM})..."
if [[ -n "${PRIVATE_REGISTRY}" ]]; then
    log "尝试使用私有仓库基础镜像: ${PRIVATE_REGISTRY}/python:3.12-slim"
    docker pull --platform "${PLATFORM}" "${PRIVATE_REGISTRY}/python:3.12-slim" >/dev/null 2>&1 \
        && docker tag "${PRIVATE_REGISTRY}/python:3.12-slim" "${PYTHON_IMAGE}" \
        || warn "私有仓库未命中 python:3.12-slim，将使用 Docker Hub"
fi

log "预拉取基础镜像: ${PYTHON_IMAGE} (${PLATFORM})"
docker pull --platform "${PLATFORM}" "${PYTHON_IMAGE}"

docker buildx build \
    --platform "${PLATFORM}" \
    --tag "${WEB_IMAGE}" \
    --file "${SCRIPT_DIR}/compose/Dockerfile" \
    --load \
    "${SCRIPT_DIR}/compose"

WEB_ARCH="$(image_arch "${WEB_IMAGE}")"
[[ "${WEB_ARCH}" == "${EXPECTED_ARCH}" ]] || die "Web 镜像架构异常: ${WEB_ARCH:-unknown}，期望 ${EXPECTED_ARCH}"
ok "Web 镜像构建完成: ${WEB_IMAGE} (${WEB_ARCH})"

log "Step 4/7: 拉取 MySQL 镜像 (${PLATFORM})..."
if [[ -n "${PRIVATE_REGISTRY}" ]]; then
    docker pull --platform "${PLATFORM}" "${PRIVATE_REGISTRY}/mysql:8.4" >/dev/null 2>&1 \
        && docker tag "${PRIVATE_REGISTRY}/mysql:8.4" "${MYSQL_IMAGE}" \
        || docker pull --platform "${PLATFORM}" "${MYSQL_IMAGE}"
else
    docker pull --platform "${PLATFORM}" "${MYSQL_IMAGE}"
fi

MYSQL_ARCH="$(image_arch "${MYSQL_IMAGE}")"
[[ "${MYSQL_ARCH}" == "${EXPECTED_ARCH}" ]] || die "MySQL 镜像架构异常: ${MYSQL_ARCH:-unknown}，期望 ${EXPECTED_ARCH}"
ok "MySQL 镜像准备完成: ${MYSQL_IMAGE} (${MYSQL_ARCH})"

log "Step 5/7: 生成部署包目录..."
rm -rf "${STAGING_PARENT}"
mkdir -p "${PACKAGE_DIR}/images" "${PACKAGE_DIR}/sql" "${PACKAGE_DIR}/scripts"
mkdir -p "${SCRIPT_DIR}/sql" "${SCRIPT_DIR}/scripts"

tar \
    --exclude='.DS_Store' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='compose/myproject/.env' \
    --exclude='compose/myproject/.runtime' \
    --exclude='compose/myproject/db.sqlite3' \
    --exclude='compose/myproject/test.sqlite3' \
    -C "${SCRIPT_DIR}" \
    -cf - compose docker-compose.yml deploy.sh README.md scripts sql 2>/dev/null \
    | tar -C "${PACKAGE_DIR}" -xf -

cat > "${PACKAGE_DIR}/.env.example" <<'ENVEOF'
# DevOps Platform - 生产环境配置示例
# deploy.sh 首次运行会自动生成 .env 和随机密码；如需固定密码，可复制本文件为 .env 后修改。

DJANGO_ENV=production
DJANGO_DEBUG=false
DJANGO_SECRET_KEY=replace-with-a-random-secret-key
DJANGO_ALLOWED_HOSTS=*

WEB_PORT=8000

MYSQL_ROOT_PASSWORD=replace-root-password
MYSQL_DATABASE=devops_platform
MYSQL_USER=devops
MYSQL_PASSWORD=replace-devops-password
MYSQL_PORT=3306
MYSQL_DATA_DIR=/docker/devops/mysql/data

ADMIN_USERNAME=admin
ADMIN_EMAIL=admin@devops.local
ADMIN_PASSWORD=replace-admin-password
ENVEOF
ok "部署目录已生成"

log "Step 6/7: 导出 Docker 镜像..."
docker save -o "${PACKAGE_DIR}/images/devops-platform-web.tar" "${WEB_IMAGE}"
docker save -o "${PACKAGE_DIR}/images/mysql-8.4.tar" "${MYSQL_IMAGE}"
ok "镜像已导出:"
du -sh "${PACKAGE_DIR}/images/"*.tar

log "Step 7/7: 生成压缩包..."
tar -C "${STAGING_PARENT}" -czf "${PACKAGE_PATH}" "${PACKAGE_ROOT}"
PACKAGE_SIZE="$(du -sh "${PACKAGE_PATH}" | awk '{print $1}')"

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                    打包完成                                      ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${CYAN}部署包:${NC} ${PACKAGE_PATH}"
echo -e "  ${CYAN}大小:${NC}   ${PACKAGE_SIZE}"
echo -e "  ${CYAN}架构:${NC}   ${PLATFORM}"
echo ""
echo "  上传到服务器:"
echo "    scp ${PACKAGE_PATH} root@server-ip:/opt/"
echo ""
echo "  服务器一键部署:"
echo "    cd /opt"
echo "    tar -xzf ${PACKAGE_NAME}"
echo "    cd offline-deploy"
echo "    bash deploy.sh"
echo ""
warn "首次跨架构构建会比较慢；后续会复用 Docker buildx 缓存。"
