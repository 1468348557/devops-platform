#!/bin/bash
# 时钟调度器 —— 每分钟触发所有已启用的 cron 调度（分支创建 + 定时导出）
set -e

MANAGE="python /app/myproject/manage.py"

while true; do
    sleep 60
    echo "[scheduler] $(date '+%Y-%m-%d %H:%M:%S') tick"
    $MANAGE clock_tick 2>&1 || true
done
