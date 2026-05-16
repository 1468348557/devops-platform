#!/usr/bin/env bash
# 启动本地 MySQL（Docker 容器）
# 容器名: devops-mysql  镜像: mysql:8.4  端口: 3306

set -e

CONTAINER="devops-mysql"

if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    echo "MySQL 已在运行"
    exit 0
fi

if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
    echo "启动已有容器 $CONTAINER ..."
    docker start "$CONTAINER"
else
    echo "容器不存在，创建并启动..."
    docker run -d \
        --name "$CONTAINER" \
        -p 3306:3306 \
        -e MYSQL_ROOT_PASSWORD=root123456 \
        -e MYSQL_DATABASE=devops_platform \
        -e MYSQL_USER=devops \
        -e MYSQL_PASSWORD=devops123 \
        -v devops-platform_mysql_data:/var/lib/mysql \
        mysql:8.4
fi

until docker exec "$CONTAINER" mysqladmin ping -h localhost --silent 2>/dev/null; do
    echo "等待 MySQL 就绪..."
    sleep 1
done

echo "MySQL 已就绪 (127.0.0.1:3306)"
