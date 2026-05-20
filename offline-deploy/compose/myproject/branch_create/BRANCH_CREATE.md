# 投产征集 / 分支创建 模块开发文档

## 一、模块概述

`branch_create` 是 DevOps 平台的核心应用模块，负责**分支创建**相关功能，包含三大业务场景：

1. **投产征集（Release Entry）**：按批次（ReleaseBatch）组织投产计划，研发人员在批次下提交投产行项（ReleaseItem），记录流程名称、分支信息、各类上线检查项等。
2. **HOBO 需求登记台账（Hobo Requirement Ledger）**：研发人员登记需求信息，维护需求分支、依赖关系、上线日期等。
3. **分支创建执行**：统一对投产征集和 HOBO 需求登记中未建分支的记录，自动执行 Git 分支创建操作。

---

## 二、数据模型（models.py）

### 2.1 ProjectCatalog — 工程目录

| 字段 | 类型 | 说明 |
|------|------|------|
| project_code | CharField(64) unique | 工程编码，如 `hobo-customer-front` |
| project_name | CharField(128) | 工程名称，如 `客户流程前端` |
| enabled | BooleanField | 是否启用 |

用途：全局工程枚举表，作为 HOBO 需求登记和投产征集批次的**标准工程来源**。

### 2.2 ReleaseBatch — 投产批次

| 字段 | 类型 | 说明 |
|------|------|------|
| release_date | DateField unique | 投产日期 |
| release_type | CharField(16) | `release` 或 `hotfix` |
| release_branch | CharField(64) | 投产分支名，如 `release-20260516` |
| status | CharField(16) | `open` ↔ `closed` → `executed`，见下文状态流转 |
| created_by | FK → User | 创建人 |

批次状态流转：
- **open（开放填写）**：批次创建时的初始状态，研发人员可提交行项。所有 CRUD 操作要求 `batch.status == OPEN`
- **closed（关闭填写）**：管理员关闭填写后不再接受新增/编辑/删除。可重新开放（→ open）或标记已执行（→ executed）
- **executed（已执行）**：投产已完成，不可再变更
- ~~draft（草稿）~~：枚举定义了但未使用

状态变更通过 `release-entry/api/batches/update-status/` API（仅 `release_batch_manage` 权限）：
```
open ──→ closed ──→ executed
  ↑        │
  └────────┘（重新开放）
```

### 2.3 ReleaseBatchProject — 批次关联工程

| 字段 | 类型 | 说明 |
|------|------|------|
| batch | FK → ReleaseBatch | 所属批次 |
| project_code | CharField(64) | 工程编码 |
| project_name | CharField(128) | 工程名称 |
| enabled | BooleanField | 是否在该批次中启用 |

唯一约束：`(batch, project_code)`

批次创建时自动从 ProjectCatalog 同步工程列表。工程在 ProjectCatalog 中被禁用后，已存在的批次工程会自动标记 `enabled=False`。

### 2.4 ReleaseBranchSequence — 分支序号生成器

| 字段 | 类型 | 说明 |
|------|------|------|
| branch_type | CharField(3) | `FIX` / `REQ` / `PUB` |
| date_str | CharField(8) | 日期字符串 `YYYYMMDD` |
| current_serial | PositiveIntegerField | 当前序号（自增） |

唯一约束：`(branch_type, date_str)`

用于生成需求分支名。规则：`{branch_type}-{date_str}-{serial:04d}`，例如 `REQ-20260516-0001`。
使用 `select_for_update` 保证并发安全。

### 2.5 ReleaseItem — 投产行项

核心表，投产征集中的每一条填写记录。

**分类字段**：

