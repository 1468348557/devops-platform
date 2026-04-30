#!/bin/bash
# ============================================================
# Docker & Docker-Compose 麒麟系统离线安装脚本
# 适用系统：Kylin OS (V4/V10, arm64 或 x86_64)
# 使用方法：
#   1. 在有网的机器上下载物料到 物料/ 目录
#   2. 将整个 docker安装/ 目录拷贝到目标服务器
#   3. 执行 bash install_docker_kylin.sh
# ============================================================

set -e

# ---- 配置 ----
DOCKER_VERSION="27.5.1"          # Docker 版本（与物料包对应）
DOCKER_COMPOSE_VERSION="2.32.4"  # docker-compose 版本（与物料包对应）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 下载镜像源（国内网络不通时，改为镜像站）
# 可选镜像：
#   Docker 官方：https://download.docker.com
#   阿里云镜像：https://mirrors.aliyun.com/docker-ce/linux/static/stable
#   Docker-Compose 官方：https://github.com/docker/compose/releases/download
#   阿里云镜像（compose）：https://mirrors.aliyun.com/docker-toolbox/linux/compose
DOCKER_MIRROR="https://mirrors.aliyun.com/docker-ce/linux/static/stable"
COMPOSE_MIRROR="https://github.com/docker/compose/releases/download"
MATERIAL_DIR="${SCRIPT_DIR}/物料"
DOCKER_PKG_DIR="${MATERIAL_DIR}/docker"
COMPOSE_PKG_DIR="${MATERIAL_DIR}/docker-compose"

# ---- 颜色输出 ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ---- 环境检查 ----
check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "请使用 root 用户执行本脚本"
        exit 1
    fi
}

check_arch() {
    ARCH=$(uname -m)
    log_info "检测到系统架构: ${ARCH}"
    if [[ "$ARCH" == "x86_64" ]]; then
        ARCH_TAG="x86_64"
    elif [[ "$ARCH" == "aarch64" || "$ARCH" == "arm64" ]]; then
        ARCH_TAG="aarch64"
    else
        log_error "不支持的架构: ${ARCH}"
        exit 1
    fi
}

# ---- 检查物料是否齐全 ----
check_materials() {
    log_info "检查安装物料..."

    # 检查 docker 二进制包
    DOCKER_BIN=$(ls "${DOCKER_PKG_DIR}"/docker-*.tgz 2>/dev/null | head -1)
    if [[ -z "$DOCKER_BIN" ]]; then
        log_error "未找到 Docker 二进制包，请先下载到: ${DOCKER_PKG_DIR}/"
        log_info "下载命令参考（在有网的机器上执行）:"
        log_info "  wget https://download.docker.com/linux/static/stable/${ARCH_TAG}/docker-${DOCKER_VERSION}.tgz -O ${DOCKER_PKG_DIR}/docker-${DOCKER_VERSION}.tgz"
        exit 1
    fi
    log_info "找到 Docker 包: $(basename "$DOCKER_BIN")"

    # 检查 docker-compose 二进制文件
    COMPOSE_BIN=$(ls "${COMPOSE_PKG_DIR}"/docker-compose-* 2>/dev/null | head -1)
    if [[ -z "$COMPOSE_BIN" ]]; then
        log_error "未找到 docker-compose 二进制文件，请先下载到: ${COMPOSE_PKG_DIR}/"
        log_info "下载命令参考:"
        log_info "  wget https://github.com/docker/compose/releases/download/v${DOCKER_COMPOSE_VERSION}/docker-compose-linux-${ARCH_TAG} -O ${COMPOSE_PKG_DIR}/docker-compose-linux-${ARCH_TAG}"
        exit 1
    fi
    log_info "找到 docker-compose: $(basename "$COMPOSE_BIN")"
}

