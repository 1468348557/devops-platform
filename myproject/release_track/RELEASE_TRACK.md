# 投产追板 / Release Track 模块开发文档

## 一、模块概述

`release_track` 是 DevOps 平台中负责**投产追板**的应用模块。它将投产征集（branch_create）中已标记"REL 已部署"的行项对应的仓库，自动创建 GitLab MR 并打 Tag，实现投产分支到目标分支的自动合并流程。

核心工作流：选择批次 → 勾选仓库 → 预检查 → 创建 MR → 人工审批 → 验证 MR 状态 → 打 Tag。

---

## 二、数据模型（models.py）

### 2.1 ReleaseTrackRun — 追板执行记录

| 字段 | 类型 | 说明 |
|------|------|------|
| run_id | CharField(64) unique | 唯一运行标识（UUID） |
| status | CharField(16) | `running` / `success` / `failed` |
| phase | CharField(32) | 当前阶段：`init` / `plan` / `precheck` / `mr` / `approval` / `verify_mr` / `tag` / `done` |
| approval_status | CharField(16) | 审批状态，默认 `pending` |
| approval_url | CharField(255) | 审批 URL |
| approved_by | FK → User | 审批人，SET_NULL |
| approved_at | DateTimeField | 审批时间 |
| triggered_by | FK → User | 触发人，SET_NULL |
| batch_id | PositiveIntegerField | 关联的投产批次 ID |
| tag_name | CharField(128) | Tag 名称 |
| merge_message | CharField(128) | MR 合并信息 |
| tag_message | CharField(128) | Tag 信息 |
| dry_run | BooleanField | 是否为演练模式 |
| total_count / processed_count / success_count / skipped_count / failed_count | PositiveIntegerField | 统计计数 |
| tip / error | CharField(255) | 提示信息 / 错误信息 |
| started_at / finished_at | DateTimeField | 时间记录 |

### 2.2 ReleaseTrackRunItem — 追板仓库执行明细

| 字段 | 类型 | 说明 |
|------|------|------|
| run | FK → ReleaseTrackRun | 所属执行记录（CASCADE） |
| repo | CharField(128) | 仓库名/编码 |
| release_branch | CharField(128) | 投产分支名 |
| target_branch | CharField(128) | 目标分支名 |
| stage | CharField(32) | 当前阶段 |
| status | CharField(16) | 状态：`pending` / `READY` / `WAIT_MR` / `MERGED` / `SUCCESS` / `FAILED` / `SKIPPED` / `MERGED_NO_TAG` |
| reason | CharField(255) | 状态原因 |
| pending_count | PositiveIntegerField | 待合并提交数 |
| mr_url | CharField(255) | MR 的 URL |
| mr_iid | PositiveIntegerField | MR 的 IID |
| mr_state | CharField(32) | MR 的状态 |
| tag_result | CharField(255) | Tag 操作结果 |
| source | CharField(32) | 来源标识 |

唯一约束：`(run, repo)`

---

## 三、URL 路由（urls.py）

| URL | 方法 | View | 说明 |
|-----|------|------|------|
| `/release-track/` | GET | release_track_index | 主页面 |
| `/release-track/execute/` | GET/POST | release_track_execute | 旧版同步执行（result.html） |
| `/release-track/api/batches/` | GET | release_track_api_batches | 获取可用批次列表 |
| `/release-track/api/batch-detail/` | GET | release_track_api_batch_detail | 批次详情+项目列表 |
| `/release-track/api/run/start/` | POST | release_track_api_run_start | 启动追版运行 |
| `/release-track/api/run/progress/` | GET | release_track_api_run_progress | 查询运行进度 |
| `/release-track/api/run/approve/` | POST | release_track_api_run_approve | 审批操作 |
| `/release-track/api/precheck/` | POST | release_track_api_precheck | 预检查 |
| `/release-track/api/create-mr/` | POST | release_track_api_create_mr | 创建 MR |
| `/release-track/api/create-tag/` | POST | release_track_api_create_tag | 创建 Tag |

---

## 四、核心业务逻辑

### 4.1 追版执行引擎（ReleaseTrackService）