| 字段组 | 字段 | 说明 |
|--------|------|------|
| 基本信息 | branch_type | 分支类型 FIX/REQ/PUB |
| | requirement_branch | 需求分支名称（可空表示仅SQL上线） |
| | sql_only_release | 仅SQL上线无需需求分支 |
| | flow_name | 流程/功能名称 |
| | biz_category | 业务种类 |
| | release_branch | 行项投产分支 |
| | tech_owner | 科技联系人 |
| | biz_owner | 业务联系人 |
| | common_component_branch | 公共组件分支 |
| | implementation_unit_no | 实施单元编号 |
| | remark | 备注 |
| bug修复信息 | is_bug_fix | 是否为 bug 修复（三态：null=未填写/true=是/false=否） |
| | bug_reporter | bug 修复汇报人（多选：周子健/高翔，逗号分隔） |
| | bug_discovery_time | bug 发现时间（datetime，精确到小时） |
| 研发字段 | need_param_release / param_confirmed | 是否需要参数投产 / 参数是否已确认 |
| | need_menu / menu_added | 是否需要菜单 / 菜单是否已新增 |
| | need_difs | 是否需要 DIFS |
| | need_flowchart / flowchart_checked / flow_definition_name | 是否需要流程图 / 是否已核对 / 流程定义名称 |
| | need_event_platform | 是否需要事件平台 |
| | need_task_pool | 是否需要任务池 |
| | need_bpmp | 是否需要 BPMP |
| | need_image | 是否需要镜像 |
| | need_esf | 是否需要 ESF |
| | need_trade_tuning | 是否需要交易申调 |
| | need_release_verify | 是否需要投产验证 |
| | need_config_release | 是否需要配置文件投产 |
| 运维字段 | rel_deployed | REL 是否已部署 |
| | deploy_status | 投产状态（是/否/未填写） |
| 状态字段 | rel_test_status | REL 测试状态 |
| | line_status | 行状态（draft/incomplete/submitted/confirmed/rejected） |
| | developer | FK → User 填写人 |
| 分支创建 | branch_created | 分支是否已创建 |
| | branch_created_at | 创建时间 |
| | branch_created_by | 创建人 |
| | branch_create_error | 错误信息 |
| | branch_create_log | 执行日志 |

**行状态自动刷新机制**：
- `get_missing_fields()` 检查必填字段（18个核心字段 + 4个条件依赖字段）
- 条件依赖：`need_param_release=True` 时 `param_confirmed` 必填；`need_menu=True` 时 `menu_added` 必填；`need_flowchart=True` 时 `flowchart_checked` 和 `flow_definition_name` 必填；`is_bug_fix=True` 时 `bug_reporter` 和 `bug_discovery_time` 必填
- `save()` 自动调用 `refresh_line_status()`：缺失必填 → `incomplete`，完整 → `draft`
- 提交时再次检查，全部完整才允许变为 `submitted`
- **创建/更新时**：`need_release_verify`（涉及投产验证）为必填项，未选择是/否时直接拒绝保存，返回 400 错误

### 2.6 HoboRequirementLedger — HOBO 需求登记台账

| 字段 | 类型 | 说明 |
|------|------|------|
| requirement_type | CharField(3) | 需求类型 FIX/REQ/PUB |
| requirement_branch | CharField(68) unique | 分支名称（自动生成 + 可选自定义后缀） |
| project | FK → ProjectCatalog | 工程 |
| description | TextField | 需求描述 |
| applicant_name | CharField(128) | 申请人 |
| applied_date | DateField | 申请日期 |
| base_branch | CharField(128) | 依赖分支 |
| base_branch_contact | CharField(128) | 依赖分支联系人 |
| flowchart_name | CharField(256) | 流程图名称 |
| uat_submit_date | DateField | 提交 UAT 日期 |
| rel_submit_date | DateField | 提交 REL 日期 |
| production_date | DateField | 投产日期 |
| remark | TextField | 备注 |
| branch_created / branch_created_at / branch_created_by | | 分支创建状态 |
| branch_create_error / branch_create_log | | 错误日志 |
| created_by | FK → User | 登记人 |

**分支名生成规则**：
1. 调用 `ReleaseItem._next_requirement_branch(requirement_type)` 生成基础分支名
2. 如果用户填写了自定义后缀（`custom_branch_suffix`），追加为 `{base}-{suffix}`
3. 后缀验证：长度 ≤ 50，仅允许中文、字母、数字、下划线和中划线

**依赖分支约束**：填写依赖分支时必须同时填写依赖分支联系人。

### 2.7 BranchCreateSchedule — 分支创建计划任务

| 字段 | 类型 | 说明 |
|------|------|------|
| name | CharField(64) unique | 任务名称 |
| enabled | BooleanField | 是否启用 |
| cron_expr | CharField(64) | 5 字段 cron 表达式 |
| source_type | CharField(16) | `hobo` / `release` / `both` |
| days_back | IntegerField | 回看天数（正数回看过去，负数查未来） |
| created_by | FK → User | 创建人 |
| last_run_at | DateTimeField | 上次执行时间 |

### 2.8 BranchCreateScheduleRun — 调度执行记录

| 字段 | 类型 | 说明 |
|------|------|------|
| schedule | FK → BranchCreateSchedule | 所属计划 |
| status | CharField(16) | running/success/failed |
| trigger_mode | CharField(16) | manual/cron |
| triggered_by | FK → User | 触发人 |
| total_count / success_count / skipped_count / failed_count | | 执行统计 |
| summary / log | | 结果摘要和日志 |

### 2.9 BranchTaskExecuteRun / BranchTaskExecuteRunItem — 前台执行记录

用于 Web 端批量执行时的进度追踪。

- **BranchTaskExecuteRun**：run_id 唯一标识一次执行，包含状态、计数、tip
- **BranchTaskExecuteRunItem**：每行任务的执行结果（seq 序号、source_type/source_id 来源、project_code、new_branch、status、message、log）

### 2.10 ExportSchedule — 定时导出计划

| 字段 | 类型 | 说明 |
|------|------|------|
| name | CharField(64) unique | 任务名称 |
| enabled | BooleanField | 是否启用 |
| cron_expr | CharField(64) | cron 表达式 |
| export_type | CharField(32) | `hobo_ledger` / `release_entry` |
| created_by | FK → User | 创建人 |
| last_run_at | DateTimeField | 上次执行时间 |

---

## 三、权限系统

### 3.1 字段级权限

通过 `accounts/role_meta.py` 定义：

**研发字段（RELEASE_ENTRY_DEV_FIELD_KEYS）**：30 个字段，包括基本信息、所有布尔检查项、bug修复信息、rel_test_status

**运维字段（RELEASE_ENTRY_OPS_FIELD_KEYS）**：仅 `rel_deployed`、`deploy_status`

配置在 `RolePermissionPolicy.release_entry_editable_fields` 中，每个角色可定制可编辑的字段集合。

### 3.2 操作权限

| 权限 Key | 说明 |
|----------|------|
| branch_task_preview | 查看分支创建预览（通用） |
| branch_task_execute_hobo | 执行 HOBO 分支创建 |
| branch_task_execute_release | 执行投产分支创建 |
| release_batch_manage | 管理投产批次（创建/删除） |
| release_item_create | 创建投产行项 |
| release_item_edit_others | 编辑他人的行项 |
| release_item_delete_own | 删除自己的行项 |
| release_entry_export | 导出投产征集 |
| schedule_manage | 管理分支创建计划 |
| hobo_item_create | 创建 HOBO 登记 |
| hobo_item_edit_own / hobo_item_edit_others | 编辑 HOBO 登记 |
| hobo_item_delete_own | 删除 HOBO 登记 |
| hobo_ledger_export | 导出 HOBO 台账 |
| auto_export_hobo_ledger | 自动导出 HOBO 台账 |
| auto_export_release_entry | 自动导出投产征集 |

### 3.3 数据范围

