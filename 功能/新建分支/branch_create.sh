#!/bin/bash

# 脚本执行规则：
# 1. 按 project.conf 一行一行处理，每行格式：新分支名 项目名
# 2. 空行、以 # 开头的整行注释会被忽略
# 3. 执行前先进行预检查并汇总，确认后才正式执行
# 4. 若项目目录不存在，则自动从 GitLab 拉取项目
# 5. 若目标分支在本地或远程已存在，则记为【跳过】
# 6. 若目标分支不存在，则基于指定基准分支创建并推送，成功记为【成功】
# 7. 除分支已存在之外的其他异常情况，一律记为【失败】

set -u
set -o pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
############################
# 基础配置
############################
GIT_HOST="gitlab.spdb.com"
GROUP="zh-1087"
BASE_DIR="/d/项目"
LOG_DIR="${SCRIPT_DIR}/logs"
DEFAULT_BASE_BRANCH="master"

# SSH clone 地址前缀
GIT_CLONE_PREFIX="git@${GIT_HOST}:${GROUP}"

# 脚本同目录配置文件

CONFIG_FILE="${SCRIPT_DIR}/project.conf"

############################
# 固定项目列表
############################
PROJECTS=(
  "hobo-customer-front"
  "hobo-element-front"
  "hobo-credit-front"
  "hobo-asset-front"
  "hobo-payment-front"
  "hobo-deposit-front"
  "hobo-pub-front"
  "hobo-work-front"
  "hobo-image-component"
  "hobo-factory-front"
  "hobo-flow-orch"
  "hobo-flow-config"
  "hobo-pub-flow"
  "hobo-deposit-flow"
  "hobo-customer-flow"
  "hobo-credit-flow"
  "hobo-element-flow"
  "hobo-asset-flow"
  "hobo-payment-flow"
)

############################
# 初始化
############################
mkdir -p "$BASE_DIR"
mkdir -p "$LOG_DIR"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="${LOG_DIR}/batch_create_branch_by_file_${TIMESTAMP}.log"

SUCCESS_ITEMS=()
SKIPPED_ITEMS=()
FAILED_ITEMS=()

# 预检查阶段保留可执行任务
TASK_LINES=()
TASK_BRANCHES=()
TASK_RAW_PROJECTS=()
TASK_MAPPED_PROJECTS=()

PRECHECK_SKIPPED_ITEMS=()
PRECHECK_FAILED_ITEMS=()

############################
# 日志函数
############################
log() {
  local msg="$1"
  echo "$(date '+%F %T') | $msg" | tee -a "$LOG_FILE"
}

############################
# 去首尾空格
############################
trim() {
  local var="$*"
  var="${var#"${var%%[![:space:]]*}"}"
  var="${var%"${var##*[![:space:]]}"}"
  echo -n "$var"
}

############################
# 标准化项目名
############################
normalize_project_name() {
  local name="$1"
  name="$(trim "$name")"
  name="$(echo "$name" | tr '[:upper:]' '[:lower:]')"
  name="$(echo "$name" | tr -d '[:space:]')"
  name="$(echo "$name" | tr '_' '-')"
  echo "$name"
}

############################
# 判断项目是否存在于标准列表
############################
project_exists() {
  local target="$1"
  local item
  for item in "${PROJECTS[@]}"; do
    if [ "$item" = "$target" ]; then
      return 0
    fi
  done
  return 1
}

############################
# 项目名映射/纠错
############################
map_project_name() {
  local raw="$1"
  local normalized
  local candidate
  local item
  local item_without_prefix
  local normalized_without_prefix

  normalized="$(normalize_project_name "$raw")"

  if project_exists "$normalized"; then
    echo "$normalized"
    return 0
  fi

  if [[ "$normalized" != hobo-* ]]; then
    candidate="hobo-$normalized"
    if project_exists "$candidate"; then
      echo "$candidate"
      return 0
    fi
  fi

  normalized_without_prefix="${normalized#hobo-}"

  for item in "${PROJECTS[@]}"; do
    item_without_prefix="${item#hobo-}"
    if [ "$normalized_without_prefix" = "$item_without_prefix" ]; then
      echo "$item"
      return 0
    fi
  done

  echo ""
  return 1
}

############################
# 询问基准分支
############################
ask_base_branch() {
  local answer
  while true; do
    read -p "默认基准分支为 ${DEFAULT_BASE_BRANCH}，是否使用？(Y/n): " answer
    answer="$(trim "$answer")"
    answer="$(echo "$answer" | tr '[:upper:]' '[:lower:]')"

    case "$answer" in
      ""|"y"|"yes")
        BASE_BRANCH="$DEFAULT_BASE_BRANCH"
        break
        ;;
      "n"|"no")
        read -p "请输入基于哪个分支创建: " BASE_BRANCH
        BASE_BRANCH="$(echo "$BASE_BRANCH" | tr -d '[:space:]')"
        if [ -n "$BASE_BRANCH" ]; then
          break
        else
          echo "错误：基准分支不能为空，请重新输入"
        fi
        ;;
      *)
        echo "请输入 y / yes / n / no，或者直接回车"
        ;;
    esac
  done
}

