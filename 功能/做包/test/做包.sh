#!/usr/bin/env bash
set -euo pipefail

trap 'echo "[ERROR] 执行失败，行号: $LINENO，命令: $BASH_COMMAND" >&2' ERR

die() { echo "[ERROR] $*" >&2; exit 1; }
log() { echo "[INFO] $*"; }
need_cmd() { command -v "$1" >/dev/null 2>&1 || die "缺少命令：$1"; }

normalize_date() {
  local d="$1"
  d="${d//-/}"
  [[ "$d" =~ ^[0-9]{8}$ ]] || die "RELEASE_DATE 格式不对：$1（期望 YYYYMMDD 或 YYYY-MM-DD）"
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
: "${COMMIT_MSG:=}"
: "${TARGET_DIR:=}"

if [[ ${#PROJECTS_INPUT[@]} -eq 0 ]]; then
  die "配置项 PROJECTS_INPUT 未设置或为空"
fi

if [[ ${#DOCKER_CLONE_DIRS[@]} -eq 0 ]]; then
  die "配置项 DOCKER_CLONE_DIRS 未设置或为空"
fi

############################################################
# 三、固定标准项目列表（固定逻辑保留在脚本）
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

IMAGE_NAME="$VERSION_NO"
AMP_TITLE="HOBO_FLOW_${DATE8}_${VERSION_TAG}"
PACKAGE_NAME="${AMP_TITLE}.tar"

if [[ -z "${COMMIT_MSG}" ]]; then
  COMMIT_MSG="自动化投产包整理：${CHANGE_NO} ${DATE8} ${VERSION_TAG} image=${IMAGE_NAME}"
fi

REPO_DIR="${WORK_ROOT}/hobo-doc"

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
  echo "模板目录         : ${TEMPLATE_DIR}"
  echo "模板 Excel       : ${CHANGE_XLS}"
  echo "基础目录         : ${BASE_DIR}"
  echo "目标目录         : ${TARGET_DIR}"
  echo "生成 Excel       : ${CHANGE_XLS_TARGET}"
  echo "docker 主目录名  : ${DOCKER_MAIN_DIRNAME}"
  echo "docker 复制目录  : ${DOCKER_CLONE_DIRS[*]}"
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

if ! git -C "$REPO_DIR" diff --quiet || ! git -C "$REPO_DIR" diff --cached --quiet; then
  die "仓库存在未提交修改，请先处理后再执行脚本"
fi

git -C "$REPO_DIR" checkout "$BRANCH"

if ! git -C "$REPO_DIR" pull --ff-only; then
  log "警告：git pull --ff-only 失败，继续使用当前本地分支内容"
fi

############################################################
# 十一、路径校验
############################################################

[[ -d "$TEMPLATE_DIR" ]] || die "模板目录不存在：$TEMPLATE_DIR"
[[ -d "${TEMPLATE_DIR}/${DOCKER_MAIN_DIRNAME}" ]] || die "模板中缺少目录：${TEMPLATE_DIR}/${DOCKER_MAIN_DIRNAME}"
[[ -f "$CHANGE_XLS" ]] || die "模板表格不存在：$CHANGE_XLS"

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
  base_lc="$(echo "$base" | tr '[:upper:]' '[:lower:]')"

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
# 十七、生成 order.txt
############################################################

log "生成 order.txt（按 dev -> test -> prod 顺序写入该目录下 .json 文件名）"
: > "${DOCKER_MAIN}/order.txt"

for prefix in dev_ test_ prod_; do
  find "$DOCKER_MAIN" -maxdepth 1 -type f -name "${prefix}*.json" -printf "%f\n" | sort >> "${DOCKER_MAIN}/order.txt"
done

json_count="$(find "$DOCKER_MAIN" -maxdepth 1 -type f -name "*.json" | wc -l | tr -d ' ')"
order_count="$(wc -l < "${DOCKER_MAIN}/order.txt" | tr -d ' ')"

log "${DOCKER_MAIN} 下 json 文件数：${json_count}"
log "${DOCKER_MAIN}/order.txt 行数：${order_count}"

if [[ "${json_count}" == "0" ]]; then
  log "警告：未匹配到任何 json 文件，请检查 PROJECTS_INPUT 与模板文件名是否对应"
fi

############################################################
# 十八、复制 docker 目录
############################################################

for d in "${DOCKER_CLONE_DIRS[@]}"; do
  dst="${TARGET_DIR}/${d}"
  log "复制 ${DOCKER_MAIN_DIRNAME} -> ${d}"
  rm -rf -- "$dst"
  mkdir -p "$dst"
  cp -a -- "${DOCKER_MAIN}/." "$dst/"
done

############################################################
# 十九、git 状态
############################################################

log "查看 git 状态："
git -C "$REPO_DIR" config core.quotepath false
git -C "$REPO_DIR" status -sb

############################################################
# 二十、commit / push
############################################################

if [[ "$AUTO_PUSH_TO_REMOTE" == true ]]; then
  log "开始执行 git add / commit / push"
  git -C "$REPO_DIR" add -A

  if git -C "$REPO_DIR" diff --cached --quiet; then
    log "暂存区无变更，跳过 commit/push"
  else
    git -C "$REPO_DIR" commit -m "$COMMIT_MSG"
    git -C "$REPO_DIR" push
    log "push 完成"
  fi
else
  log "已跳过 git add / commit / push"
fi

log "全部完成：${TARGET_DIR}"
log "Excel 已生成并更新：${CHANGE_XLS_TARGET}"
