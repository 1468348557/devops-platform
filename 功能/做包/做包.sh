#!/usr/bin/env bash
set -euo pipefail

trap 'echo "[ERROR] 执行失败，行号: $LINENO，命令: $BASH_COMMAND" >&2' ERR

die() { echo "[ERROR] $*" >&2; exit 1; }
log() { echo "[INFO] $*"; }
warn() { echo "[WARN] $*" >&2; }
need_cmd() { command -v "$1" >/dev/null 2>&1 || die "缺少命令：$1"; }

normalize_date() {
  local d="$1"
  d="${d//-/}"
  [[ "$d" =~ ^[0-9]{8}$ ]] || die "日期格式不对：$1（期望 YYYYMMDD 或 YYYY-MM-DD）"
  echo "$d"
}

confirm() {
  local prompt="$1"
  read -r -p "$prompt [y/N]: " ans
  [[ "${ans,,}" == "y" || "${ans,,}" == "yes" ]]
}

trim() {
  local var="$*"
  var="${var#"${var%%[![:space:]]*}"}"
  var="${var%"${var##*[![:space:]]}"}"
  echo -n "$var"
}

normalize_project_name() {
  local name="$1"
  name="$(trim "$name")"
  name="$(echo "$name" | tr '[:upper:]' '[:lower:]')"
  name="$(echo "$name" | tr -d '[:space:]')"
  name="$(echo "$name" | tr '_' '-')"
  echo "$name"
}

to_lower() {
  echo "$1" | tr '[:upper:]' '[:lower:]'
}

contains_any_keyword() {
  local text="$1"
  shift
  local text_lc
  local kw
  text_lc="$(to_lower "$text")"
  for kw in "$@"; do
    kw="$(trim "$kw")"
    [[ -z "$kw" ]] && continue
    kw="$(to_lower "$kw")"
    if [[ "$text_lc" == *"$kw"* ]]; then
      return 0
    fi
  done
  return 1
}

convert_to_utf8() {
  local file="$1"
  local tmp="${file}.utf8tmp"

  if iconv -f UTF-8 -t UTF-8 "$file" -o "$tmp" 2>/dev/null; then
    mv -f -- "$tmp" "$file"
    return 0
  fi

  if iconv -f GBK -t UTF-8 "$file" -o "$tmp" 2>/dev/null; then
    mv -f -- "$tmp" "$file"
    return 0
  fi

  if iconv -f GB18030 -t UTF-8 "$file" -o "$tmp" 2>/dev/null; then
    mv -f -- "$tmp" "$file"
    return 0
  fi

  cp -f -- "$file" "$tmp"
  mv -f -- "$tmp" "$file"
}

ensure_use_hobo_flow() {
  local file="$1"
  local tmp="${file}.tmp"
  local first_non_empty

  first_non_empty="$(grep -im1 '^[[:space:]]*[^[:space:]].*$' "$file" || true)"
  if echo "$first_non_empty" | grep -iqE '^[[:space:]]*use[[:space:]]+hobo_flow;?[[:space:]]*$'; then
    return 0
  fi

  {
    printf 'use hobo_flow;\n'
    cat "$file"
  } > "$tmp"
  mv -f -- "$tmp" "$file"
}

is_empty_sql() {
  local file="$1"
  [[ ! -s "$file" ]]
}

sort_sql_names_with_ddl_first() {
  while IFS= read -r name; do
    [[ -z "$name" ]] && continue
    if [[ "$(to_lower "$name")" == *ddl* ]]; then
      printf '0|%s\n' "$name"
    else
      printf '1|%s\n' "$name"
    fi
  done | sort -t'|' -k1,1 -k2,2V | cut -d'|' -f2-
}

write_sql_order_file() {
  local dir="$1"
  : > "${dir}/order.txt"

  local names=()
  local f
  while IFS= read -r -d '' f; do
    names+=("$(basename "$f")")
  done < <(find "$dir" -maxdepth 1 -type f \( -iname "*.sql" -o -iname "*.SQL" \) -print0)

  if (( ${#names[@]} == 0 )); then
    return 0
  fi

  printf '%s\n' "${names[@]}" | sort_sql_names_with_ddl_first >> "${dir}/order.txt"
}

prepare_sql_target_dirs() {
  mkdir -p "$SQL_EXEC_DIR" "$SQL_BAK_DIR" "$SQL_ROLLBACK_DIR"
  find "$SQL_EXEC_DIR" -maxdepth 1 -type f -delete || true
  find "$SQL_BAK_DIR" -maxdepth 1 -type f -delete || true
  find "$SQL_ROLLBACK_DIR" -maxdepth 1 -type f -delete || true
}

post_process_sql_file() {
  local file="$1"
  local add_use="$2"

  convert_to_utf8 "$file"
  if [[ "$add_use" == "true" ]]; then
    ensure_use_hobo_flow "$file"
  fi
}

collect_sql_basenames_in_dir() {
  local dir="$1"
  local tmpfile
  local f

  tmpfile="$(mktemp)"
  while IFS= read -r -d '' f; do
    basename "$f"
  done < <(find "$dir" -maxdepth 1 -type f \( -iname "*.sql" -o -iname "*.SQL" \) -print0) > "$tmpfile"

  if [[ -s "$tmpfile" ]]; then
    sort_sql_names_with_ddl_first < "$tmpfile"
  fi

  rm -f -- "$tmpfile"
}

############################################################
# 一、读取配置文件
############################################################

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${SCRIPT_DIR}/做包.conf"

[[ -f "$CONFIG_FILE" ]] || die "配置文件不存在：$CONFIG_FILE"

# shellcheck source=/dev/null
source "$CONFIG_FILE"

############################################################
# 二、校验配置
############################################################

: "${RELEASE_DATE:?配置项 RELEASE_DATE 未设置}"
: "${VERSION_TAG:?配置项 VERSION_TAG 未设置}"
: "${CHANGE_NO:?配置项 CHANGE_NO 未设置}"
: "${VERSION_NO:?配置项 VERSION_NO 未设置}"
: "${WORK_ROOT:?配置项 WORK_ROOT 未设置}"
: "${REPO_SSH:?配置项 REPO_SSH 未设置}"
: "${BRANCH:?配置项 BRANCH 未设置}"
: "${TEMPLATE_DIR:?配置项 TEMPLATE_DIR 未设置}"
: "${CHANGE_XLS:?配置项 CHANGE_XLS 未设置}"
: "${DOCKER_MAIN_DIRNAME:?配置项 DOCKER_MAIN_DIRNAME 未设置}"
: "${DB_REPO_SSH:?配置项 DB_REPO_SSH 未设置}"
: "${DB_BRANCH:?配置项 DB_BRANCH 未设置}"
: "${COMMIT_MSG:=}"
: "${TARGET_DIR:=}"
: "${SQL_DATE_DIR:=}"

if [[ ${#PROJECTS_INPUT[@]} -eq 0 ]]; then
  die "配置项 PROJECTS_INPUT 未设置或为空"
fi

if [[ ${#DOCKER_CLONE_DIRS[@]} -eq 0 ]]; then
  die "配置项 DOCKER_CLONE_DIRS 未设置或为空"
fi

if [[ ${#EXEC_SQL_KEYWORDS[@]} -eq 0 ]]; then
  die "配置项 EXEC_SQL_KEYWORDS 未设置或为空"
fi

if [[ ${#BACKUP_SQL_KEYWORDS[@]} -eq 0 ]]; then
  die "配置项 BACKUP_SQL_KEYWORDS 未设置或为空"
fi

if [[ ${#ROLLBACK_SQL_KEYWORDS[@]} -eq 0 ]]; then
  die "配置项 ROLLBACK_SQL_KEYWORDS 未设置或为空"
fi

############################################################
# 三、固定标准项目列表
############################################################

STANDARD_PROJECTS=(
hobo-customer-front
hobo-element-front
hobo-credit-front
hobo-asset-front
hobo-payment-front
hobo-deposit-front
hobo-pub-front
hobo-work-front
hobo-image-component
hobo-factory-front
hobo-flow-orch
hobo-flow-config
hobo-pub-flow
hobo-deposit-flow
hobo-customer-flow
hobo-credit-flow
hobo-element-flow
hobo-asset-flow
hobo-payment-flow
)

############################################################
# 四、项目名映射
############################################################

standard_project_exists() {
  local target="$1"
  local item
  for item in "${STANDARD_PROJECTS[@]}"; do
    if [[ "$item" == "$target" ]]; then
      return 0
    fi
  done
  return 1
}

map_project_name() {
  local raw="$1"
  local normalized
  local candidate
  local item
  local item_without_prefix
  local normalized_without_prefix

  normalized="$(normalize_project_name "$raw")"

  if standard_project_exists "$normalized"; then
    echo "$normalized"
    return 0
  fi

  if [[ "$normalized" != hobo-* ]]; then
    candidate="hobo-$normalized"
    if standard_project_exists "$candidate"; then
      echo "$candidate"
      return 0
    fi
  fi

  normalized_without_prefix="${normalized#hobo-}"
  for item in "${STANDARD_PROJECTS[@]}"; do
    item_without_prefix="${item#hobo-}"
    if [[ "$normalized_without_prefix" == "$item_without_prefix" ]]; then
      echo "$item"
      return 0
    fi
  done

  echo ""
  return 1
}

############################################################
# 五、标准化项目列表
############################################################

RAW_PROJECTS_TEXT="$(printf '%s, ' "${PROJECTS_INPUT[@]}")"
RAW_PROJECTS_TEXT="${RAW_PROJECTS_TEXT%, }"

declare -a FINAL_PROJECTS=()
declare -a FINAL_PROJECT_KEYS=()
declare -a INVALID_PROJECTS=()
declare -A SEEN_PROJECTS=()

for raw_proj in "${PROJECTS_INPUT[@]}"; do
  raw_proj="$(trim "$raw_proj")"
  [[ -z "$raw_proj" ]] && continue

  mapped_proj="$(map_project_name "$raw_proj" || true)"
  if [[ -z "$mapped_proj" ]]; then
    INVALID_PROJECTS+=("$raw_proj")
    continue
  fi

  if [[ -z "${SEEN_PROJECTS[$mapped_proj]+x}" ]]; then
    FINAL_PROJECTS+=("$mapped_proj")
    FINAL_PROJECT_KEYS+=("${mapped_proj#hobo-}")
    SEEN_PROJECTS["$mapped_proj"]=1
  fi
done

(( ${#FINAL_PROJECTS[@]} > 0 )) || die "PROJECTS_INPUT 解析后没有有效项目，请检查配置"

############################################################
# 六、计算路径和变量
############################################################

DATE8="$(normalize_date "$RELEASE_DATE")"
YEAR="${DATE8:0:4}"
MMDD="${DATE8:4:4}"

[[ "$VERSION_TAG" == "v1" || "$VERSION_TAG" == "v2" ]] || die "VERSION_TAG 只允许 v1 或 v2"
[[ -n "$CHANGE_NO" ]] || die "CHANGE_NO 不能为空"
[[ -n "$VERSION_NO" ]] || die "VERSION_NO 不能为空"
[[ "$VERSION_NO" != *" "* ]] || die "VERSION_NO 不能包含空格"

if [[ -z "$SQL_DATE_DIR" ]]; then
  SQL_DATE_DIR="$(date +%Y%m%d)"
else
  SQL_DATE_DIR="$(normalize_date "$SQL_DATE_DIR")"
fi

IMAGE_NAME="$VERSION_NO"
AMP_TITLE="HOBO_FLOW_${DATE8}_${VERSION_TAG}"
PACKAGE_NAME="${AMP_TITLE}.tar"

if [[ -z "${COMMIT_MSG}" ]]; then
  COMMIT_MSG="自动化投产包整理：${CHANGE_NO} ${DATE8} ${VERSION_TAG} image=${IMAGE_NAME}"
fi

REPO_DIR="${WORK_ROOT}/hobo-doc"
DB_REPO_DIR="${WORK_ROOT}/hobo-database"

if [[ "${VERSION_TAG}" == "v1" ]]; then
  BASE_DIR="${REPO_DIR}/投产材料/${YEAR}年/${MMDD}_日常变更_自动化"
else
  BASE_DIR="${REPO_DIR}/投产材料/${YEAR}年/${MMDD}_日常变更_自动化_v2"
fi

if [[ -z "${TARGET_DIR}" ]]; then
  TARGET_DIR="${BASE_DIR}/HOBO_FLOW_${DATE8}_${VERSION_TAG}"
fi

DOCKER_MAIN="${TARGET_DIR}/${DOCKER_MAIN_DIRNAME}"
CHANGE_XLS_TARGET="${BASE_DIR}/变更管理_后台智能工厂_${CHANGE_NO}_hobo_flow_${DATE8}_${VERSION_TAG}.xls"

SQL_SRC_DIR="${DB_REPO_DIR}/${SQL_DATE_DIR}"
SQL_EXEC_DIR="${TARGET_DIR}/goldendb"
SQL_BAK_DIR="${TARGET_DIR}/goldendbbak"
SQL_ROLLBACK_DIR="${TARGET_DIR}/手动"

############################################################
# 七、打印汇总并确认
############################################################

print_config_summary() {
  echo "=================================================="
  echo "发布配置确认"
  echo "=================================================="
  echo "配置文件路径     : ${CONFIG_FILE}"
  echo "发布日期         : ${DATE8}"
  echo "版本标签         : ${VERSION_TAG}"
  echo "变更号           : ${CHANGE_NO}"
  echo "版本号           : ${VERSION_NO}"
  echo "镜像名           : ${IMAGE_NAME}"
  echo "包名             : ${PACKAGE_NAME}"
  echo "WORK_ROOT        : ${WORK_ROOT}"
  echo "REPO_SSH         : ${REPO_SSH}"
  echo "BRANCH           : ${BRANCH}"
  echo "REPO_DIR         : ${REPO_DIR}"
  echo "DB_REPO_SSH      : ${DB_REPO_SSH}"
  echo "DB_BRANCH        : ${DB_BRANCH}"
  echo "DB_REPO_DIR      : ${DB_REPO_DIR}"
  echo "SQL 日期目录     : ${SQL_DATE_DIR}"
  echo "SQL 扫描范围     : ${SQL_SRC_DIR}"
  echo "模板目录         : ${TEMPLATE_DIR}"
  echo "模板 Excel       : ${CHANGE_XLS}"
  echo "基础目录         : ${BASE_DIR}"
  echo "目标目录         : ${TARGET_DIR}"
  echo "生成 Excel       : ${CHANGE_XLS_TARGET}"
  echo "docker 主目录名  : ${DOCKER_MAIN_DIRNAME}"
  echo "docker 复制目录  : ${DOCKER_CLONE_DIRS[*]}"
  echo "SQL 执行目录     : ${SQL_EXEC_DIR}"
  echo "SQL 备份目录     : ${SQL_BAK_DIR}"
  echo "SQL 回滚目录     : ${SQL_ROLLBACK_DIR}"
  echo "执行关键字       : ${EXEC_SQL_KEYWORDS[*]}"
  echo "备份关键字       : ${BACKUP_SQL_KEYWORDS[*]}"
  echo "回滚关键字       : ${ROLLBACK_SQL_KEYWORDS[*]}"
  echo "提交信息         : ${COMMIT_MSG}"
  echo "-----------------------------------------------"
  echo "原始项目输入     : ${RAW_PROJECTS_TEXT}"
  echo "标准化后项目列表 :"
  local i
  for i in "${!FINAL_PROJECTS[@]}"; do
    echo "  - ${FINAL_PROJECTS[$i]}  (匹配关键字: ${FINAL_PROJECT_KEYS[$i]})"
  done

  if (( ${#INVALID_PROJECTS[@]} > 0 )); then
    echo "未匹配项目列表   :"
    for i in "${INVALID_PROJECTS[@]}"; do
      echo "  - ${i}"
    done
  else
    echo "未匹配项目列表   : 无"
  fi
  echo "=================================================="
}

print_config_summary

if (( ${#INVALID_PROJECTS[@]} > 0 )); then
  die "存在未匹配的项目名称，请修正配置文件后再执行"
fi

if ! confirm "请确认以上配置和项目映射是否正确，是否继续执行？"; then
  die "用户取消执行"
fi

############################################################
# 八、高风险确认：是否自动 push
############################################################

AUTO_PUSH_TO_REMOTE=false

if confirm "【高风险确认】是否允许本次脚本执行结束后自动提交并 push 到远端 Git？"; then
  AUTO_PUSH_TO_REMOTE=true
  log "已确认：脚本结束后将自动 git add / commit / push"
else
  log "未确认：脚本结束后将跳过 git add / commit / push"
fi

############################################################
# 九、依赖检查
############################################################

need_cmd git
need_cmd find
need_cmd sed
need_cmd sort
need_cmd cp
need_cmd rm
need_cmd mkdir
need_cmd powershell.exe
need_cmd cygpath
need_cmd tr
need_cmd wc
need_cmd grep
need_cmd mv
need_cmd iconv
need_cmd date
need_cmd basename
need_cmd dirname
need_cmd cut
need_cmd mktemp

mkdir -p "$WORK_ROOT"

############################################################
# 十、克隆 / 更新仓库
############################################################

if [[ ! -d "$REPO_DIR/.git" ]]; then
  log "克隆仓库：$REPO_SSH"
  git clone "$REPO_SSH" "$REPO_DIR"
else
  log "仓库已存在，拉取更新"
  git -C "$REPO_DIR" fetch --all --prune
fi

if [[ ! -d "$DB_REPO_DIR/.git" ]]; then
  log "克隆数据库仓库：$DB_REPO_SSH"
  git clone "$DB_REPO_SSH" "$DB_REPO_DIR"
else
  log "数据库仓库已存在，拉取更新"
  git -C "$DB_REPO_DIR" fetch --all --prune
fi

if ! git -C "$REPO_DIR" diff --quiet || ! git -C "$REPO_DIR" diff --cached --quiet; then
  die "仓库 ${REPO_DIR} 存在未提交修改，请先处理后再执行脚本"
fi

if ! git -C "$DB_REPO_DIR" diff --quiet || ! git -C "$DB_REPO_DIR" diff --cached --quiet; then
  die "仓库 ${DB_REPO_DIR} 存在未提交修改，请先处理后再执行脚本"
fi

git -C "$REPO_DIR" checkout "$BRANCH"
git -C "$REPO_DIR" pull --ff-only

git -C "$DB_REPO_DIR" checkout "$DB_BRANCH"
git -C "$DB_REPO_DIR" pull --ff-only

############################################################
# 十一、路径校验
############################################################

[[ -d "$TEMPLATE_DIR" ]] || die "模板目录不存在：$TEMPLATE_DIR"
[[ -d "${TEMPLATE_DIR}/${DOCKER_MAIN_DIRNAME}" ]] || die "模板中缺少目录：${TEMPLATE_DIR}/${DOCKER_MAIN_DIRNAME}"
[[ -f "$CHANGE_XLS" ]] || die "模板表格不存在：$CHANGE_XLS"
[[ -d "$SQL_SRC_DIR" ]] || die "SQL 日期目录不存在：$SQL_SRC_DIR"

[[ "$BASE_DIR" == "${REPO_DIR}"/* ]] || die "BASE_DIR 不在仓库目录下，拒绝操作：$BASE_DIR"
[[ "$TARGET_DIR" == "${REPO_DIR}"/* ]] || die "TARGET_DIR 不在仓库目录下，拒绝操作：$TARGET_DIR"
[[ "$(basename "$TARGET_DIR")" == HOBO_FLOW_* ]] || die "TARGET_DIR 目录名异常，拒绝操作：$TARGET_DIR"

############################################################
# 十二、复制 Excel
############################################################

mkdir -p "$BASE_DIR"
log "复制模板表格并重命名 -> ${CHANGE_XLS_TARGET}"
cp -f -- "$CHANGE_XLS" "$CHANGE_XLS_TARGET"

############################################################
# 十三、修改 Excel
############################################################

log "修改 Excel：基础信息 sheet（B1 / B3 / A6）"

CHANGE_XLS_TARGET_WIN="$(cygpath -w "$CHANGE_XLS_TARGET")"

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "
\$ErrorActionPreference = 'Stop'

\$excel = New-Object -ComObject Excel.Application
\$excel.Visible = \$false
\$excel.DisplayAlerts = \$false
\$wb = \$null
\$ws = \$null

try {
    \$wb = \$excel.Workbooks.Open('${CHANGE_XLS_TARGET_WIN}')
    \$ws = \$wb.Worksheets.Item('基础信息')

    \$ws.Range('B1').Value2 = '${CHANGE_NO}'
    \$ws.Range('B3').Value2 = '${VERSION_NO}'
    \$ws.Range('A6').Value2 = '${PACKAGE_NAME}'

    \$wb.Save()
    \$wb.Close(\$false)
}
finally {
    if (\$ws -ne \$null) { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject(\$ws) }
    if (\$wb -ne \$null) { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject(\$wb) }
    if (\$excel -ne \$null) {
        \$excel.Quit()
        [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject(\$excel)
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
"

############################################################
# 十四、复制模板目录
############################################################

if [[ -d "$TARGET_DIR" ]]; then
  log "目标目录已存在，先删除：$TARGET_DIR"
  rm -rf -- "$TARGET_DIR"
fi

mkdir -p "$TARGET_DIR"
cp -a -- "${TEMPLATE_DIR}/." "$TARGET_DIR/"

############################################################
# 十五、清理 docker 主目录
############################################################

[[ -d "$DOCKER_MAIN" ]] || die "docker 主目录不存在：$DOCKER_MAIN"

log "清理 ${DOCKER_MAIN}: 仅保留 order.txt + 配置项目对应文件（其它文件删除）"
touch "${DOCKER_MAIN}/order.txt"

shopt -s nullglob
for p in "${DOCKER_MAIN}/"*; do
  [[ -f "$p" ]] || continue
  base="$(basename "$p")"
  base_lc="$(to_lower "$base")"

  [[ "$base" == "order.txt" ]] && continue

  keep=false
  for proj_key in "${FINAL_PROJECT_KEYS[@]}"; do
    [[ -z "$proj_key" ]] && continue
    if [[ "$base_lc" == *"$proj_key"* ]]; then
      keep=true
      break
    fi
  done

  [[ "$keep" == false ]] && rm -f -- "$p"
done
shopt -u nullglob

############################################################
# 十六、替换 json image
############################################################

log "替换 ${DOCKER_MAIN} 下所有 .json 的 image 为：${IMAGE_NAME}"
find "$DOCKER_MAIN" -type f -name "*.json" -print0 | while IFS= read -r -d '' f; do
  sed -i -E 's/"image"[[:space:]]*:[[:space:]]*"[^"]*"/"image":"'"${IMAGE_NAME//\//\\/}"'"/g' "$f"
done

############################################################
# 十七、生成 docker order.txt
############################################################

log "生成 docker order.txt（按 dev -> test -> prod 顺序写入该目录下 .json 文件名）"
: > "${DOCKER_MAIN}/order.txt"

for prefix in dev_ test_ prod_; do
  find "$DOCKER_MAIN" -maxdepth 1 -type f -name "${prefix}*.json" -print0 \
    | while IFS= read -r -d '' f; do
        basename "$f"
      done \
    | sort >> "${DOCKER_MAIN}/order.txt"
done

json_count="$(find "$DOCKER_MAIN" -maxdepth 1 -type f -name "*.json" | wc -l | tr -d ' ')"
order_count="$(wc -l < "${DOCKER_MAIN}/order.txt" | tr -d ' ')"

log "${DOCKER_MAIN} 下 json 文件数：${json_count}"
log "${DOCKER_MAIN}/order.txt 行数：${order_count}"

if [[ "${json_count}" == "0" ]]; then
  warn "未匹配到任何 json 文件，请检查 PROJECTS_INPUT 与模板文件名是否对应"
fi

############################################################
# 十八、处理数据库 SQL 脚本
############################################################

log "开始处理数据库 SQL 脚本，来源目录：${SQL_SRC_DIR}"
log "说明：处理过程中不会输出 SQL 文件内容，只输出文件路径、分类、编号和统计信息"

prepare_sql_target_dirs

UNMATCHED_SQL_REPORT="${TARGET_DIR}/未匹配SQL清单.txt"
EMPTY_SQL_REPORT="${TARGET_DIR}/空SQL清单.txt"
: > "$UNMATCHED_SQL_REPORT"
: > "$EMPTY_SQL_REPORT"

declare -a UNMATCHED_SQL_FILES=()
declare -a EMPTY_SQL_FILES=()
declare -a SQL_PARENT_DIRS=()
declare -A SQL_PARENT_SEEN=()
declare -A DIR_INDEX_MAP=()

sql_total=0
sql_skip_count=0
sql_empty_count=0
sql_exec_count=0
sql_bak_count=0
sql_rollback_count=0

while IFS= read -r -d '' sql_file; do
  base="$(basename "$sql_file")"
  rel_path="${sql_file#${SQL_SRC_DIR}/}"
  parent_rel="$(dirname "$rel_path")"
  [[ "$parent_rel" == "." ]] && parent_rel="__ROOT__"

  sql_total=$((sql_total + 1))

  if is_empty_sql "$sql_file"; then
    EMPTY_SQL_FILES+=("$rel_path")
    sql_empty_count=$((sql_empty_count + 1))
  fi

  if contains_any_keyword "$base" "${EXEC_SQL_KEYWORDS[@]}"; then
    :
  elif contains_any_keyword "$base" "${BACKUP_SQL_KEYWORDS[@]}"; then
    :
  elif contains_any_keyword "$base" "${ROLLBACK_SQL_KEYWORDS[@]}"; then
    :
  else
    UNMATCHED_SQL_FILES+=("$rel_path")
    sql_skip_count=$((sql_skip_count + 1))
    continue
  fi

  if [[ -z "${SQL_PARENT_SEEN[$parent_rel]+x}" ]]; then
    SQL_PARENT_DIRS+=("$parent_rel")
    SQL_PARENT_SEEN["$parent_rel"]=1
  fi
done < <(find "$SQL_SRC_DIR" -type f \( -iname "*.sql" -o -iname "*.SQL" \) -print0)

if (( ${#EMPTY_SQL_FILES[@]} > 0 )); then
  {
    echo "以下 SQL 文件为空文件："
    printf '%s\n' "${EMPTY_SQL_FILES[@]}" | sort
  } > "$EMPTY_SQL_REPORT"

  warn "检测到空 SQL 文件，流程继续。清单：${EMPTY_SQL_REPORT}"
  while IFS= read -r line; do
    echo "  - ${line}"
  done < <(printf '%s\n' "${EMPTY_SQL_FILES[@]}" | sort)
fi

if (( ${#UNMATCHED_SQL_FILES[@]} > 0 )); then
  {
    echo "以下 SQL 文件未匹配到任何分类关键字："
    printf '%s\n' "${UNMATCHED_SQL_FILES[@]}" | sort
  } > "$UNMATCHED_SQL_REPORT"

  log "检测到未匹配分类的 SQL 文件，已生成清单：${UNMATCHED_SQL_REPORT}"
  while IFS= read -r line; do
    echo "  - ${line}"
  done < <(printf '%s\n' "${UNMATCHED_SQL_FILES[@]}" | sort)

  die "存在未匹配分类的 SQL 文件，请修正后重试"
fi

if (( sql_total == 0 )); then
  touch "${SQL_EXEC_DIR}/order.txt" "${SQL_BAK_DIR}/order.txt" "${SQL_ROLLBACK_DIR}/order.txt"
  log "SQL 日期目录下未找到任何 .sql 文件"
else
  idx=1
  while IFS= read -r dir_name; do
    DIR_INDEX_MAP["$dir_name"]="$idx"
    idx=$((idx + 1))
  done < <(printf '%s\n' "${SQL_PARENT_DIRS[@]}" | sort)

  log "SQL 来源目录编号映射如下："
  while IFS= read -r dir_name; do
    if [[ "$dir_name" == "__ROOT__" ]]; then
      echo "  ${DIR_INDEX_MAP[$dir_name]} -> /"
    else
      echo "  ${DIR_INDEX_MAP[$dir_name]} -> ${dir_name}"
    fi
  done < <(printf '%s\n' "${SQL_PARENT_DIRS[@]}" | sort)

  while IFS= read -r parent_rel; do
    dir_index="${DIR_INDEX_MAP[$parent_rel]}"

    if [[ "$parent_rel" == "__ROOT__" ]]; then
      src_parent_dir="${SQL_SRC_DIR}"
      parent_show="/"
    else
      src_parent_dir="${SQL_SRC_DIR}/${parent_rel}"
      parent_show="${parent_rel}"
    fi

    mapfile -t dir_sql_files < <(collect_sql_basenames_in_dir "$src_parent_dir")

    for base in "${dir_sql_files[@]}"; do
      src_file="${src_parent_dir}/${base}"
      dst_dir=""
      add_use="false"
      category_label=""

      if contains_any_keyword "$base" "${EXEC_SQL_KEYWORDS[@]}"; then
        dst_dir="$SQL_EXEC_DIR"
        add_use="true"
        category_label="执行"
        sql_exec_count=$((sql_exec_count + 1))
      elif contains_any_keyword "$base" "${BACKUP_SQL_KEYWORDS[@]}"; then
        dst_dir="$SQL_BAK_DIR"
        category_label="备份"
        sql_bak_count=$((sql_bak_count + 1))
      elif contains_any_keyword "$base" "${ROLLBACK_SQL_KEYWORDS[@]}"; then
        dst_dir="$SQL_ROLLBACK_DIR"
        category_label="回滚"
        sql_rollback_count=$((sql_rollback_count + 1))
      else
        die "内部错误：文件二次分类失败：${src_file}"
      fi

      dst_file="${dst_dir}/${dir_index}_${base}"
      [[ ! -e "$dst_file" ]] || die "目标文件重名冲突：${dst_file}"

      cp -f -- "$src_file" "$dst_file"
      post_process_sql_file "$dst_file" "$add_use"

      log "SQL分类: [${category_label}] [编号=${dir_index}] ${parent_show}/${base} -> $(basename "$dst_file")"
    done
  done < <(printf '%s\n' "${SQL_PARENT_DIRS[@]}" | sort)

  write_sql_order_file "$SQL_EXEC_DIR"
  write_sql_order_file "$SQL_BAK_DIR"
  write_sql_order_file "$SQL_ROLLBACK_DIR"
fi

log "SQL 脚本处理完成：总数=${sql_total}，执行=${sql_exec_count}，备份=${sql_bak_count}，回滚=${sql_rollback_count}，空文件=${sql_empty_count}，未分类=${sql_skip_count}"
log "已生成：${SQL_EXEC_DIR}/order.txt"
log "已生成：${SQL_BAK_DIR}/order.txt"
log "已生成：${SQL_ROLLBACK_DIR}/order.txt"

############################################################
# 十九、复制 docker 目录
############################################################

for d in "${DOCKER_CLONE_DIRS[@]}"; do
  dst="${TARGET_DIR}/${d}"
  log "复制 ${DOCKER_MAIN_DIRNAME} -> ${d}"
  rm -rf -- "$dst"
  mkdir -p "$dst"
  cp -a -- "${DOCKER_MAIN}/." "$dst/"
done

############################################################
# 二十、git 状态
############################################################

log "查看 git 状态："
git -C "$REPO_DIR" config core.quotepath false
git -C "$REPO_DIR" status -sb

############################################################
# 二十一、commit / push
############################################################

if [[ "$AUTO_PUSH_TO_REMOTE" == true ]]; then
  log "开始执行 git add / commit"
  git -C "$REPO_DIR" add -A

  if git -C "$REPO_DIR" diff --cached --quiet; then
    log "暂存区无变更，跳过 commit/push"
  else
    git -C "$REPO_DIR" commit -m "$COMMIT_MSG"

    if confirm "即将执行 git push 到远端分支 ${BRANCH}，是否继续？"; then
      git -C "$REPO_DIR" push
      log "push 完成"
    else
      log "用户取消 push，已完成 commit，但未推送远端"
    fi
  fi
else
  log "已跳过 git add / commit / push"
fi

log "全部完成：${TARGET_DIR}"
log "Excel 已生成并更新：${CHANGE_XLS_TARGET}"

if [[ -s "$EMPTY_SQL_REPORT" ]]; then
  warn "本次检测到空 SQL 文件，请关注清单：${EMPTY_SQL_REPORT}"
fi