- 投产行项列表通过 `apply_data_scope(items, user, scope_key="release_entry", owner_field="developer")` 控制可见范围
- HOBO 台账列表通过 `apply_data_scope(items, user, scope_key="hobo_ledger", owner_field="created_by")` 控制可见范围

### 3.4 菜单权限

通过 `can_access_menu(user, "branch_create")` 控制分支创建主页访问，`can_access_menu(user, "export_schedule")` 控制导出计划页访问。

---

## 四、URL 路由（urls.py）

### 4.1 页面路由

| URL | View | 说明 |
|-----|------|------|
| `/branch-create/` | branch_create_index | 主页面（分支创建查询） |
| `/branch-create/execute/` | branch_create_execute | 配置文本执行 |
| `/branch-create/release-entry/` | release_entry_page | 投产征集填写页 |
| `/branch-create/hobo-ledger/` | hobo_ledger_page | HOBO 需求登记页 |
| `/branch-create/export-schedules/` | export_schedule_page | 导出计划管理页 |

### 4.2 API 路由

**分支创建**：
- `POST /branch-create/api/precheck/` — 配置预检查
- `POST /branch-create/api/create/` — 执行单条分支创建
- `POST /branch-create/api/branch-tasks/preview/` — 预览待建分支列表
- `POST /branch-create/api/branch-tasks/execute/` — 同步批量执行
- `POST /branch-create/api/branch-tasks/execute/start/` — 异步启动执行
- `GET /branch-create/api/branch-tasks/execute/progress/` — 查询执行进度

**计划任务**：
- `GET /branch-create/api/schedules/` — 列表
- `POST /branch-create/api/schedules/save/` — 新增/编辑
- `POST /branch-create/api/schedules/delete/` — 删除
- `POST /branch-create/api/schedules/run/` — 手动执行
- `POST /branch-create/api/schedules/run-due/` — 执行到期任务

**投产征集**：
- `GET /release-entry/api/batches/` — 批次列表
- `POST /release-entry/api/batches/create/` / `delete/` / `update-status/` — 管理批次
- `GET /release-entry/api/items/` — 行项列表
- `POST /release-entry/api/items/create/` / `update/` / `submit/` / `delete/` — CRUD
- `GET /release-entry/api/items/last-by-project/` — 引用上次填写
- `POST /release-entry/api/items/bulk-update/` — 批量修改运维字段
- `GET /release-entry/export.xlsx` — 导出 Excel

**HOBO 台账**：
- `GET /hobo-ledger/api/projects/` — 工程列表
- `GET /hobo-ledger/api/items/` — 登记项列表
- `POST /hobo-ledger/api/items/create/` / `update/` / `delete/` — CRUD
- `GET /hobo-ledger/export.xlsx` — 导出 Excel

**导出计划**：
- `GET /api/export-schedules/` — 列表
- `POST /api/export-schedules/save/` / `delete/` — 管理
- `POST /api/export-schedules/run-now/` — 立即执行

---

## 五、核心业务逻辑

### 5.1 分支创建执行器（BranchExecutor）

文件：`services/branch_executor.py`

执行流程：
1. **解析工程名**：通过 `normalize_project_code()` 做项目名映射
2. **准备工作目录**：读取 Git 配置中的工作目录，回退到 `.runtime/repos/`
3. **克隆/更新仓库**：
   - 如目录不存在 → `git clone`
   - 如 `.git` 不存在 → 报错"不是 Git 仓库"
4. **设置远端地址**：`git remote set-url origin <url>`（带凭证）
5. **检查工作区**：`git status --porcelain` 必须干净
6. **拉取最新**：`git fetch origin --prune`
7. **检查分支**：
   - 本地分支已存在 → skipped
   - 远程分支已存在 → skipped
8. **切换基准分支**：`git checkout <base_branch>` / `pull --ff-only`
9. **创建新分支**：`git checkout -b <new_branch>`
10. **推送**：`git push -u origin <new_branch>`

