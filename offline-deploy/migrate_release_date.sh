#!/bin/bash
# ============================================================
# 投产征集日期迁移脚本
# 用法: bash migrate_release_date.sh 2026-05-28 2026-05-29
# 加 --dry-run 只预览不执行
# ============================================================
set -euo pipefail

FROM_DATE="${1:-}"
TO_DATE="${2:-}"
DRY_RUN=false
[[ "${3:-}" == "--dry-run" ]] && DRY_RUN=true

if [[ -z "$FROM_DATE" || -z "$TO_DATE" ]]; then
    echo "用法: bash migrate_release_date.sh <from-date> <to-date> [--dry-run]"
    echo "示例: bash migrate_release_date.sh 2026-05-28 2026-05-29"
    exit 1
fi

# -------- 通过 docker exec 进 MySQL 容器执行 SQL --------
DB_USER="${MYSQL_USER:-devops}"
DB_PASS="${MYSQL_PASSWORD:-}"
DB_NAME="${MYSQL_DATABASE:-devops_platform}"

run_sql() {
    docker exec -i -e MYSQL_PWD="${DB_PASS}" devops-mysql mysql -u"${DB_USER}" "${DB_NAME}" -N -e "$1" 2>&1
}

# ----------------------------------------------------------
echo "============================================="
echo "投产征集日期迁移 ${FROM_DATE} → ${TO_DATE}"
echo "数据库: ${DB_USER}@devops-mysql/${DB_NAME}"
[[ "$DRY_RUN" == true ]] && echo "*** DRY RUN 模式，不会实际修改 ***"
echo "============================================="

# -------- 迁移前检查 --------
echo ""
echo ">>> 迁移前检查"

BATCH_INFO=$(run_sql "SELECT id, release_date, release_type, release_branch, status FROM release_batch WHERE release_date = '${FROM_DATE}'")
if [[ -z "$BATCH_INFO" ]]; then
    echo "错误: ${FROM_DATE} 的批次不存在"
    exit 1
fi
echo "源批次: $BATCH_INFO"

ITEM_COUNT=$(run_sql "SELECT COUNT(*) FROM release_item WHERE batch_id = (SELECT id FROM release_batch WHERE release_date = '${FROM_DATE}')")
echo "关联 item: ${ITEM_COUNT} 条"

DST_INFO=$(run_sql "SELECT id, release_date FROM release_batch WHERE release_date = '${TO_DATE}'")
if [[ -n "$DST_INFO" ]]; then
    DST_ITEM_COUNT=$(run_sql "SELECT COUNT(*) FROM release_item WHERE batch_id = (SELECT id FROM release_batch WHERE release_date = '${TO_DATE}')")
    if [[ "$DST_ITEM_COUNT" != "0" ]]; then
        echo "错误: ${TO_DATE} 已存在批次且包含 ${DST_ITEM_COUNT} 条记录，不允许合并"
        exit 1
    fi
    echo "目标批次已存在但为空，将先删除: $DST_INFO"
fi

# -------- 迁移 --------
echo ""
if [[ "$DRY_RUN" == true ]]; then
    echo ">>> DRY RUN 完成，以上为预览"
    exit 0
fi

echo ">>> 执行迁移"

# 删除空的目标批次（如果存在）
run_sql "DELETE FROM release_batch WHERE release_date = '${TO_DATE}'"

# 更新批次
run_sql "UPDATE release_batch SET release_date = '${TO_DATE}', release_branch = CONCAT(release_type, '-', '$(echo ${TO_DATE} | tr -d -)') WHERE release_date = '${FROM_DATE}'"

# 更新 item 分支名
run_sql "UPDATE release_item SET release_branch = CONCAT((SELECT release_type FROM release_batch WHERE release_date = '${TO_DATE}'), '-', '$(echo ${TO_DATE} | tr -d -)') WHERE batch_id = (SELECT id FROM release_batch WHERE release_date = '${TO_DATE}')"

# -------- 迁移后确认 --------
echo ""
echo ">>> 迁移后确认"

SRC_LEFT=$(run_sql "SELECT COUNT(*) FROM release_batch WHERE release_date = '${FROM_DATE}'")
echo "${FROM_DATE} 剩余批次: ${SRC_LEFT} (应为 0)"

DST_BATCH=$(run_sql "SELECT id, release_date, release_type, release_branch, status FROM release_batch WHERE release_date = '${TO_DATE}'")
echo "${TO_DATE} 批次: $DST_BATCH"

DST_ITEMS=$(run_sql "SELECT COUNT(*) FROM release_item WHERE batch_id = (SELECT id FROM release_batch WHERE release_date = '${TO_DATE}')")
echo "${TO_DATE} item 总数: ${DST_ITEMS} (应为 ${ITEM_COUNT})"

echo ""
echo ">>> 迁移完成"
