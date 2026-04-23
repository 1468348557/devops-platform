#!/usr/bin/env bash
set -euo pipefail

########################################
# 基础配置
########################################

GIT_HOST="gitlab.spdb.com"
GROUP="zh-1087"

BASE_DIR="/d/项目"
CONFIG_FILE="./repos.conf"

# 与手工验证成功命令保持一致：http + 项目路径 + --form
GITLAB_API="http://${GIT_HOST}/api/v4"
GITLAB_TOKEN="${GITLAB_TOKEN:-你的token}"

# 如果远端 tag 已存在，是否强制覆盖
FORCE_TAG="false"

# 创建 MR 后是否自动 merge
AUTO_MERGE_MR="true"

########################################
# 初始化
########################################

mkdir -p "$BASE_DIR"
cd "$BASE_DIR"

REPOS=()
COMMENTED_REPOS=()

READY_REPOS=()
MERGED_REPOS=()
SUCCESS_REPOS=()
SKIPPED_REPOS=()
FAILED_REPOS=()
MR_CREATED_REPOS=()
TAG_SUCCESS_REPOS=()

declare -A REPO_RELEASE_BRANCH=()
declare -A REPO_TARGET_BRANCH=()
declare -A REPO_COMMENTED=()

declare -A REPO_MERGE_HEAD=()
declare -A REPO_PENDING_COUNT=()
declare -A REPO_PENDING_LOG=()
declare -A REPO_STATUS=()
declare -A REPO_REASON=()

declare -A REPO_MR_URL=()
declare -A REPO_MR_IID=()
declare -A REPO_MR_STATE=()
declare -A REPO_TAG_RESULT=()

TAG_NAME=""
MERGE_MESSAGE=""
TAG_MESSAGE=""

########################################
# 工具函数
########################################

print_sep() {
  echo
  echo "=================================================="
  echo "$1"
  echo "=================================================="
}

array_contains() {
  local seeking="$1"
  shift
  local item
  for item in "$@"; do
    [[ "$item" == "$seeking" ]] && return 0
  done
  return 1
}

append_unique() {
  local value="$1"
  local array_name="$2"

  eval "local current=(\"\${${array_name}[@]}\")"
  if ! array_contains "$value" "${current[@]}"; then
    eval "${array_name}+=(\"\$value\")"
  fi
}

remove_from_array() {
  local value="$1"
  local array_name="$2"
  eval "local current=(\"\${${array_name}[@]}\")"

  local new_array=()
  local item
  for item in "${current[@]}"; do
    if [[ "$item" != "$value" ]]; then
      new_array+=("$item")
    fi
  done

  eval "${array_name}=()"
  local item2
  for item2 in "${new_array[@]}"; do
    eval "${array_name}+=(\"\$item2\")"
  done
}

mark_ready() {
  local repo="$1"
  local reason="${2:-本地预合并成功}"
  append_unique "$repo" "READY_REPOS"
  remove_from_array "$repo" "FAILED_REPOS"
  remove_from_array "$repo" "SKIPPED_REPOS"
  REPO_STATUS["$repo"]="READY"
  REPO_REASON["$repo"]="$reason"
}

mark_merged() {
  local repo="$1"
  local reason="${2:-MR 已成功合并}"
  append_unique "$repo" "MERGED_REPOS"
  remove_from_array "$repo" "FAILED_REPOS"
  remove_from_array "$repo" "SKIPPED_REPOS"
  remove_from_array "$repo" "READY_REPOS"
  REPO_STATUS["$repo"]="MERGED"
  REPO_REASON["$repo"]="$reason"
}

mark_success() {
  local repo="$1"
  local reason="${2:-已成功完成}"
  append_unique "$repo" "SUCCESS_REPOS"
  remove_from_array "$repo" "FAILED_REPOS"
  remove_from_array "$repo" "SKIPPED_REPOS"
  REPO_STATUS["$repo"]="SUCCESS"
  REPO_REASON["$repo"]="$reason"
}

mark_skipped() {
  local repo="$1"
  local reason="${2:-已跳过}"
  append_unique "$repo" "SKIPPED_REPOS"
  remove_from_array "$repo" "FAILED_REPOS"
  remove_from_array "$repo" "READY_REPOS"
  REPO_STATUS["$repo"]="SKIPPED"
  REPO_REASON["$repo"]="$reason"
}

mark_failed() {
  local repo="$1"
  local reason="${2:-执行失败}"
  append_unique "$repo" "FAILED_REPOS"
  remove_from_array "$repo" "READY_REPOS"
  REPO_STATUS["$repo"]="FAILED"
  REPO_REASON["$repo"]="$reason"
}