# ---- 安装 Docker ----
install_docker() {
    if systemctl is-active docker &>/dev/null; then
        log_warn "Docker 已在运行，跳过安装"
        docker --version
        return
    fi

    log_info "开始安装 Docker..."

    # 解压 docker 二进制包
    local docker_tgz
    docker_tgz=$(ls "${DOCKER_PKG_DIR}"/docker-*.tgz | head -1)
    log_info "解压: $(basename "$docker_tgz")"
    tar -xzf "$docker_tgz" -C /tmp/

    # 安装二进制文件
    cp -f /tmp/docker/* /usr/bin/
    chmod +x /usr/bin/docker*
    rm -rf /tmp/docker

    # 创建 docker 组（如果不存在）
    getent group docker >/dev/null || groupadd docker

    # 创建 systemd 服务文件
    log_info "创建 systemd 服务文件..."
    cat > /etc/systemd/system/docker.service <<'EOF'
[Unit]
Description=Docker Application Container Engine
Documentation=https://docs.docker.com
After=network-online.target firewalld.service containerd.service
Wants=network-online.target

[Service]
Type=notify
ExecStart=/usr/bin/dockerd
ExecReload=/bin/kill -s HUP $MAINPID
TimeoutSec=0
RestartSec=2
Restart=always
StartLimitBurst=3
StartLimitInterval=60s
LimitNOFILE=infinity
LimitNPROC=infinity
LimitCORE=infinity
TasksMax=infinity
Delegate=yes
KillMode=process
OOMScoreAdjust=-500

[Install]
WantedBy=multi-user.target
EOF

    # 创建 docker socket 服务
    cat > /etc/systemd/system/docker.socket <<'EOF'
[Unit]
Description=Docker Socket for the API

[Socket]
ListenStream=/var/run/docker.sock
SocketMode=0660
SocketUser=root
SocketGroup=docker

[Install]
WantedBy=sockets.target
EOF

    # 创建 containerd 服务（docker 依赖）
    cat > /etc/systemd/system/containerd.service <<'EOF'
[Unit]
Description=containerd container runtime
After=network.target local-fs.target

[Service]
ExecStartPre=-/sbin/modprobe overlay
ExecStart=/usr/bin/containerd
Type=notify
Delegate=yes
KillMode=process
Restart=always
RestartSec=5
LimitNPROC=infinity
LimitCORE=infinity
LimitNOFILE=infinity
TasksMax=infinity
OOMScoreAdjust=-999

[Install]
WantedBy=multi-user.target
EOF

    # 创建 docker 配置文件目录
    mkdir -p /etc/docker

    # 默认 daemon.json（可根据需要修改）
    if [[ ! -f /etc/docker/daemon.json ]]; then
        cat > /etc/docker/daemon.json <<'EOF'
{
  "registry-mirrors": [],
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "100m",
    "max-file": "3"
  },
  "storage-driver": "overlay2"
}
EOF
    fi

    # 重载并启动
    systemctl daemon-reload
    systemctl enable docker.service
    systemctl enable containerd.service
    systemctl start containerd
    systemctl start docker

    sleep 2
    if systemctl is-active docker &>/dev/null; then
        log_info "Docker 安装成功!"
        docker --version
    else
        log_error "Docker 启动失败，请检查日志: journalctl -u docker -n 50"
        exit 1
    fi
}

# ---- 安装 docker-compose ----
install_docker_compose() {
    if command -v docker-compose &>/dev/null; then
        log_warn "docker-compose 已安装，跳过: $(docker-compose version --short 2>/dev/null || echo '')"
        return
    fi

    log_info "开始安装 docker-compose..."

    local compose_bin
    compose_bin=$(ls "${COMPOSE_PKG_DIR}"/docker-compose-* | head -1)

    cp "$compose_bin" /usr/local/bin/docker-compose
    chmod +x /usr/local/bin/docker-compose

    # 创建软链接（兼容 v2 的 `docker compose` 子命令）
    if [[ -d /usr/libexec/docker/cli-plugins ]]; then
        mkdir -p /usr/libexec/docker/cli-plugins
        cp "$compose_bin" /usr/libexec/docker/cli-plugins/docker-compose
        chmod +x /usr/libexec/docker/cli-plugins/docker-compose
    fi

    log_info "docker-compose 安装成功!"
    docker-compose version
}

# ---- 验证安装 ----
verify_install() {
    log_info "验证安装结果..."
    echo "----------------------------------------"
    docker --version
    docker-compose version
    echo "----------------------------------------"
    log_info "Docker 状态: $(systemctl is-active docker)"
    log_info "安装目录: /usr/bin/docker, /usr/local/bin/docker-compose"
    log_info "配置文件: /etc/docker/daemon.json"
    log_info "数据目录: /var/lib/docker"
}

# ---- 下载物料（在有网的机器上执行） ----
download_materials() {
    log_info "开始下载安装物料（架构: ${ARCH_TAG}）..."

    mkdir -p "$DOCKER_PKG_DIR" "$COMPOSE_PKG_DIR"

    # 下载 Docker
    # 阿里云镜像路径格式：mirrors.aliyun.com/docker-ce/linux/static/stable/{arch}/docker-{version}.tgz
    DOCKER_URL="${DOCKER_MIRROR}/${ARCH_TAG}/docker-${DOCKER_VERSION}.tgz"
    DOCKER_TGZ="${DOCKER_PKG_DIR}/docker-${DOCKER_VERSION}.tgz"
    if [[ -f "$DOCKER_TGZ" ]]; then
        log_warn "Docker 包已存在，跳过下载: $DOCKER_TGZ"
    else
        log_info "下载 Docker ${DOCKER_VERSION} (${ARCH_TAG})..."
        log_info "URL: $DOCKER_URL"
        wget --no-check-certificate "$DOCKER_URL" -O "$DOCKER_TGZ" || {
            log_error "Docker 下载失败，请手动下载后放入: ${DOCKER_PKG_DIR}/"
            log_info "备用地址: https://download.docker.com/linux/static/stable/${ARCH_TAG}/docker-${DOCKER_VERSION}.tgz"
            exit 1
        }
    fi

    # 下载 docker-compose
    COMPOSE_BIN="${COMPOSE_PKG_DIR}/docker-compose-linux-${ARCH_TAG}"
    COMPOSE_URL="${COMPOSE_MIRROR}/v${DOCKER_COMPOSE_VERSION}/docker-compose-linux-${ARCH_TAG}"
    if [[ -f "$COMPOSE_BIN" ]]; then
        log_warn "docker-compose 已存在，跳过下载: $COMPOSE_BIN"
    else
        log_info "下载 docker-compose v${DOCKER_COMPOSE_VERSION} (${ARCH_TAG})..."
        log_info "URL: $COMPOSE_URL"
        wget --no-check-certificate "$COMPOSE_URL" -O "$COMPOSE_BIN" || {
            log_error "docker-compose 下载失败，请手动下载后放入: ${COMPOSE_PKG_DIR}/"
            log_info "备用地址: https://github.com/docker/compose/releases/download/v${DOCKER_COMPOSE_VERSION}/docker-compose-linux-${ARCH_TAG}"
            exit 1
        }
        chmod +x "$COMPOSE_BIN"
    fi

    log_info "物料下载完成，目录: ${MATERIAL_DIR}"
    ls -lh "$DOCKER_PKG_DIR" "$COMPOSE_PKG_DIR"
}

# ---- 主流程 ----
main() {
    echo "========================================"
    echo "  Docker & Docker-Compose 离线安装脚本"
    echo "  适用: Kylin OS (x86_64 / aarch64)"
    echo "========================================"

    local action="${1:-install}"

    if [[ "$action" == "download" ]]; then
        # 仅下载物料（在有网的机器上执行）
        check_arch
        download_materials
        exit 0
    fi

    # 安装模式
    check_root
    check_arch
    check_materials

    # 卸载旧版本（如果有）
    if [[ "$action" == "reinstall" ]]; then
        log_warn "卸载旧版本..."
        systemctl stop docker 2>/dev/null || true
        rm -f /usr/bin/docker* /usr/local/bin/docker-compose
    fi

    install_docker
    install_docker_compose
    verify_install

    echo ""
    log_info "✅ 安装完成！"
    log_info "常用命令:"
    log_info "  查看状态:  systemctl status docker"
    log_info "  启动 Docker: systemctl start docker"
    log_info "  开机自启:  systemctl enable docker"
}

main "$@"