结果状态：`success` / `skipped` / `failed`

敏感信息脱敏：所有日志通过 `scrub_sensitive_text()` 处理后才写入。

### 5.2 任务收集（collect_pending_tasks）

文件：`services/branch_tasks.py`

按 `source_type` 分别查询：

- **HOBO**：查询 `HoboRequirementLedger` 中 `applied_date` 在日期范围、`branch_created=False` 的记录
- **Release**：查询 `ReleaseItem` 中 `batch.status=OPEN`、`batch.release_date` 在日期范围、`branch_created=False` 的记录，**排除 `sql_only_release=True`（仅SQL上线无需建分支）**
- **Both**：合并两者

日期范围算法（`_resolve_date_range`）：
- `days_back > 0`：`(today - days_back) ~ today`
- `days_back = 0`：仅今天
- `days_back < 0`：`today ~ (today + abs(days_back))`

### 5.3 远程检查（filter_preview_tasks_with_remote_check）

在预览阶段，对每个任务执行 `git ls-remote --exit-code --heads origin <branch>` 检查远端是否已存在分支。如已存在则自动标记 `branch_created=True` 并更新数据库，避免重复创建。

### 5.4 批量执行（execute_tasks）

依次执行每个任务，每执行完一个就调用 `_mark_task_result` 更新对应源记录的 `branch_created` / `branch_created_at` / `branch_created_by` / `branch_create_error` / `branch_create_log`。

### 5.5 异步执行流程

1. 前端 POST `/api/branch-tasks/execute/start/` → 创建 `BranchTaskExecuteRun`，序列化任务到 `.runtime/jobs/`，调用 `run_branch_execute_run` 管理命令作为独立进程运行
2. 前端轮询 `/api/branch-tasks/execute/progress/?run_id=xxx` 获取进度
3. 后端 `_run_execute_job` 通过 `on_progress` 回调实时写入 `BranchTaskExecuteRunItem`

### 5.6 配置解析（config_parser.py）

用于配置文本模式的批量分支创建。

文本格式：每行 `<branch_name> <project_code>`，注释 `#` 开头跳过。

工程名映射规则：
1. 标准化：去空格、小写、`_` → `-`
2. 直接在标准工程列表中匹配
3. 尝试加 `hobo-` 前缀匹配
4. 尝试去掉前缀后匹配

### 5.7 引用上次填写

API：`GET /release-entry/api/items/last-by-project/`

用户在新建行项时点击"引用上次填写"，系统根据当前工程查询历史最近一次填写的行项，将其部分字段值填入当前表单。

**引用字段白名单**（`_QUOTE_LAST_ITEM_KEYS`）：
- 基础信息：`biz_category`, `tech_owner`, `biz_owner`, `common_component_branch`, `flow_definition_name`, `implementation_unit_no`
- 检查项：`need_param_release`, `param_confirmed`, `need_menu`, `menu_added`, `need_difs`, `need_flowchart`, `flowchart_checked`, `need_event_platform`, `need_task_pool`, `need_bpmp`, `need_image`, `need_esf`, `need_trade_tuning`, `need_release_verify`

**排除字段**（不会被引用）：
- `flow_name`（流程/功能名称）— 每条记录的核心标识，不应重复
- `remark`（备注）— 每条记录独立填写
- `requirement_branch`（需求分支）— 系统自动生成
- `is_bug_fix`（是否为bug修复）— 已有默认值"否"，不需引用
- `bug_reporter`（bug修复汇报人）— 每条独立填写
- `bug_discovery_time`（bug发现时间）— 每条独立填写
- `rel_test_status`（REL测试状态）— 已有默认值"否"，不需引用
- `need_config_release`（涉及配置文件投产）— 每条独立确认

查询逻辑：优先引用上一个批次（排除当前批次），回退到所有历史最新记录。

