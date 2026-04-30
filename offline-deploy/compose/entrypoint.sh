#!/bin/bash
set -e

# docker compose run web python manage.py migrate 这类命令会作为参数传入。
# 有参数时直接执行参数，避免被默认的 gunicorn 前台进程卡住。
if [ "$#" -gt 0 ]; then
    exec "$@"
fi

# 收集静态文件
echo "Collecting static files..."
python manage.py collectstatic --noinput --verbosity=1

# 启动 gunicorn
echo "Starting gunicorn..."
exec gunicorn myproject.wsgi:application \
    --bind "0.0.0.0:8000" \
    --workers 3 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -
