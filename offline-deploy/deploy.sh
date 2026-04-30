#!/usr/bin/env bash
# =============================================================================
# DevOps Platform - 服务器一键部署脚本
# =============================================================================
# 功能：
#   1. 自动检测 Docker / Docker Compose 环境
#   2. 自动生成安全的随机密码
#   3. 自动加载离线镜像（或提示拉取）
#   4. 自动创建数据目录并设置权限（沿用已有 .env 时不提示清空 MySQL 数据）
#   5. 自动等待 MySQL 就绪后执行迁移
#   6. 自动收集静态文件并启动服务
#
# 使用方法：
#   cd offline-deploy
#   bash deploy.sh
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

# ─── 颜色定义 ────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'

log()     { echo -e "${BLUE}[INFO]${NC} $(date '+%H:%M:%S') $*"; }
ok()      { echo -e "${GREEN}[ OK ]${NC} $(date '+%H:%M:%S') $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $(date '+%H:%M:%S') $*"; }
die()     { echo -e "${RED}[FAIL]${NC} $(date '+%H:%M:%S') $*" >&2; exit 1; }

# ─── 工具函数 ────────────────────────────────────────────────────────────────
need_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "缺少命令: $1"
}

gen_password() {
    local length="${1:-24}"
    tr -dc 'A-Za-z0-9!@#%^&*' < /dev/urandom | head -c "$length" || true
}

gen_secret_key() {
    local length="${1:-50}"
    tr -dc 'A-Za-z0-9!@#%^&*(-_=+)' < /dev/urandom | head -c "$length" || true
}

env_value() {
    local key="$1"
    grep "^${key}=" "$ENV_FILE" 2>/dev/null | tail -n 1 | cut -d'=' -f2- | tr -d '\r' || true
}

ensure_env_value() {
    local key="$1"
    local value="$2"
    if ! grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
        echo "${key}=${value}" >> "$ENV_FILE"
        return 0
    fi
    return 1
}

sql_string() {
    local value="$1"
    value="${value//\\/\\\\}"
    value="${value//\'/\'\'}"
    printf "'%s'" "$value"
}

sql_identifier() {
    local value="$1"
    value="${value//\`/\`\`}"
    printf '`%s`' "$value"
}

detect_compose() {
    if docker compose version &>/dev/null; then
        COMPOSE="docker compose"
    elif docker-compose --version &>/dev/null; then
        COMPOSE="docker-compose"
    else
        die "未检测到 Docker Compose，请先安装"
    fi
    ok "Docker Compose: $COMPOSE"
}

# ─── 步骤 1: 环境检查 ────────────────────────────────────────────────────────
log "═══════════════════════════════════════════════════════"
log "  DevOps Platform - 服务器一键部署"
log "═══════════════════════════════════════════════════════"
echo ""

log "Step 1/11: 检查运行环境..."
need_cmd docker
need_cmd grep
need_cmd date
need_cmd mkdir
need_cmd tr
need_cmd head
need_cmd cut
need_cmd awk

# 检查 Docker 是否运行
if ! docker info &>/dev/null; then
    die "Docker 守护进程未运行，请先启动 Docker"
fi
ok "Docker 运行正常"

# 检测 Compose
detect_compose

# 检查架构
ARCH=$(uname -m)
log "服务器架构: $ARCH"
if [[ "$ARCH" != "x86_64" && "$ARCH" != "amd64" ]]; then
    warn "当前架构为 $ARCH，镜像为 linux/amd64，可能不兼容！"
    read -p "是否继续? [y/N] " confirm
    [[ "$confirm" =~ ^[Yy]$ ]] || exit 0
fi

# ─── 步骤 2: 生成 .env 配置 ──────────────────────────────────────────────────
log "Step 2/11: 生成环境配置文件..."

ENV_FILE=".env"
ENV_EXAMPLE=".env.deploy.example"
# 本次部署是否沿用了已有 .env（未重新生成）；为 true 时不提示清空 MySQL 数据目录
KEEP_EXISTING_ENV=false

# 如果 .env 已存在且包含有效密码，询问是否保留
if [[ -f "$ENV_FILE" ]]; then
    if ! grep -q "replace-" "$ENV_FILE" 2>/dev/null; then
        warn ".env 已存在且密码已配置"
        read -p "是否重新生成密码? [y/N] " regen
        if [[ ! "$regen" =~ ^[Yy]$ ]]; then
            ok "保留现有 .env 配置"
            KEEP_EXISTING_ENV=true
        else
            rm -f "$ENV_FILE"
        fi
    else
        rm -f "$ENV_FILE"
    fi
fi

# 生成新的 .env
if [[ ! -f "$ENV_FILE" ]]; then
    MYSQL_ROOT_PASS=$(gen_password 20)
    MYSQL_USER_PASS=$(gen_password 16)
    DJANGO_SECRET=$(gen_secret_key 50)
    TIMESTAMP=$(date +%Y%m%d%H%M%S)

    cat > "$ENV_FILE" << EOF
# =============================================================================
# DevOps Platform - 生产环境配置
# 生成时间: $(date '+%Y-%m-%d %H:%M:%S')
# =============================================================================

# ─── Django 配置 ────────────────────────────────────────────────────────────
DJANGO_ENV=production
DJANGO_DEBUG=false
DJANGO_SECRET_KEY=${DJANGO_SECRET}
DJANGO_ALLOWED_HOSTS=*

# ─── Web 服务端口 ───────────────────────────────────────────────────────────
WEB_PORT=8000

# ─── MySQL 配置 ─────────────────────────────────────────────────────────────
MYSQL_ROOT_PASSWORD=${MYSQL_ROOT_PASS}
MYSQL_DATABASE=devops_platform
MYSQL_USER=devops
MYSQL_PASSWORD=${MYSQL_USER_PASS}
MYSQL_PORT=3306

# ─── MySQL 数据持久化目录 ───────────────────────────────────────────────────
MYSQL_DATA_DIR=/docker/devops/mysql/data

# ─── Django 超级管理员 ─────────────────────────────────────────────────────
ADMIN_USERNAME=admin
ADMIN_EMAIL=admin@devops.local
ADMIN_PASSWORD=$(gen_password 18)
EOF

    ok "已生成 .env，密码已自动配置"
    chmod 600 "$ENV_FILE" 2>/dev/null || true
    echo ""
    echo -e "${CYAN}  生成的密码（请保存）：${NC}"
    echo -e "    MySQL Root 密码: ${GREEN}${MYSQL_ROOT_PASS}${NC}"
    echo -e "    MySQL 用户密码:  ${GREEN}${MYSQL_USER_PASS}${NC}"
    echo -e "    Django Secret:   ${GREEN}${DJANGO_SECRET}${NC}"
    echo -e "    超管用户名:      ${GREEN}admin${NC}"
    echo -e "    超管密码:        ${GREEN}$(env_value ADMIN_PASSWORD)${NC}"
    echo ""
else
    ok ".env 已存在"
fi

# 兼容已经生成过的 .env：缺少超管配置时补齐，不覆盖已有账号密码。
ADMIN_PASSWORD_GENERATED=false
ensure_env_value ADMIN_USERNAME admin || true
ensure_env_value ADMIN_EMAIL admin@devops.local || true
if ensure_env_value ADMIN_PASSWORD "$(gen_password 18)"; then
    ADMIN_PASSWORD_GENERATED=true
fi
chmod 600 "$ENV_FILE" 2>/dev/null || true

ADMIN_USERNAME=$(env_value ADMIN_USERNAME)
ADMIN_EMAIL=$(env_value ADMIN_EMAIL)
ADMIN_PASSWORD=$(env_value ADMIN_PASSWORD)
ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@devops.local}"
[[ -n "$ADMIN_PASSWORD" ]] || die "ADMIN_PASSWORD 为空，请检查 .env"

if [[ "$ADMIN_PASSWORD_GENERATED" == "true" ]]; then
    echo ""
    echo -e "${CYAN}  已补充 Django 超管账号（请保存）：${NC}"
    echo -e "    用户名: ${GREEN}${ADMIN_USERNAME}${NC}"
    echo -e "    密码:   ${GREEN}${ADMIN_PASSWORD}${NC}"
    echo ""
fi

# ─── 步骤 3: 创建数据目录 ────────────────────────────────────────────────────
log "Step 3/11: 准备数据目录..."

MYSQL_DATA_DIR=$(env_value MYSQL_DATA_DIR)
MYSQL_DATA_DIR="${MYSQL_DATA_DIR:-/docker/devops/mysql/data}"

mkdir -p "$MYSQL_DATA_DIR"

