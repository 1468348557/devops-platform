#!/bin/bash
set -e

# docker compose run web python manage.py migrate 这类命令会作为参数传入。
# 有参数时直接执行参数，避免被默认的 gunicorn 前台进程卡住。
if [ "$#" -gt 0 ]; then
    exec "$@"
fi

# 配置 git 全局用户信息
if [ -n "$GIT_USER_NAME" ] && [ -n "$GIT_USER_EMAIL" ]; then
    echo "Configuring git global user..."
    git config --global user.name "$GIT_USER_NAME"
    git config --global user.email "$GIT_USER_EMAIL"
fi

# 收集静态文件
echo "Collecting static files..."
python manage.py collectstatic --noinput --verbosity=1

# 启动定时任务后台调度器（每分钟执行一次）
echo "Starting scheduler..."
bash /app/scheduler.sh &

# 启动 gunicorn
echo "Starting gunicorn..."
exec gunicorn myproject.wsgi:application \
    --bind "0.0.0.0:8000" \
    --workers 3 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -
