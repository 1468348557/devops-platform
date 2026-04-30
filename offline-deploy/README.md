# DevOps Platform - x86_64 离线部署指南

这个目录用于在本地打包 `linux/amd64` 镜像，然后把一个压缩包上传到 x86_64 服务器执行一键部署。适合本地是 Apple Silicon、目标服务器是 CentOS / Linux x86_64 的场景。

## 一、本地打包

本地需要能访问外网或镜像源，并已启动 Docker Desktop / Docker Engine。

```bash
cd offline-deploy
bash package-x86_64.sh
```

脚本会自动完成：

1. 初始化 Docker Buildx 跨架构构建环境。
2. 构建 `linux/amd64` 的 Web 镜像 `devops-platform-web:latest`。
3. 拉取 `linux/amd64` 的 MySQL 镜像 `mysql:8.4`。
4. 导出两个镜像到 `images/`。
5. 生成 `../dist/offline/devops-platform-x86_64-YYYYMMDD-HHMMSS.tar.gz`。

如果有私有仓库镜像源，可以这样执行：

```bash
PRIVATE_REGISTRY=registry.example.com bash package-x86_64.sh
```

## 二、上传到服务器

```bash
scp ../dist/offline/devops-platform-x86_64-*.tar.gz root@server-ip:/opt/
```

## 三、服务器一键部署

服务器上需要提前安装 Docker 和 Docker Compose 插件。部署包解压后会包含固定的 `offline-deploy/` 目录。

```bash
ssh root@server-ip
cd /opt
tar -xzf devops-platform-x86_64-*.tar.gz
cd offline-deploy
bash deploy.sh
```

`deploy.sh` 会自动检测环境、生成 `.env` 和随机密码、加载离线镜像、创建 MySQL 数据目录、执行 Django `migrate`、创建或更新超管账号、导入 `sql/*.sql`（如果存在）、收集静态文件并启动服务。

默认超管用户名是 `admin`，密码会随机生成并打印在部署日志里，同时写入服务器上的 `.env`。请保存好 `.env`，后续重启会继续使用同一套密码。

## 四、服务管理

```bash
cd /opt/offline-deploy

docker compose ps
docker compose logs -f web
docker compose logs -f mysql
docker compose restart
docker compose down
```

进入容器：

```bash
docker compose exec web bash
docker compose exec mysql mysql -uroot -p
```

## 五、常见问题

- `exec format error`：镜像架构不匹配，重新在本地执行 `bash package-x86_64.sh`，确认脚本输出架构为 `linux/amd64`。
- MySQL 启动超时：检查 `.env` 中的 `MYSQL_DATA_DIR` 是否可写，默认目录是 `/docker/devops/mysql/data`。
- 端口被占用：修改服务器 `.env` 中的 `WEB_PORT` 或 `MYSQL_PORT` 后重新执行 `bash deploy.sh`。
- 需要导入数据：把 `.sql` 文件放到 `offline-deploy/sql/` 后执行 `bash deploy.sh`，脚本会自动导入。
