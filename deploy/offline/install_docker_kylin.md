# 麒麟 x86_64 安装 Docker 与 Compose（管理员执行）

本文档给出在线与离线两种安装方式。安装完成后，普通用户即可执行离线部署包中的 `deploy.sh`。

## 0. 前置检查

```bash
uname -m
cat /etc/os-release
```

确认架构是 `x86_64`。

## 1) 在线安装（推荐）

> 需要服务器能访问软件仓库。

### Debian/Ubuntu 系（含部分麒麟版本）

```bash
apt-get update
apt-get install -y docker.io docker-compose-plugin
systemctl enable --now docker
docker --version
docker compose version
```

### RHEL/CentOS 系（含部分麒麟版本）

```bash
yum install -y docker docker-compose-plugin || dnf install -y docker docker-compose-plugin
systemctl enable --now docker
docker --version
docker compose version
```

## 2) 离线安装（无外网）

### 2.1 准备离线安装包（在有网机器）

按目标服务器发行版下载以下 rpm/deb 包并带到服务器：

- Docker Engine（`docker-ce` / `docker.io` 对应包）
- containerd
- runc
- Docker Compose 插件（`docker-compose-plugin`）

### 2.2 服务器安装

Debian/Ubuntu 系：

```bash
dpkg -i ./*.deb
apt-get -f install -y
```

RHEL/CentOS 系：

```bash
rpm -Uvh ./*.rpm --nodeps
# 如有依赖冲突，请补齐缺失依赖后重试
```

启动与验证：

```bash
systemctl daemon-reload
systemctl enable --now docker
docker --version
docker compose version
```

## 3. 赋权给普通用户

```bash
groupadd docker 2>/dev/null || true
usermod -aG docker <your_user>
```

然后让该普通用户重新登录（或执行 `newgrp docker`）后再部署。

## 4. 验证普通用户可用

切换到普通用户后执行：

```bash
docker ps
docker compose version
```

若均正常，即可按离线包文档执行部署。