# 检查目录是否为空或只有 auto.cnf
shopt -s nullglob dotglob
entries=("$MYSQL_DATA_DIR"/*)
shopt -u nullglob dotglob

if (( ${#entries[@]} > 0 )) && [[ "${entries[0]##*/}" != "auto.cnf" || ${#entries[@]} -gt 1 ]]; then
    if [[ "$KEEP_EXISTING_ENV" == "true" ]]; then
        ok "沿用现有 .env，保留 MySQL 数据目录: $MYSQL_DATA_DIR"
    else
        warn "MySQL 数据目录已存在数据: $MYSQL_DATA_DIR"
        warn "如果这是全新部署，建议清空该目录以避免冲突"
        read -p "是否清空并继续? [y/N] " clear_dir
        if [[ "$clear_dir" =~ ^[Yy]$ ]]; then
            rm -rf "${MYSQL_DATA_DIR:?}"/*
            ok "已清空数据目录"
        fi
    fi
fi

# 设置目录权限（MySQL 容器内以 999:999 运行）
if command -v chown &>/dev/null; then
    chown -R 999:999 "$MYSQL_DATA_DIR" 2>/dev/null || warn "无法设置目录权限，请确保 MySQL 可写入"
fi

ok "数据目录准备完成: $MYSQL_DATA_DIR"

# ─── 步骤 4: 加载离线镜像 ────────────────────────────────────────────────────
log "Step 4/11: 加载 Docker 镜像..."

IMAGES_DIR="images"
WEB_IMAGE_LOADED=false
MYSQL_IMAGE_LOADED=false

if [[ -d "$IMAGES_DIR" ]]; then
    # 加载 Web 镜像
    if [[ -f "${IMAGES_DIR}/devops-platform-web.tar" ]]; then
        log "  加载 Web 镜像..."
        docker load -i "${IMAGES_DIR}/devops-platform-web.tar"
        WEB_IMAGE_LOADED=true
        ok "  Web 镜像加载完成"
    fi

    # 加载 MySQL 镜像
    for img in "${IMAGES_DIR}"/mysql*.tar; do
        [[ -f "$img" ]] || continue
        log "  加载 MySQL 镜像: $(basename "$img")..."
        docker load -i "$img"
        MYSQL_IMAGE_LOADED=true
        ok "  MySQL 镜像加载完成"
        break
    done
fi

# 如果离线镜像不存在，尝试在线拉取
if [[ "$WEB_IMAGE_LOADED" == "false" ]]; then
    warn "未找到 Web 离线镜像，尝试在线构建..."
    ${COMPOSE} build web
    WEB_IMAGE_LOADED=true
fi

if [[ "$MYSQL_IMAGE_LOADED" == "false" ]]; then
    warn "未找到 MySQL 离线镜像，尝试在线拉取..."
    docker pull mysql:8.4
    MYSQL_IMAGE_LOADED=true
fi

# ─── 步骤 5: 启动 MySQL ──────────────────────────────────────────────────────
log "Step 5/11: 启动 MySQL 服务..."

${COMPOSE} up -d mysql

# 等待 MySQL 健康检查通过
log "  等待 MySQL 就绪（最多 3 分钟）..."
for i in {1..60}; do
    STATUS=$(docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}unknown{{end}}' devops-mysql 2>/dev/null || echo "unknown")
    if [[ "$STATUS" == "healthy" ]]; then
        ok "MySQL 已就绪"
        break
    fi
    if [[ $i -eq 60 ]]; then
        warn "MySQL 启动超时"
        docker logs devops-mysql --tail 80
        die "部署失败"
    fi
    sleep 3
done

# ─── 步骤 6: 同步 MySQL 应用账号 ─────────────────────────────────────────────
log "Step 6/11: 同步 MySQL 应用账号..."

MYSQL_ROOT_PASSWORD=$(env_value MYSQL_ROOT_PASSWORD)
MYSQL_DATABASE=$(env_value MYSQL_DATABASE)
MYSQL_USER=$(env_value MYSQL_USER)
MYSQL_PASSWORD=$(env_value MYSQL_PASSWORD)
MYSQL_DATABASE="${MYSQL_DATABASE:-devops_platform}"
MYSQL_USER="${MYSQL_USER:-devops}"

[[ -n "$MYSQL_ROOT_PASSWORD" ]] || die "MYSQL_ROOT_PASSWORD 为空，请检查 .env"
[[ -n "$MYSQL_PASSWORD" ]] || die "MYSQL_PASSWORD 为空，请检查 .env"

MYSQL_DATABASE_SQL=$(sql_identifier "$MYSQL_DATABASE")
MYSQL_USER_SQL=$(sql_string "$MYSQL_USER")
MYSQL_PASSWORD_SQL=$(sql_string "$MYSQL_PASSWORD")

if ! docker exec -i \
    -e MYSQL_PWD="${MYSQL_ROOT_PASSWORD}" \
    devops-mysql \
    mysql -uroot <<SQL
CREATE DATABASE IF NOT EXISTS ${MYSQL_DATABASE_SQL} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS ${MYSQL_USER_SQL}@'%' IDENTIFIED BY ${MYSQL_PASSWORD_SQL};
ALTER USER ${MYSQL_USER_SQL}@'%' IDENTIFIED BY ${MYSQL_PASSWORD_SQL};
GRANT ALL PRIVILEGES ON ${MYSQL_DATABASE_SQL}.* TO ${MYSQL_USER_SQL}@'%';
FLUSH PRIVILEGES;
SQL
then
    warn "无法使用 .env 中的 MYSQL_ROOT_PASSWORD 登录 MySQL"
    warn "通常是已有数据目录使用了旧 root 密码，或之前重新生成过 .env"
    warn "如果这是全新部署，请清空 MYSQL_DATA_DIR 后重试；如果要保留数据，请把 .env 改回旧 root 密码"
    die "MySQL 应用账号同步失败"
fi

ok "MySQL 应用账号已同步: ${MYSQL_USER}@% -> ${MYSQL_DATABASE}"

# ─── 步骤 7: 数据库迁移 ──────────────────────────────────────────────────────
log "Step 7/11: 执行数据库迁移..."

${COMPOSE} run --rm -T web python manage.py migrate --noinput
ok "数据库迁移完成"

# ─── 步骤 8: 创建 Django 超级管理员 ──────────────────────────────────────────
log "Step 8/11: 创建或更新 Django 超级管理员..."

${COMPOSE} run --rm -T \
    -e ADMIN_USERNAME="${ADMIN_USERNAME}" \
    -e ADMIN_EMAIL="${ADMIN_EMAIL}" \
    -e ADMIN_PASSWORD="${ADMIN_PASSWORD}" \
    web python manage.py shell <<'PY'
import os
from django.contrib.auth import get_user_model

User = get_user_model()
username = os.environ["ADMIN_USERNAME"]
email = os.environ.get("ADMIN_EMAIL", "")
password = os.environ["ADMIN_PASSWORD"]

user, created = User.objects.get_or_create(
    username=username,
    defaults={"email": email, "is_staff": True, "is_superuser": True},
)
user.email = email
user.is_staff = True
user.is_superuser = True
user.set_password(password)
user.save()
print(f"{'Created' if created else 'Updated'} superuser: {username}")
PY

ok "Django 超级管理员已就绪: ${ADMIN_USERNAME}"

# ─── 步骤 9: 导入可选 SQL ───────────────────────────────────────────────────
log "Step 9/11: 检查可选 SQL 初始化脚本..."

if [[ -d "sql" ]]; then
    shopt -s nullglob
    sql_files=(sql/*.sql)
    shopt -u nullglob

    if (( ${#sql_files[@]} > 0 )); then
        MYSQL_ROOT_PASSWORD=$(env_value MYSQL_ROOT_PASSWORD)
        MYSQL_DATABASE=$(env_value MYSQL_DATABASE)
        MYSQL_DATABASE="${MYSQL_DATABASE:-devops_platform}"
        for sql_file in "${sql_files[@]}"; do
            log "  导入 SQL: ${sql_file}"
            docker exec -i devops-mysql mysql -uroot -p"${MYSQL_ROOT_PASSWORD}" "${MYSQL_DATABASE}" < "${sql_file}"
        done
        ok "SQL 初始化脚本导入完成"
    else
        ok "未发现 SQL 初始化脚本，跳过"
    fi
else
    ok "未发现 sql 目录，跳过"
fi

# ─── 步骤 10: 收集静态文件 ───────────────────────────────────────────────────
log "Step 10/11: 收集静态文件..."

${COMPOSE} run --rm -T web python manage.py collectstatic --noinput
ok "静态文件收集完成"

# ─── 步骤 11: 启动 Web 服务 ──────────────────────────────────────────────────
log "Step 11/11: 启动 Web 服务..."

${COMPOSE} up -d web

# 等待 Web 服务启动
sleep 5

# 检查 Web 健康状态
WEB_STATUS=$(docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}unknown{{end}}' devops-web 2>/dev/null || echo "unknown")
if [[ "$WEB_STATUS" == "healthy" || "$WEB_STATUS" == "unknown" ]]; then
    ok "Web 服务已启动"
else
    warn "Web 服务状态: $WEB_STATUS，请检查日志"
fi

# ─── 部署完成 ────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                    ✅ 部署完成！                                  ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════════════╝${NC}"
echo ""

# 获取服务器 IP
SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "服务器IP")
WEB_PORT=$(env_value WEB_PORT)
WEB_PORT="${WEB_PORT:-8000}"

echo -e "  ${CYAN}访问地址:${NC} http://${SERVER_IP}:${WEB_PORT}"
echo -e "  ${CYAN}超管账号:${NC} ${ADMIN_USERNAME}"
echo -e "  ${CYAN}超管密码:${NC} ${ADMIN_PASSWORD}"
echo ""
echo -e "  ${CYAN}服务状态:${NC}"
${COMPOSE} ps

echo ""
echo -e "  ${CYAN}常用命令:${NC}"
echo "    查看日志:        ${COMPOSE} logs -f web"
echo "    查看 MySQL 日志: ${COMPOSE} logs -f mysql"
echo "    停止服务:        ${COMPOSE} down"
echo "    重启服务:        ${COMPOSE} restart"
echo "    进入容器:        ${COMPOSE} exec web bash"
echo ""
echo -e "  ${YELLOW}⚠️  请妥善保存 .env 文件中的密码！${NC}"
echo ""