ensure_clean_worktree() {
  if [[ -n "$(git status --porcelain)" ]]; then
    echo "当前仓库存在未提交改动，请先清理后再执行："
    git status --short
    return 1
  fi
  return 0
}

rollback_merge_if_needed() {
  if git rev-parse -q --verify MERGE_HEAD >/dev/null 2>&1; then
    echo "检测到未完成 merge，执行 git merge --abort"
    git merge --abort || true
  fi
}

collect_repo_summary() {
  local repo="$1"
  local release_branch="$2"
  local target_branch="$3"

  REPO_MERGE_HEAD["$repo"]="$(git log -1 --oneline || true)"

  local pending_log
  pending_log="$(git log --oneline "origin/${target_branch}..${release_branch}" || true)"
  REPO_PENDING_LOG["$repo"]="$pending_log"

  local pending_count
  if [[ -n "$pending_log" ]]; then
    pending_count="$(printf '%s\n' "$pending_log" | sed '/^$/d' | wc -l | tr -d ' ')"
  else
    pending_count="0"
  fi
  REPO_PENDING_COUNT["$repo"]="$pending_count"
}

show_repo_summary() {
  local repo="$1"
  local release_branch="$2"
  local target_branch="$3"

  echo
  echo "仓库: $repo"
  echo "投产分支: $release_branch"
  echo "目标分支: $target_branch"
  echo "Merge Message: $MERGE_MESSAGE"
  echo "Tag Name: $TAG_NAME"
  echo "Tag Message: $TAG_MESSAGE"
  echo

  echo "[1] 当前分支："
  git branch --show-current

  echo
  echo "[2] 当前最新提交："
  echo "${REPO_MERGE_HEAD[$repo]}"

  echo
  echo "[3] 待合并提交数量："
  echo "${REPO_PENDING_COUNT[$repo]}"

  echo
  echo "[4] 本次从 ${release_branch} 合入 ${target_branch} 的提交列表："
  if [[ -n "${REPO_PENDING_LOG[$repo]}" ]]; then
    printf '%s\n' "${REPO_PENDING_LOG[$repo]}"
  else
    echo "无"
  fi

  echo
  echo "[5] 当前工作区状态："
  git status --short
  echo
}

