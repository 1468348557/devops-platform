# DevOps Platform 离线部署说明（麒麟 x86_64）

本文档对应 `docker-compose` 离线部署方式，适用于服务器无法访问外网的场景。

## 1. 本地打包（有 Docker 的机器）

在项目根目录执行：

```bash
chmod +x scripts/build_offline_bundle.sh
./scripts/build_offline_bundle.sh
```

成功后会输出一个归档文件路径，例如：

```text
dist/devops-platform-offline-20260422-153000.tar.gz
```

## 2. 上传到服务器

将 `tar.gz` 包上传到麒麟服务器任意目录，例如：

```bash
scp dist/devops-platform-offline-*.tar.gz user@server:/home/user/
```

## 3. 服务器一次性安装 Docker（root）

先让管理员执行 [`install_docker_kylin.md`](./install_docker_kylin.md) 中的步骤安装 Docker Engine + Compose。

## 4. 普通用户部署

```bash
cd /home/user
tar -xzf devops-platform-offline-*.tar.gz
cd devops-platform-offline-*
cp compose/.env.deploy.example compose/.env
vi compose/.env
chmod +x deploy/deploy.sh deploy/check.sh
./deploy/deploy.sh
```

> 首次执行 `deploy.sh` 时，如果 `compose/.env` 不存在会自动生成并退出，提示你先修改变量。
> 如需导入初始化数据，将 `.sql` 文件放到离线包的 `sql/` 目录下，部署脚本会在数据库迁移完成后按文件名顺序导入。

## 5. 必填变量说明

编辑 `compose/.env` 时，至少要替换以下值：

- `DJANGO_SECRET_KEY`
- `MYSQL_ROOT_PASSWORD`
- `MYSQL_PASSWORD`
- `MYSQL_DATA_DIR`（默认 `/docker/devops/mysql/data`，首次初始化时目录必须为空）
- `DJANGO_ALLOWED_HOSTS`（按实际域名/IP 配置）

## 6. 部署后检查

```bash
./deploy/check.sh
```

或手动检查：

```bash
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
curl -I http://127.0.0.1:${WEB_PORT:-8000}
```

## 7. 常见问题

- `Cannot connect to the Docker daemon`：当前用户没有 Docker 权限，重新登录或检查是否已加入 `docker` 组。
- MySQL 一直不健康：检查 `compose/.env` 密码配置及 `docker logs devops-mysql`。
- Web 启动失败：查看 `docker logs devops-web`，重点检查数据库连通和 `DJANGO_ALLOWED_HOSTS`。