实现位置：
- 后端：`release_entry_views.py` → `release_entry_item_last_by_project()` 视图 + `_QUOTE_LAST_ITEM_KEYS` 常量
- 前端：`release_entry.html` → `applyLastItemToForm()` 函数，通过 `el("btn-quote-last")` 按钮触发

### 5.8 表单默认值

新建行项或切换工程清空表单时，以下字段自动填充默认值，减少填写负担：

| 字段 | 默认值 | 设置位置 |
|------|--------|----------|
| `is_bug_fix`（是否为bug修复） | 否 | HTML `selected` 属性 + `clearForm()` + `clearFormForProjectSwitch()` |
| `rel_test_status`（REL测试状态） | 否 | HTML `selected` 属性 + `clearForm()` + `clearFormForProjectSwitch()` |

**三层保障**：
1. **HTML 层**：`<option value="否" selected>` 确保页面首次加载时下拉框默认选中"否"
2. **`clearForm()`**：新建记录时清空所有字段后，显式设置这两个字段为"否"
3. **`clearFormForProjectSwitch()`**：切换工程触发表单清空时，同样设置这两个字段为"否"

> **注意**：引用上次填写（5.7）不会覆盖这些默认值，因为 `is_bug_fix` 和 `rel_test_status` 均在引用排除列表中。

实现位置：
- 前端：`release_entry.html` → `clearForm()` / `clearFormForProjectSwitch()` 函数
- HTML：`<select id="is-bug-fix">` 和 `<select id="rel-test-status">` 的 `selected` 属性

### 5.9 Cron 匹配（cron_utils.py）

标准 5 字段 cron 表达式匹配：`minute hour dom month dow`。
支持：`*`、`*/N`（每 N）、`,`（枚举）、精确数字。

---

## 六、管理命令

### 6.1 clock_tick

统一时钟调度入口，建议在 crontab 中每分钟调用一次：
```
* * * * * cd /path/to/myproject && python manage.py clock_tick
```

逻辑：
1. 遍历所有 `BranchCreateSchedule`（enabled=True），匹配 cron → 执行建分支调度
2. 遍历所有 `ExportSchedule`（enabled=True），匹配 cron → 执行导出

### 6.2 run_branch_schedules

分支创建调度执行，支持 `--due` 参数仅执行到期任务。

### 6.3 run_export_schedules

导出调度执行，支持：
- `--due`：仅执行到期
- `--schedule-id N`：执行指定调度

导出文件位置：
- HOBO：`HOBO_EXPORT_DIR` 环境变量 或 `.runtime/exports/hobo-需求登记台账/`
- 投产征集：`RELEASE_ENTRY_EXPORT_DIR` 环境变量 或 `.runtime/exports/投产征集/`

### 6.4 run_branch_execute_run

由 Web 端异步触发，读取 JSON payload 文件后执行 `_run_execute_job`。

### 6.5 install_branch_schedule_cron

输出建议的 crontab 条目，便于部署时配置。

### 6.6 release_track

投产追板命令，用于将投产分支合并到目标分支并打 tag。

参数：`--batch-id`（必填）、`--config-file`（repos.conf 格式）、`--tag-name / --merge-message / --tag-message`（覆盖配置）、`--yes`（跳过交互）、`--dry-run`（演练模式）。

---

## 七、投产追板（ReleaseTrackService）

文件：`services/release_track_service.py`

### 执行阶段

```
plan → precheck → (确认) → mr → approval → verify_mr → (确认) → tag → done
```

1. **plan**：从批次中筛选 `rel_deployed=True` 的行项，去重得仓库列表
2. **precheck**：clone/更新仓库 → 检查远端分支存在 → 本地 trial merge → 计算 pending commits
3. **mr**：创建 GitLab MR（source=release_branch → target=target_branch）
4. **approval**：提示管理员在 GitLab 完成 MR 审批
5. **verify_mr**：调用 GitLab API 确认 MR 状态为 merged
6. **tag**：在每个仓库 `target_branch` 的最新 commit 上创建 annotated tag 并推送