实际引擎位于 `branch_create/services/release_track_service.py`，由本应用调用。详见 [BRANCH_CREATE.md 第七章](../branch_create/BRANCH_CREATE.md#七投产追板releasetrackservice)。

### 4.2 视图层执行流程（views.py）

#### 主页面（release_track_index）
- 渲染 `release_track/index.html`
- 提供 SPA 风格的追版操作界面

#### 运行启动（release_track_api_run_start）
1. 验证 `batch_id`，确保批次状态为 OPEN
2. 解析 `selected_projects`（JSON 数组或逗号分隔）
3. 检查项目是否已被锁定（已有追版记录在运行）
4. 读取配置参数：`skip_tag`、`tag_name`、`merge_message`、`tag_message`、`config_text`
5. 构建 `ReleaseTrackOptions` 并创建 `ReleaseTrackRun`
6. 序列化配置到 `.runtime/jobs/` 下 JSON 文件
7. 启动独立后台进程：`manage.py run_release_track_run <run_id> <payload_file>`
8. 返回 `run_id` 供前端轮询

#### 进度查询（release_track_api_run_progress）
- 按 `run_id` 查询 `ReleaseTrackRun` 和 `ReleaseTrackRunItem`
- 权限检查：仅触发人或超管可查看
- 返回完整运行状态（含所有仓库明细）

#### 审批操作（release_track_api_run_approve）
- 支持 `approve` 或 `reject` 操作
- 验证运行仍在进行中且处于审批阶段
- 更新 `approval_status`、`approved_by`、`approved_at` 字段

#### 后台工作进程（_run_release_track_worker）
1. 加载 `ReleaseTrackRun` 记录
2. 定义 `wait_for_approval(callback)`：轮询 DB 直到审批完成
3. 定义 `on_event(callback)`：处理 `phase`、`approval`、`summary` 事件，更新数据库
4. 创建 `ReleaseTrackService` 执行 `service.run()`
5. 运行结束更新统计计数和状态

### 4.3 旧版同步执行（release_track_execute + result.html）
- POST 时解析配置，执行本地预合并检查
- 返回每个仓库的合并可行性
- result.html 提供逐仓库的 MR 创建按钮

---

## 五、GitLab API 客户端（gitlab_api.py）

### GitLabConfig 数据类

| 字段 | 说明 |
|------|------|
| base_url | GitLab 基础 URL |
| group | 组名 |
| token | PAT 令牌 |
| username / password | 备选认证方式 |
| api_version | 默认 `v4` |

### GitLabAPI 类

基于标准库 `urllib` 实现，无外部依赖。支持 `PRIVATE-TOKEN` 或 Basic Auth。

| 方法 | 说明 |
|------|------|
| branch_exists(repo, branch) | 检查分支是否存在 |
| list_branches(repo) | 列出分支 |
| create_mr(repo, source, target, title, desc) | 创建合并请求 |
| merge_mr(repo, mr_iid, message) | 合并 MR |
| get_mr(repo, mr_iid) | 获取 MR 详情 |
| tag_exists(repo, tag_name) | 检查 Tag |
| create_tag(repo, tag_name, ref, message) | 创建 Tag |
| delete_tag(repo, tag_name) | 删除 Tag |
| force_push_tag(repo, tag_name, ref, message) | 强制覆盖 Tag |

---

## 六、配置解析（config_parser.py）

### repos.conf 格式
```
TAG_NAME=release-2026.05.16
MERGE_MESSAGE=Merge release branches
TAG_MESSAGE=Production release 2026.05.16

repo_name|release_branch|target_branch
hobo-customer-front|release-20260516|master
```

解析规则：
- `#` 开头的为注释行
- 提取 `TAG_NAME`、`MERGE_MESSAGE`、`TAG_MESSAGE` 变量
- 跳过标题行（含 `=` 或 `repo_name`）
- 按 `|` 分隔解析仓库配置行

---

## 七、管理命令

### run_release_track_run

后台执行追板运行的主入口，由 Web 端异步触发。

参数：
- `run_id` — 运行 ID
- `payload_file` — JSON 配置文件路径

逻辑：读取 JSON 负载 → 删除文件 → 构建 `ReleaseTrackOptions` → 调用 `_run_release_track_worker()`

---

## 八、模板页面

| 模板文件 | 功能 |
|----------|------|
| release_track/index.html | SPA 追版界面：批次选择、项目勾选、运行监控、审批操作、MR 链接 |
| release_track/result.html | 旧版结果页：逐仓库预检查结果 + MR 创建操作 |

### index.html 前端逻辑
- `loadBatches()` — 加载可用批次
- `loadBatchDetail(batchId)` — 加载批次项目的追版状态
- `startRun()` — 收集勾选项目，启动追版
- `pollRun()` — 每秒轮询进度直到完成
- `approveRun(action)` — 执行审批操作
- 已完成的/已锁定的项目不可选择
- 标记为 0 的项目以红色高亮

---

## 九、权限

| 权限 Key | 说明 |
|----------|------|
| release_track_use | 访问追版功能 |

通过 `admin_required` 装饰器检查。所有 API 端点都需要 `release_track_use` 权限。

---

## 十、数据库迁移

| 迁移 | 内容 |
|------|------|
| 0001_initial | 创建 ReleaseTrackRun + ReleaseTrackRunItem |
| 0002 | 添加审批字段（approval_status 等） |
| 0003 | 添加 dry_run 字段 |

---

## 十一、测试

`tests.py` 包含基础的权限访问测试：
- 超级用户可以访问追版页面
- 普通员工不能访问

---

## 十二、依赖关系

- **branch_create** 应用：ReleaseBatch、ReleaseItem、ReleaseTrackOptions、ReleaseTrackService
- **accounts** 应用：权限检查、Git 运行时配置
- Django AUTH_USER_MODEL