############################
# 结果记录
############################
record_success() {
  SUCCESS_ITEMS+=("$1")
}

record_skip() {
  SKIPPED_ITEMS+=("$1")
}

record_fail() {
  FAILED_ITEMS+=("$1")
}

############################
# 项目不存在时自动 clone
############################
clone_project_if_missing() {
  local project="$1"
  local project_dir="${BASE_DIR}/${project}"
  local repo_url="${GIT_CLONE_PREFIX}/${project}.git"

  if [ -d "$project_dir" ]; then
    return 0
  fi

  log "[信息] 本地项目不存在，准备拉取: ${project}"
  log "[信息] clone 地址: ${repo_url}"

  cd "$BASE_DIR" || {
    log "[失败] 无法进入 BASE_DIR: ${BASE_DIR}"
    return 1
  }

  if ! git clone "$repo_url" "$project" >>"$LOG_FILE" 2>&1; then
    log "[失败] 项目拉取失败: ${project}"
    return 1
  fi

  log "[成功] 项目拉取成功: ${project}"
  return 0
}

############################
# 预检查
############################
precheck_config() {
  local line_no=0
  local line
  local new_branch
  local raw_project
  local mapped_project

  TASK_LINES=()
  TASK_BRANCHES=()
  TASK_RAW_PROJECTS=()
  TASK_MAPPED_PROJECTS=()
  PRECHECK_SKIPPED_ITEMS=()
  PRECHECK_FAILED_ITEMS=()

  while IFS= read -r line || [ -n "$line" ]; do
    line_no=$((line_no + 1))
    line="$(trim "$line")"

    if [ -z "$line" ]; then
      PRECHECK_SKIPPED_ITEMS+=("第${line_no}行 | 空行")
      continue
    fi

    case "$line" in
      \#*)
        PRECHECK_SKIPPED_ITEMS+=("第${line_no}行 | 注释行 | $line")
        continue
        ;;
    esac

    new_branch="$(echo "$line" | awk '{print $1}')"
    raw_project="$(echo "$line" | awk '{print $2}')"

    new_branch="$(echo "$new_branch" | tr -d '[:space:]')"
    raw_project="$(trim "$raw_project")"

    if [ -z "$new_branch" ] || [ -z "$raw_project" ]; then
      PRECHECK_FAILED_ITEMS+=("第${line_no}行 | 格式错误 | 应为：新分支名 项目名")
      continue
    fi

    mapped_project="$(map_project_name "$raw_project")"

    if [ -z "$mapped_project" ]; then
      PRECHECK_FAILED_ITEMS+=("第${line_no}行 | ${new_branch} | ${raw_project} | 项目名无法识别")
      continue
    fi

    TASK_LINES+=("$line_no")
    TASK_BRANCHES+=("$new_branch")
    TASK_RAW_PROJECTS+=("$raw_project")
    TASK_MAPPED_PROJECTS+=("$mapped_project")
  done < "$CONFIG_FILE"
}