### 仓库状态

| 状态 | 含义 |
|------|------|
| READY | 预检查通过，待创建 MR |
| WAIT_MR | MR 已创建，待人工合并 |
| MERGED | MR 已合并，待打 tag |
| SUCCESS | Tag 创建成功 |
| SKIPPED | 无待合并提交 |
| FAILED | 执行失败 |
| MERGED_NO_TAG | 合并成功但未打 tag（skip_tag 模式） |

---

## 八、模板页面

| 模板文件 | 功能 |
|----------|------|
| branch_create/index.html | 分支创建主页面：筛选、预览、批量执行、计划任务管理 |
| branch_create/release_entry.html | 投产征集填写页：批次管理、行项 CRUD、Excel 导出 |
| branch_create/hobo_requirement_ledger.html | HOBO 需求登记页：登记列表、新增/编辑弹窗、Excel 导出 |
| branch_create/export_schedules.html | 导出计划管理页 |
| branch_create/result.html | 配置文本执行结果页 |

---

## 九、Excel 导出

### 投产征集导出

文件：`release_entry_views.py::_release_entry_xlsx_bytes`

39 列，包括：批次信息、工程信息、分支信息、所有布尔检查项（是/否/未填写）、bug修复信息、行状态、分支创建状态、填写人等。

导出文件名：`release_entry_{date}_{branch}.xlsx`

### HOBO 台账导出

文件：`hobo_ledger_views.py::_hobo_ledger_xlsx_bytes`

19 列，包括：需求类型、分支名称、工程、描述、申请人、日期、依赖分支、投产日期、建分支状态等。

导出文件名：`hobo_requirement_ledger_{date}.xlsx`

---

## 十、数据库迁移历史

| 迁移 | 内容 |
|------|------|
| 0001 | 初始模型：ReleaseBatch, ReleaseBatchProject, ReleaseBranchSequence, ReleaseItem |
| 0002 | ReleaseBatch 增加 release_type |
| 0003 | ProjectCatalog |
| 0004 | ProjectCatalog project_name 允许空 |
| 0005 | ReleaseItem 增加 deploy_status |
| 0006 | HoboRequirementLedger |
| 0007 | HoboRequirementLedger 增加 requirement_branch |
| 0008 | ReleaseItem 增加 biz_category |
| 0009 | BranchCreateSchedule, BranchCreateScheduleRun |
| 0010 | ReleaseItem 增加 branch_create_log |
| 0011 | BranchTaskExecuteRun, BranchTaskExecuteRunItem |
| 0012 | ReleaseItem 增加 implementation_unit_no |
| 0013 | ReleaseItem 增加 remark |
| 0014 | ReleaseItem 增加 sql_only_release |
| 0015-0019 | requirement_branch 字段调整 |
| 0020 | HoboRequirementLedger base_branch 长度调整到 128 |
| 0021 | ExportSchedule 模型 + ReleaseItem need_config_release |

---

## 十一、开发约定

### 字段命名
- 布尔字段统一 `need_xxx` 前缀 + 三态（True/False/None=None 表示未填写）
- 状态字段统一 `xxx_status` 后缀

### 时间显示
- 所有前端显示时间使用东八区（通过 `timezone.localtime()` 转换）

### 安全
- Git 操作的 URL 和日志必须通过 `scrub_sensitive_text()` 脱敏
- 所有操作需校验 `can_do_action()` 权限
- 列表查询通过 `apply_data_scope()` 控制数据范围
- 删除操作需验证 `request.user` 是创建人或超管

### 计划任务
- 所有计划任务统一通过 `clock_tick` 管理命令驱动
- cron 表达式使用标准 5 字段格式
- 配置 crontab 时使用 `install_branch_schedule_cron` 输出建议条目
