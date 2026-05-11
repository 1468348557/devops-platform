#!/bin/bash
# 轻量级定时任务调度器 —— 每分钟触发 run_branch_schedules 和 run_export_schedules
set -e

MANAGE="python /app/myproject/manage.py"

while true; do
    sleep 60
    echo "[scheduler] $(date '+%Y-%m-%d %H:%M:%S') tick"
    $MANAGE run_branch_schedules --due 2>&1 || true
    $MANAGE run_export_schedules --due 2>&1 || true
done