############################
# 打印预检查汇总
############################
print_precheck_summary() {
  local i

  echo
  echo "================ 预检查汇总 ================"
  echo "脚本目录         : $SCRIPT_DIR"
  echo "配置文件         : $CONFIG_FILE"
  echo "基准分支         : $BASE_BRANCH"
  echo "BASE_DIR         : $BASE_DIR"
  echo "克隆地址前缀     : $GIT_CLONE_PREFIX"
  echo

  echo "可执行条数: ${#TASK_LINES[@]}"
  for ((i=0; i<${#TASK_LINES[@]}; i++)); do
    echo "  第${TASK_LINES[$i]}行 | 新分支: ${TASK_BRANCHES[$i]} | 原始项目名: ${TASK_RAW_PROJECTS[$i]} | 映射项目名: ${TASK_MAPPED_PROJECTS[$i]}"
  done

  echo
  echo "预检查跳过条数: ${#PRECHECK_SKIPPED_ITEMS[@]}"
  for item in "${PRECHECK_SKIPPED_ITEMS[@]}"; do
    echo "  [跳过] $item"
  done

  echo
  echo "预检查失败条数: ${#PRECHECK_FAILED_ITEMS[@]}"
  for item in "${PRECHECK_FAILED_ITEMS[@]}"; do
    echo "  [失败] $item"
  done

  echo "==========================================="
  echo
}

############################
# 创建分支核心逻辑
############################
create_branch_for_project() {
  local new_branch="$1"
  local project="$2"
  local raw_project="$3"
  local base_branch="$4"
  local line_no="$5"
  local project_dir="${BASE_DIR}/${project}"

  log "--------------------------------------------------"
  log "开始处理 [第${line_no}行]"
  log "原始项目名: ${raw_project}"
  log "映射后项目名: ${project}"
  log "新分支: ${new_branch}"
  log "项目目录: ${project_dir}"

  if ! clone_project_if_missing "$project"; then
    record_fail "第${line_no}行 | ${new_branch} | ${raw_project} -> ${project} | 项目拉取失败"
    return
  fi

  if [ ! -d "$project_dir" ]; then
    log "[失败] [第${line_no}行] 目录不存在: ${project_dir}"
    record_fail "第${line_no}行 | ${new_branch} | ${raw_project} -> ${project} | 目录不存在"
    return
  fi

  cd "$project_dir" || {
    log "[失败] [第${line_no}行] 无法进入目录: ${project_dir}"
    record_fail "第${line_no}行 | ${new_branch} | ${raw_project} -> ${project} | 无法进入目录"
    return
  }

  if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    log "[失败] [第${line_no}行] 不是 Git 仓库: ${project}"
    record_fail "第${line_no}行 | ${new_branch} | ${raw_project} -> ${project} | 不是Git仓库"
    return
  fi

  if [ -n "$(git status --porcelain)" ]; then
    log "[失败] [第${line_no}行] 工作区有未提交内容: ${project}"
    record_fail "第${line_no}行 | ${new_branch} | ${raw_project} -> ${project} | 工作区不干净"
    return
  fi

  log "[1/7] 获取远程最新信息"
  if ! git fetch origin --prune >>"$LOG_FILE" 2>&1; then
    log "[失败] [第${line_no}行] git fetch 执行失败"
    record_fail "第${line_no}行 | ${new_branch} | ${raw_project} -> ${project} | git fetch失败"
    return
  fi

  log "[2/7] 检查远程基准分支是否存在: origin/${base_branch}"
  if ! git ls-remote --exit-code --heads origin "$base_branch" >/dev/null 2>&1; then
    log "[失败] [第${line_no}行] 远程基准分支不存在: origin/${base_branch}"
    record_fail "第${line_no}行 | ${new_branch} | ${raw_project} -> ${project} | 基准分支不存在"
    return
  fi

  log "[3/7] 切换到基准分支: ${base_branch}"
  if git show-ref --verify --quiet "refs/heads/${base_branch}"; then
    if ! git checkout "$base_branch" >>"$LOG_FILE" 2>&1; then
      log "[失败] [第${line_no}行] 切换本地基准分支失败: ${base_branch}"
      record_fail "第${line_no}行 | ${new_branch} | ${raw_project} -> ${project} | 切换基准分支失败"
      return
    fi
  else
    if ! git checkout -b "$base_branch" "origin/$base_branch" >>"$LOG_FILE" 2>&1; then
      log "[失败] [第${line_no}行] 创建并切换基准分支失败: ${base_branch}"
      record_fail "第${line_no}行 | ${new_branch} | ${raw_project} -> ${project} | 创建基准分支失败"
      return
    fi
  fi

  log "[4/7] 基准分支快进更新到最新"
  if ! git pull --ff-only origin "$base_branch" >>"$LOG_FILE" 2>&1; then
    log "[失败] [第${line_no}行] 基准分支更新失败: ${base_branch}"
    record_fail "第${line_no}行 | ${new_branch} | ${raw_project} -> ${project} | pull失败"
    return
  fi

  log "[5/7] 检查新分支是否已存在: ${new_branch}"

  if git show-ref --verify --quiet "refs/heads/${new_branch}"; then
    log "[跳过] [第${line_no}行] 本地分支已存在: ${new_branch}"
    record_skip "第${line_no}行 | ${new_branch} | ${raw_project} -> ${project} | 本地分支已存在"
    return
  fi

  if git ls-remote --exit-code --heads origin "$new_branch" >/dev/null 2>&1; then
    log "[跳过] [第${line_no}行] 远程分支已存在: origin/${new_branch}"
    record_skip "第${line_no}行 | ${new_branch} | ${raw_project} -> ${project} | 远程分支已存在"
    return
  fi

  log "[6/7] 创建新分支: ${new_branch}"
  if ! git checkout -b "$new_branch" >>"$LOG_FILE" 2>&1; then
    log "[失败] [第${line_no}行] 创建新分支失败: ${new_branch}"
    record_fail "第${line_no}行 | ${new_branch} | ${raw_project} -> ${project} | 创建新分支失败"
    return
  fi

  log "[7/7] 推送新分支到远程: origin/${new_branch}"
  if ! git push -u origin "$new_branch" >>"$LOG_FILE" 2>&1; then
    log "[失败] [第${line_no}行] 推送新分支失败: ${new_branch}"
    record_fail "第${line_no}行 | ${new_branch} | ${raw_project} -> ${project} | 推送失败"
    return
  fi

  log "[成功] [第${line_no}行] ${raw_project} -> ${project} 已创建并推送分支: ${new_branch}"
  record_success "第${line_no}行 | ${new_branch} | ${raw_project} -> ${project}"
}

############################
# 打印支持项目
############################
print_supported_projects() {
  echo
  echo "支持的标准项目如下："
  local item
  for item in "${PROJECTS[@]}"; do
    echo "  - $item"
  done
  echo
}

############################
# 主流程
############################
main() {
  local confirm
  local i
  local item

  print_supported_projects

  if [ ! -f "$CONFIG_FILE" ]; then
    echo "错误：未找到配置文件 -> $CONFIG_FILE"
    exit 1
  fi

  ask_base_branch

  # 预检查
  precheck_config
  print_precheck_summary

  read -p "确认开始执行？(Y/n): " confirm
  confirm="$(trim "$confirm")"
  confirm="$(echo "$confirm" | tr '[:upper:]' '[:lower:]')"

  case "$confirm" in
    ""|"y"|"yes")
      ;;
    *)
      echo "已取消"
      exit 0
      ;;
  esac

  log "========== 按配置文件批量创建分支任务开始 =========="
  log "SCRIPT_DIR=${SCRIPT_DIR}"
  log "CONFIG_FILE=${CONFIG_FILE}"
  log "BASE_BRANCH=${BASE_BRANCH}"
  log "BASE_DIR=${BASE_DIR}"
  log "GIT_CLONE_PREFIX=${GIT_CLONE_PREFIX}"
  log "可执行条数=${#TASK_LINES[@]}"
  log "预检查跳过条数=${#PRECHECK_SKIPPED_ITEMS[@]}"
  log "预检查失败条数=${#PRECHECK_FAILED_ITEMS[@]}"

  for ((i=0; i<${#TASK_LINES[@]}; i++)); do
    create_branch_for_project \
      "${TASK_BRANCHES[$i]}" \
      "${TASK_MAPPED_PROJECTS[$i]}" \
      "${TASK_RAW_PROJECTS[$i]}" \
      "$BASE_BRANCH" \
      "${TASK_LINES[$i]}"
  done

  log "========== 按配置文件批量创建分支任务结束 =========="

  echo
  echo "================ 执行结果 ================"
  echo "实际执行条数: ${#TASK_LINES[@]}"
  echo

  echo "成功条数: ${#SUCCESS_ITEMS[@]}"
  for item in "${SUCCESS_ITEMS[@]}"; do
    echo "  [成功] $item"
  done

  echo
  echo "跳过条数: ${#SKIPPED_ITEMS[@]}"
  for item in "${SKIPPED_ITEMS[@]}"; do
    echo "  [跳过] $item"
  done

  echo
  echo "失败条数: ${#FAILED_ITEMS[@]}"
  for item in "${FAILED_ITEMS[@]}"; do
    echo "  [失败] $item"
  done

  echo
  echo "详细日志: $LOG_FILE"
  echo "========================================="
}

main "$@"