print_stage_summary() {
  print_sep "本地预合并结果汇总"

  echo "[待创建 MR 的仓库]"
  if [[ ${#READY_REPOS[@]} -eq 0 ]]; then
    echo "无"
  else
    local repo
    for repo in "${READY_REPOS[@]}"; do
      echo " - $repo"
      echo "   投产分支: ${REPO_RELEASE_BRANCH[$repo]}"
      echo "   目标分支: ${REPO_TARGET_BRANCH[$repo]}"
      echo "   状态: ${REPO_STATUS[$repo]}"
      echo "   原因: ${REPO_REASON[$repo]}"
      echo "   最新提交: ${REPO_MERGE_HEAD[$repo]}"
      echo "   待合并提交数量: ${REPO_PENDING_COUNT[$repo]}"
    done
  fi

  echo
  echo "[配置中已注释跳过的仓库]"
  if [[ ${#COMMENTED_REPOS[@]} -eq 0 ]]; then
    echo "无"
  else
    local repo
    for repo in "${COMMENTED_REPOS[@]}"; do
      echo " - $repo"
      echo "   投产分支: ${REPO_RELEASE_BRANCH[$repo]}"
      echo "   目标分支: ${REPO_TARGET_BRANCH[$repo]}"
    done
  fi

  echo
  echo "[运行中跳过仓库]"
  if [[ ${#SKIPPED_REPOS[@]} -eq 0 ]]; then
    echo "无"
  else
    local repo
    for repo in "${SKIPPED_REPOS[@]}"; do
      echo " - $repo"
      echo "   投产分支: ${REPO_RELEASE_BRANCH[$repo]:-未配置}"
      echo "   目标分支: ${REPO_TARGET_BRANCH[$repo]:-未配置}"
      echo "   原因: ${REPO_REASON[$repo]}"
    done
  fi

  echo
  echo "[失败仓库]"
  if [[ ${#FAILED_REPOS[@]} -eq 0 ]]; then
    echo "无"
  else
    local repo
    for repo in "${FAILED_REPOS[@]}"; do
      echo " - $repo"
      echo "   投产分支: ${REPO_RELEASE_BRANCH[$repo]:-未配置}"
      echo "   目标分支: ${REPO_TARGET_BRANCH[$repo]:-未配置}"
      echo "   原因: ${REPO_REASON[$repo]}"
    done
  fi
}

print_mr_merge_summary() {
  print_sep "MR 创建 / 合并结果汇总"

  echo "[MR 已合并成功，待确认打 tag 的仓库]"
  if [[ ${#MERGED_REPOS[@]} -eq 0 ]]; then
    echo "无"
  else
    local repo
    for repo in "${MERGED_REPOS[@]}"; do
      echo " - $repo"
      echo "   投产分支: ${REPO_RELEASE_BRANCH[$repo]}"
      echo "   目标分支: ${REPO_TARGET_BRANCH[$repo]}"
      echo "   原因: ${REPO_REASON[$repo]}"
      echo "   MR链接: ${REPO_MR_URL[$repo]:-无}"
      echo "   MR IID: ${REPO_MR_IID[$repo]:-无}"
    done
  fi

  echo
  echo "[新创建 MR 的仓库]"
  if [[ ${#MR_CREATED_REPOS[@]} -eq 0 ]]; then
    echo "无"
  else
    local repo
    for repo in "${MR_CREATED_REPOS[@]}"; do
      echo " - $repo"
      echo "   MR链接: ${REPO_MR_URL[$repo]:-无}"
      echo "   MR IID: ${REPO_MR_IID[$repo]:-无}"
    done
  fi

  echo
  echo "[MR 失败仓库（不会打 tag）]"
  if [[ ${#FAILED_REPOS[@]} -eq 0 ]]; then
    echo "无"
  else
    local repo
    for repo in "${FAILED_REPOS[@]}"; do
      echo " - $repo"
      echo "   原因: ${REPO_REASON[$repo]}"
      if [[ -n "${REPO_MR_URL[$repo]:-}" ]]; then
        echo "   MR链接: ${REPO_MR_URL[$repo]}"
      fi
    done
  fi
}

print_tag_summary() {
  print_sep "Tag 结果汇总"

  echo "[Tag 成功仓库]"
  if [[ ${#TAG_SUCCESS_REPOS[@]} -eq 0 ]]; then
    echo "无"
  else
    local repo
    for repo in "${TAG_SUCCESS_REPOS[@]}"; do
      echo " - $repo"
      echo "   Tag: $TAG_NAME"
      echo "   结果: ${REPO_TAG_RESULT[$repo]}"
    done
  fi

  echo
  echo "[Tag 失败/跳过仓库]"
  local has_tag_issue="false"
  local repo
  for repo in "${MERGED_REPOS[@]}"; do
    if ! array_contains "$repo" "${TAG_SUCCESS_REPOS[@]}"; then
      has_tag_issue="true"
      echo " - $repo"
      echo "   Tag: $TAG_NAME"
      echo "   结果: ${REPO_TAG_RESULT[$repo]:-${REPO_REASON[$repo]:-未执行}}"
    fi
  done

  if [[ "$has_tag_issue" != "true" ]]; then
    echo "无"
  fi
}

print_final_summary() {
  print_sep "执行结果汇总"

  echo "[成功仓库]"
  if [[ ${#SUCCESS_REPOS[@]} -eq 0 ]]; then
    echo "无"
  else
    local repo
    for repo in "${SUCCESS_REPOS[@]}"; do
      echo " - $repo"
      echo "   投产分支: ${REPO_RELEASE_BRANCH[$repo]}"
      echo "   目标分支: ${REPO_TARGET_BRANCH[$repo]}"
      echo "   原因: ${REPO_REASON[$repo]}"
      if [[ -n "${REPO_MR_URL[$repo]:-}" ]]; then
        echo "   MR链接: ${REPO_MR_URL[$repo]}"
      fi
      if [[ -n "${REPO_TAG_RESULT[$repo]:-}" ]]; then
        echo "   Tag结果: ${REPO_TAG_RESULT[$repo]}"
      fi
    done
  fi

  echo
  echo "[配置中已注释跳过的仓库]"
  if [[ ${#COMMENTED_REPOS[@]} -eq 0 ]]; then
    echo "无"
  else
    local repo
    for repo in "${COMMENTED_REPOS[@]}"; do
      echo " - $repo"
      echo "   投产分支: ${REPO_RELEASE_BRANCH[$repo]}"
      echo "   目标分支: ${REPO_TARGET_BRANCH[$repo]}"
    done
  fi

  echo
  echo "[运行中跳过仓库]"
  if [[ ${#SKIPPED_REPOS[@]} -eq 0 ]]; then
    echo "无"
  else
    local repo
    for repo in "${SKIPPED_REPOS[@]}"; do
      echo " - $repo"
      echo "   投产分支: ${REPO_RELEASE_BRANCH[$repo]:-未配置}"
      echo "   目标分支: ${REPO_TARGET_BRANCH[$repo]:-未配置}"
      echo "   原因: ${REPO_REASON[$repo]}"
    done
  fi

  echo
  echo "[失败仓库]"
  if [[ ${#FAILED_REPOS[@]} -eq 0 ]]; then
    echo "无"
  else
    local repo
    for repo in "${FAILED_REPOS[@]}"; do
      echo " - $repo"
      echo "   投产分支: ${REPO_RELEASE_BRANCH[$repo]:-未配置}"
      echo "   目标分支: ${REPO_TARGET_BRANCH[$repo]:-未配置}"
      echo "   原因: ${REPO_REASON[$repo]}"
      if [[ -n "${REPO_MR_URL[$repo]:-}" ]]; then
        echo "   MR链接: ${REPO_MR_URL[$repo]}"
      fi
    done
  fi

  echo
  echo "全部处理完成。"
}

urlencode_project_path() {
  local input="$1"
  echo "${input//\//%2F}"
}

json_get_string() {
  local json="$1"
  local key="$2"
  printf '%s\n' "$json" | grep -o "\"${key}\":\"[^\"]*\"" | head -n1 | sed "s/\"${key}\":\"//;s/\"$//"
}

json_get_number() {
  local json="$1"
  local key="$2"
  printf '%s\n' "$json" | grep -o "\"${key}\":[0-9][0-9]*" | head -n1 | sed "s/\"${key}\"://"
}

create_merge_request() {
  local repo="$1"
  local source_branch="$2"
  local target_branch="$3"

  local encoded_project
  local mr_title
  local mr_description
  local resp
  local rc

  encoded_project="$(urlencode_project_path "${GROUP}/${repo}")"

  # 尽量保持和手工成功命令同风格，但替换成你定义的信息
  mr_title="${TAG_NAME}-${repo}"
  mr_description="repo=${repo}; source=${source_branch}; target=${target_branch}; tag=${TAG_NAME}; merge=${MERGE_MESSAGE}; tagmsg=${TAG_MESSAGE}"

  set +e
  resp="$(curl --silent --show-error --location \
      --request POST \
      --header "PRIVATE-TOKEN: ${GITLAB_TOKEN}" \
      --form "source_branch=${source_branch}" \
      --form "target_branch=${target_branch}" \
      --form "title=${mr_title}" \
      --form "description=${mr_description}" \
      "${GITLAB_API}/projects/${encoded_project}/merge_requests" 2>&1)"
  rc=$?
  set -e

  echo "$resp"
  return $rc
}

merge_merge_request() {
  local repo="$1"
  local mr_iid="$2"
  local merge_message="$3"

  local encoded_project
  local resp
  local rc

  encoded_project="$(urlencode_project_path "${GROUP}/${repo}")"

  set +e
  resp="$(curl --silent --show-error --location \
      --request PUT \
      --header "PRIVATE-TOKEN: ${GITLAB_TOKEN}" \
      --form "merge_commit_message=${merge_message}" \
      "${GITLAB_API}/projects/${encoded_project}/merge_requests/${mr_iid}/merge" 2>&1)"
  rc=$?
  set -e

  echo "$resp"
  return $rc
}

load_config() {
  if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "配置文件不存在: $CONFIG_FILE"
    exit 1
  fi

  local line
  local raw_line
  local line_no=0

  while IFS= read -r raw_line || [[ -n "$raw_line" ]]; do
    line_no=$((line_no + 1))
    line="$(echo "$raw_line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"

    [[ -z "$line" ]] && continue

    if [[ "$line" =~ ^# ]]; then
      line="${line#\#}"
      line="$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"

      [[ -z "$line" ]] && continue

      if [[ "$line" =~ ^TAG_NAME= ]] || [[ "$line" =~ ^MERGE_MESSAGE= ]] || [[ "$line" =~ ^TAG_MESSAGE= ]]; then
        continue
      fi
      if [[ "$line" =~ ^repo_name\| ]]; then
        continue
      fi

      IFS='|' read -r repo release_branch target_branch <<< "$line"
      repo="$(echo "${repo:-}" | xargs)"
      release_branch="$(echo "${release_branch:-}" | xargs)"
      target_branch="$(echo "${target_branch:-}" | xargs)"

      if [[ -n "$repo" && -n "$release_branch" && -n "$target_branch" ]]; then
        REPO_RELEASE_BRANCH["$repo"]="$release_branch"
        REPO_TARGET_BRANCH["$repo"]="$target_branch"
        REPO_COMMENTED["$repo"]="Y"
        append_unique "$repo" "COMMENTED_REPOS"
      fi
      continue
    fi

    if [[ "$line" =~ ^TAG_NAME= ]]; then
      TAG_NAME="${line#TAG_NAME=}"
      TAG_NAME="$(echo "$TAG_NAME" | xargs)"
      continue
    fi

    if [[ "$line" =~ ^MERGE_MESSAGE= ]]; then
      MERGE_MESSAGE="${line#MERGE_MESSAGE=}"
      MERGE_MESSAGE="$(echo "$MERGE_MESSAGE" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
      continue
    fi

    if [[ "$line" =~ ^TAG_MESSAGE= ]]; then
      TAG_MESSAGE="${line#TAG_MESSAGE=}"
      TAG_MESSAGE="$(echo "$TAG_MESSAGE" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
      continue
    fi

    if [[ "$line" =~ ^repo_name\| ]]; then
      continue
    fi

    IFS='|' read -r repo release_branch target_branch <<< "$line"

    repo="$(echo "${repo:-}" | xargs)"
    release_branch="$(echo "${release_branch:-}" | xargs)"
    target_branch="$(echo "${target_branch:-}" | xargs)"

    if [[ -z "$repo" ]]; then
      echo "配置文件第 ${line_no} 行格式错误：repo 不能为空"
      exit 1
    fi

    if [[ -z "$release_branch" ]]; then
      echo "配置文件第 ${line_no} 行格式错误：release_branch 不能为空"
      exit 1
    fi

    if [[ -z "$target_branch" ]]; then
      echo "配置文件第 ${line_no} 行格式错误：target_branch 不能为空"
      exit 1
    fi

    REPOS+=("$repo")
    REPO_RELEASE_BRANCH["$repo"]="$release_branch"
    REPO_TARGET_BRANCH["$repo"]="$target_branch"
    REPO_COMMENTED["$repo"]="N"
  done < "$CONFIG_FILE"

  if [[ -z "$TAG_NAME" ]]; then
    echo "配置文件缺少 TAG_NAME"
    exit 1
  fi

  if [[ -z "$MERGE_MESSAGE" ]]; then
    echo "配置文件缺少 MERGE_MESSAGE"
    exit 1
  fi

  if [[ -z "$TAG_MESSAGE" ]]; then
    echo "配置文件缺少 TAG_MESSAGE"
    exit 1
  fi

  if [[ ${#REPOS[@]} -eq 0 ]]; then
    echo "配置文件没有可执行的仓库: $CONFIG_FILE"
    exit 1
  fi
}

trap 'echo; echo "收到中断信号，脚本终止。"; exit 1' INT TERM

########################################
# 读取配置
########################################

load_config

########################################
# 启动确认
########################################

print_sep "投产追版脚本启动"
echo "工作目录: $BASE_DIR"
echo "配置文件: $CONFIG_FILE"
echo "Tag 名称: $TAG_NAME"
echo "Merge 信息: $MERGE_MESSAGE"
echo "Tag 信息: $TAG_MESSAGE"
echo "FORCE_TAG: $FORCE_TAG"
echo "AUTO_MERGE_MR: $AUTO_MERGE_MR"
echo "GitLab API: $GITLAB_API"
echo

if [[ -z "$GITLAB_TOKEN" || "$GITLAB_TOKEN" == "你的token" ]]; then
  echo "请先把脚本里的 GITLAB_TOKEN 改成真实 token"
  exit 1
fi

echo "本次执行仓库："
for repo in "${REPOS[@]}"; do
  echo " - $repo"
  echo "   投产分支: ${REPO_RELEASE_BRANCH[$repo]}"
  echo "   目标分支: ${REPO_TARGET_BRANCH[$repo]}"
done

echo
echo "配置中注释掉的仓库："
if [[ ${#COMMENTED_REPOS[@]} -eq 0 ]]; then
  echo "无"
else
  for repo in "${COMMENTED_REPOS[@]}"; do
    echo " - $repo"
    echo "   投产分支: ${REPO_RELEASE_BRANCH[$repo]}"
    echo "   目标分支: ${REPO_TARGET_BRANCH[$repo]}"
  done
fi
echo

read -r -p "输入 yes 开始执行本地预合并检查: " start_confirm
if [[ "$start_confirm" != "yes" ]]; then
  echo "已取消执行。"
  exit 0
fi

########################################
# 第一阶段：本地预合并检查
########################################

for repo in "${REPOS[@]}"; do
  local_release_branch="${REPO_RELEASE_BRANCH[$repo]}"
  local_target_branch="${REPO_TARGET_BRANCH[$repo]}"

  print_sep "处理仓库: $repo"
  echo "投产分支: $local_release_branch"
  echo "目标分支: $local_target_branch"

  url="git@${GIT_HOST}:${GROUP}/${repo}.git"

  if [[ ! -d "${repo}/.git" ]]; then
    echo "本地不存在仓库，开始克隆..."
    if ! git clone "$url"; then
      echo "克隆失败: $repo"
      mark_failed "$repo" "git clone 失败"
      continue
    fi
  else
    echo "本地仓库已存在，跳过 clone"
  fi

  pushd "$repo" >/dev/null

  rollback_merge_if_needed

  if ! ensure_clean_worktree; then
    echo "工作区不干净，跳过当前仓库。"
    mark_failed "$repo" "工作区存在未提交改动"
    popd >/dev/null
    continue
  fi

  echo "同步远端信息..."
  if ! git fetch origin --tags --prune; then
    echo "git fetch 失败"
    mark_failed "$repo" "git fetch 失败"
    popd >/dev/null
    continue
  fi

  echo "检查远端投产分支是否存在..."
  if ! git ls-remote --exit-code --heads origin "$local_release_branch" >/dev/null 2>&1; then
    echo "远端不存在分支: $local_release_branch"
    mark_failed "$repo" "远端不存在投产分支 $local_release_branch"
    popd >/dev/null
    continue
  fi

  echo "检查远端目标分支是否存在..."
  if ! git ls-remote --exit-code --heads origin "$local_target_branch" >/dev/null 2>&1; then
    echo "远端不存在目标分支: $local_target_branch"
    mark_failed "$repo" "远端不存在目标分支 $local_target_branch"
    popd >/dev/null
    continue
  fi

  echo "切换并更新投产分支: $local_release_branch"
  if git show-ref --verify --quiet "refs/heads/$local_release_branch"; then
    git checkout "$local_release_branch"
  else
    git checkout -b "$local_release_branch" "origin/$local_release_branch"
  fi

  if ! git pull --ff-only origin "$local_release_branch"; then
    echo "更新投产分支失败: $local_release_branch"
    mark_failed "$repo" "更新投产分支失败 $local_release_branch"
    popd >/dev/null
    continue
  fi

  echo "切换并更新目标分支: $local_target_branch"
  if git show-ref --verify --quiet "refs/heads/$local_target_branch"; then
    git checkout "$local_target_branch"
  else
    git checkout -b "$local_target_branch" "origin/$local_target_branch"
  fi

  if ! git pull --ff-only origin "$local_target_branch"; then
    echo "更新目标分支失败: $local_target_branch"
    mark_failed "$repo" "更新目标分支失败 $local_target_branch"
    popd >/dev/null
    continue
  fi

  echo
  echo "检查是否存在待合并提交..."
  if [[ -z "$(git log --oneline "origin/${local_target_branch}..${local_release_branch}")" ]]; then
    echo "没有可合并提交：${local_release_branch} 已包含在 ${local_target_branch} 或无新增内容"
    echo "跳过 merge、MR 和 tag"
    mark_skipped "$repo" "无待合并提交"
    popd >/dev/null
    continue
  fi

  echo
  echo "开始本地试合并检查: $local_release_branch -> $local_target_branch"
  if ! git merge --no-commit --no-ff "$local_release_branch"; then
    echo "本地 merge 失败，可能存在冲突。"
    echo "冲突文件如下："
    git diff --name-only --diff-filter=U || true
    rollback_merge_if_needed
    mark_failed "$repo" "本地 merge 冲突或失败"
    popd >/dev/null
    continue
  fi

  collect_repo_summary "$repo" "$local_release_branch" "$local_target_branch"
  echo "本地预合并成功，加入待处理列表。"
  mark_ready "$repo" "本地预合并成功，待创建并合并 MR"
  show_repo_summary "$repo" "$local_release_branch" "$local_target_branch"

  echo "清理本地试合并结果..."
  git merge --abort 2>/dev/null || git reset --hard "origin/$local_target_branch"

  popd >/dev/null
done

########################################
# 第二阶段：汇总 + 确认
########################################

print_stage_summary

echo
read -r -p "是否为所有待处理仓库创建并自动合并 MR？输入 yes 继续，其它任意键结束: " final_confirm

if [[ "$final_confirm" != "yes" ]]; then
  echo "你未确认创建/合并 MR，脚本结束。"
  print_final_summary
  exit 0
fi

########################################
# 第三阶段：创建 MR + 自动合并
########################################

TO_CREATE_MR_REPOS=("${READY_REPOS[@]}")

for repo in "${TO_CREATE_MR_REPOS[@]}"; do
  local_release_branch="${REPO_RELEASE_BRANCH[$repo]}"
  local_target_branch="${REPO_TARGET_BRANCH[$repo]}"

  print_sep "开始处理 MR: $repo"
  echo "源分支: $local_release_branch"
  echo "目标分支: $local_target_branch"
  echo "项目路径: ${GROUP}/${repo}"
  echo "编码后项目路径: $(urlencode_project_path "${GROUP}/${repo}")"
  echo "直接创建 MR..."

  set +e
  create_resp="$(create_merge_request "$repo" "$local_release_branch" "$local_target_branch")"
  create_rc=$?
  set -e

  echo "创建 MR 接口返回："
  echo "$create_resp"

  if [[ $create_rc -ne 0 ]]; then
    echo "创建 MR 调用失败，退出码: $create_rc"
    mark_failed "$repo" "创建 MR 调用失败，本仓库跳过打 tag"
    continue
  fi

  if [[ -z "$create_resp" ]]; then
    echo "创建 MR 失败：返回为空"
    mark_failed "$repo" "创建 MR 失败：返回为空，本仓库跳过打 tag"
    continue
  fi

  if printf '%s\n' "$create_resp" | grep -q '"message"'; then
    echo "创建 MR 失败，GitLab 返回："
    echo "$create_resp"
    mark_failed "$repo" "创建 MR 失败，本仓库跳过打 tag"
    continue
  fi

  mr_url="$(json_get_string "$create_resp" "web_url")"
  mr_iid="$(json_get_number "$create_resp" "iid")"

  if [[ -z "$mr_url" || -z "$mr_iid" ]]; then
    echo "创建 MR 成功，但未解析到 MR 信息"
    mark_failed "$repo" "创建 MR 后未解析到 MR 信息，本仓库跳过打 tag"
    continue
  fi

  echo "MR 创建成功: $mr_url"
  echo "MR IID: $mr_iid"

  REPO_MR_URL["$repo"]="$mr_url"
  REPO_MR_IID["$repo"]="$mr_iid"
  append_unique "$repo" "MR_CREATED_REPOS"

  if [[ "$AUTO_MERGE_MR" == "true" ]]; then
    echo "开始自动合并 MR..."

    set +e
    merge_resp="$(merge_merge_request "$repo" "${REPO_MR_IID[$repo]}" "$MERGE_MESSAGE")"
    merge_rc=$?
    set -e

    echo "合并 MR 接口返回："
    echo "$merge_resp"

    if [[ $merge_rc -ne 0 ]]; then
      echo "自动合并 MR 调用失败，退出码: $merge_rc"
      mark_failed "$repo" "自动合并 MR 调用失败，本仓库跳过打 tag"
      continue
    fi

    if [[ -z "$merge_resp" ]]; then
      echo "自动合并 MR 失败：返回为空"
      mark_failed "$repo" "自动合并 MR 失败：返回为空，本仓库跳过打 tag"
      continue
    fi

    if printf '%s\n' "$merge_resp" | grep -q '"message"'; then
      echo "自动合并 MR 失败，GitLab 返回："
      echo "$merge_resp"
      mark_failed "$repo" "自动合并 MR 失败，本仓库跳过打 tag"
      continue
    fi

    REPO_MR_STATE["$repo"]="merged"
    echo "MR 自动合并成功"
    mark_merged "$repo" "MR 创建后自动合并成功，可进入打 tag 阶段"
  else
    echo "未开启自动合并 MR，本仓库不自动打 tag"
    mark_success "$repo" "MR 已创建，未自动合并，跳过打 tag"
  fi
done

########################################
# 第四阶段：确认是否打 tag
########################################

print_mr_merge_summary

if [[ ${#MERGED_REPOS[@]} -eq 0 ]]; then
  echo
  echo "没有 MR 合并成功的仓库，因此不会执行打 tag。"
  print_final_summary
  exit 0
fi

echo
read -r -p "以上仓库 MR 已处理完成，是否继续为已合并成功的仓库统一打 tag？输入 yes 继续，其它任意键结束: " tag_confirm

if [[ "$tag_confirm" != "yes" ]]; then
  echo "你未确认打 tag，脚本结束。"
  print_final_summary
  exit 0
fi

########################################
# 第五阶段：统一打 tag
########################################

TO_TAG_REPOS=("${MERGED_REPOS[@]}")

for repo in "${TO_TAG_REPOS[@]}"; do
  local_target_branch="${REPO_TARGET_BRANCH[$repo]}"

  print_sep "开始打 tag: $repo"
  echo "目标分支: $local_target_branch"
  echo "Tag: $TAG_NAME"

  if [[ ! -d "$BASE_DIR/$repo/.git" ]]; then
    echo "本地仓库不存在，无法打 tag"
    REPO_TAG_RESULT["$repo"]="本地仓库不存在，tag 失败"
    mark_failed "$repo" "本地仓库不存在，tag 失败"
    continue
  fi

  pushd "$BASE_DIR/$repo" >/dev/null

  rollback_merge_if_needed

  if ! ensure_clean_worktree; then
    echo "工作区不干净，跳过 tag。"
    REPO_TAG_RESULT["$repo"]="工作区存在未提交改动，tag 失败"
    mark_failed "$repo" "工作区存在未提交改动，tag 失败"
    popd >/dev/null
    continue
  fi

  echo "同步远端信息..."
  if ! git fetch origin --prune --tags; then
    echo "git fetch 失败"
    REPO_TAG_RESULT["$repo"]="git fetch 失败，tag 失败"
    mark_failed "$repo" "git fetch 失败，tag 失败"
    popd >/dev/null
    continue
  fi

  echo "切换并重置到远端目标分支..."
  if git show-ref --verify --quiet "refs/heads/$local_target_branch"; then
    git checkout "$local_target_branch"
  else
    git checkout -b "$local_target_branch" "origin/$local_target_branch"
  fi

  git reset --hard "origin/$local_target_branch"

  echo "检查远端 tag 是否存在..."
  if git ls-remote --tags origin "refs/tags/$TAG_NAME" | grep -q "refs/tags/$TAG_NAME$"; then
    echo "远端 tag 已存在: $TAG_NAME"

    if [[ "$FORCE_TAG" == "true" ]]; then
      echo "启用强制覆盖 tag..."

      if git rev-parse -q --verify "refs/tags/$TAG_NAME" >/dev/null; then
        git tag -d "$TAG_NAME"
      fi

      if ! git tag -a "$TAG_NAME" -m "$TAG_MESSAGE"; then
        echo "创建 tag 失败"
        REPO_TAG_RESULT["$repo"]="强制覆盖时创建 tag 失败"
        mark_failed "$repo" "强制覆盖时创建 tag 失败"
        popd >/dev/null
        continue
      fi

      if ! git push -f origin "$TAG_NAME"; then
        echo "push tag 失败"
        REPO_TAG_RESULT["$repo"]="强制覆盖时 push tag 失败"
        mark_failed "$repo" "强制覆盖时 push tag 失败"
        popd >/dev/null
        continue
      fi

      echo "Tag 已强制更新并推送: $TAG_NAME"
      REPO_TAG_RESULT["$repo"]="tag 强制覆盖成功"
      append_unique "$repo" "TAG_SUCCESS_REPOS"
      mark_success "$repo" "MR 合并成功，tag 强制覆盖成功"
    else
      echo "跳过打 tag（如需覆盖请设置 FORCE_TAG=true）"
      REPO_TAG_RESULT["$repo"]="远端 tag 已存在，未覆盖"
      mark_success "$repo" "MR 合并成功，远端 tag 已存在，未覆盖"
    fi
  else
    echo "创建附注 tag: $TAG_NAME"

    if git rev-parse -q --verify "refs/tags/$TAG_NAME" >/dev/null; then
      git tag -d "$TAG_NAME"
    fi

    if ! git tag -a "$TAG_NAME" -m "$TAG_MESSAGE"; then
      echo "创建 tag 失败"
      REPO_TAG_RESULT["$repo"]="创建 tag 失败"
      mark_failed "$repo" "创建 tag 失败"
      popd >/dev/null
      continue
    fi

    if ! git push origin "$TAG_NAME"; then
      echo "push tag 失败"
      REPO_TAG_RESULT["$repo"]="push tag 失败"
      mark_failed "$repo" "push tag 失败"
      popd >/dev/null
      continue
    fi

    echo "Tag created & pushed: $TAG_NAME"
    REPO_TAG_RESULT["$repo"]="tag 创建并推送成功"
    append_unique "$repo" "TAG_SUCCESS_REPOS"
    mark_success "$repo" "MR 合并成功，tag 创建并推送成功"
  fi

  popd >/dev/null
done

########################################
# 最终汇总
########################################

print_tag_summary
print_final_summary
