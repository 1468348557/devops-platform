# SQL 执行 / SQL Execute 模块开发文档

## 一、模块概述

`sql_execute` 是 DevOps 平台中负责**SQL 脚本执行管理**的应用模块。研发人员从 Git 仓库中选择 SQL 文件提交执行申请，经审批后，后台自动连接 MySQL 数据库按顺序执行备份/DDL/核心/回滚四个阶段的脚本。

状态流转：`pending（待审批）` → `approved（审批通过）` → `running（执行中）` → `success（执行成功）` / `failed（执行失败）` / `rejected（审批拒绝）`

---

## 二、数据模型（models.py）

### SqlExecutionRequest — SQL 执行请求（唯一模型）

| 字段 | 类型 | 说明 |
|------|------|------|
| release_date | DateField | 投产日期 |
| folder_path | CharField(255) | Git 仓库中 SQL 目录的绝对路径 |
| selected_files_json | TextField | 选中 SQL 文件的 JSON 数组，默认 `"[]"` |
| status | CharField(16) | 见状态枚举 |
| execution_result | CharField(255) | 简短结果消息 |
| execution_tip | CharField(255) | 实时进度提示 |
| execution_log | TextField | 完整执行日志（截断至 100K） |
| approved_by | FK → User | 审批人（SET_NULL） |
| approved_at | DateTimeField | 审批时间 |
| requested_by | FK → User | 申请人（PROTECT） |
| executed_at | DateTimeField | 执行完成时间 |
| created_at / updated_at | DateTimeField | 时间戳 |

#### 状态枚举

| 枚举值 | 数据库值 | 显示 |
|--------|----------|------|
| PENDING | pending | 待审批 |
| APPROVED | approved | 审批通过 |
| RUNNING | running | 执行中 |
| REJECTED | rejected | 审批拒绝 |
| SUCCESS | success | 执行成功 |
| FAILED | failed | 执行失败 |

- `db_table = "sql_execution_request"`
- 排序：`-created_at, -id`

---

## 三、URL 路由（urls.py）

### 页面路由

| URL | 方法 | View | 说明 |
|-----|------|------|------|
| `/sql-execute/` | GET | sql_execute_page | 主页面（列表+筛选+操作） |

### Git 仓库 API

| URL | 方法 | View | 说明 |
|-----|------|------|------|
| `/sql-execute/api/repo/sync/` | POST | sql_repo_sync_api | 同步 Git SQL 仓库 |
| `/sql-execute/api/repo/folders/` | GET | sql_repo_folders_api | 列出日期下的 SQL 目录 |
| `/sql-execute/api/repo/files/` | GET | sql_repo_files_api | 列出目录中的 SQL 文件 |
| `/sql-execute/api/repo/file-preview/` | GET | sql_repo_file_preview_api | 预览 SQL 文件内容 |

### SQL 请求 API

| URL | 方法 | View | 说明 |
|-----|------|------|------|
| `/sql-execute/api/request/create/` | POST | sql_request_create_api | 创建执行申请 |
| `/sql-execute/api/request/progress/` | GET | sql_request_progress_api | 查询执行进度+日志 |
| `/sql-execute/api/request/file-preview/` | GET | sql_request_file_preview_api | 预览请求关联的 SQL 文件 |
| `/sql-execute/api/request/execution-detail/` | GET | sql_request_execution_detail_api | 读取完整的执行结果文件 |
| `/sql-execute/api/request/action/` | POST | sql_request_action_api | 审批/拒绝/撤回操作 |
| `/sql-execute/api/request/delete/` | POST | sql_request_delete_api | 单条删除 |
| `/sql-execute/api/request/batch-delete/` | POST | sql_request_batch_delete_api | 批量删除（按筛选） |
| `/sql-execute/api/request/auto-approve-all/` | POST | sql_request_auto_approve_all_api | 批量自动审批（全局禁用） |

---

## 四、核心业务逻辑

### 4.1 SQL 执行引擎（services.py）

#### execute_sql_request — 主编排函数

执行流程：

1. **加载配置**：从 `GitPlatformConfig` 读取数据库连接参数（host/port/user/password/db_name）
2. **验证 DB 配置**：所有必需字段必须已配置
3. **构建执行序列**：验证文件路径，确定每个文件的执行阶段
4. **阶段识别**：根据文件名关键字匹配四个阶段
   - `backup`：备份脚本（匹配 `backup`, `bak`, `备份`）
   - `ddl`：DDL 脚本（匹配 `ddl`）
   - `execute`：核心执行脚本（匹配 `execute`, `执行`）
   - `rollback`：回滚脚本（匹配 `rollback`, `回滚`）
   仅当文件名恰好命中一种阶段类型时分配，否则拒绝
5. **顺序验证**：根据管理员配置的提交顺序规则检查（默认 `backup → ddl → execute → rollback`）
6. **数据库连接**：通过 pymysql 建立连接，关闭 autocommit
7. **逐文件执行**：
   - 读取文件 → 分词（按分号分割语句）→ 逐条执行 → 记录结果 → 提交
   - 异常时 rollback 当前事务，不自动执行回滚脚本
8. **结果日志**：
   - SELECT/SHOW 语句以 "G 风格" 格式输出查询结果（最多 200 行，单格 ≤ 4000 字符）
   - DML/DDL 显示影响行数

#### _split_sql_statements — SQL 分词器

手动实现的状态机，正确处理：
- 字符串字面量（单引号、双引号、反引号）
- 单行注释（`--`、`#`）
- 多行注释（`/* */`）

分号在以上上下文中不会被当做语句分隔符。

### 4.2 机器审查（_machine_review_sql_files）

创建申请时自动执行，检查规则：

1. **UTF-8 编码**：所有文件必须有效 UTF-8
2. **非空**：文件不能为空
3. **阶段匹配**：每个文件必须匹配一种阶段类型
4. **必须包含执行和回滚**：至少 1 个 execute 脚本和 1 个 rollback 脚本
5. **USE 语句检查**（如配置了 db_name）：SQL 必须以 `use <dbname>;` 开头
   - 支持 BOM
   - 支持中文分号 `；`
   - 支持多行注释在 USE 之前
6. **DDL 规范**：建表语句必须使用 `IF NOT EXISTS`
7. **回滚规范**：删表语句必须使用 `IF EXISTS`

### 4.3 日志解析（_parse_sql_execution_log）

将执行日志文本解析为结构化的文件级分段：

- 识别 `[phase] 阶段名` 标签
- 解析 `开始执行` / `执行完成` / `无可执行语句，跳过` / `无匹配脚本，跳过` 标记
- 为每个文件条目附加 `g_fields`（阶段、文件名、结果、语句数、日志片段）
- 处理错误行关联到当前文件

### 4.4 后台执行流程

1. 用户创建申请 → `PENDING`
2. 审批人调用 `sql_request_action_api` → `approve`
3. 事务提交后触发 `_sql_execute_worker`（分离进程）：
   - 设置状态为 `RUNNING`
   - 调用 `execute_sql_request`，通过 `progress_callback` 实时写回 `execution_tip` 和 `execution_log`
   - 完成后写入结果文件到磁盘（`.runtime/sql_execute_results/`）
   - 设置最终状态 `SUCCESS` / `FAILED`

---

## 五、Git 仓库同步

### 同步流程（sql_repo_sync_api）

1. 从 `GitPlatformConfig` 读取 `sql_repo_path` 和 `clone_url`
2. 定位 `.git` 目录（父目录查找回退）
3. 如果目录为空：
   - 从 `clone_url` 克隆（带认证配置）
   - 纠正仓库名如果配置路径不匹配
4. 执行 `git fetch origin <branch>` → `git checkout <branch>` → `git pull --ff-only`
5. 同步的分支固定为 `rel执行且投产SQL`

### 目录/文件读取

- `_list_sql_directories_by_release_date`：列出 `release_date` 目录下包含 `.sql` 文件的子目录
- `_list_sql_files`：列出指定目录中的 `.sql` 文件
- 所有文件读取都有路径遍历保护（必须在 `_SQL_EXECUTION_RESULT_DIR` 或已选文件的文件夹范围内）

---

## 六、管理命令

### run_sql_execute_request

由 Web 端异步触发执行单个 SQL 请求。

参数：`request_id`（必需，整数）

逻辑：调用 `_sql_execute_worker(request_id)` 执行实际工作。

---

## 七、权限系统

### 操作权限

| 权限 Key | 说明 |
|----------|------|
| sql_request_apply | 创建 SQL 执行申请 |
| sql_request_approve | 审批 SQL 执行申请 |
| sql_repo_sync | 同步 SQL 仓库 |
| sql_request_edit_others | 编辑他人申请 / 查看进度 |
| sql_request_delete | 删除 SQL 执行记录 |

### 数据范围

列表视图通过 `apply_data_scope()` 控制可见范围。

### 菜单权限

通过 `can_access_menu(user, "sql_execute")` 控制访问。

### 特殊说明

- **自动审批已全局禁用**：`_can_auto_approve` 硬编码返回 `False`
- 超级用户拥有全部权限
- 审批人可拒绝/撤回申请

---

## 八、关键常量

| 常量 | 值 | 说明 |
|------|-----|------|
| `_MAX_EXECUTION_LOG_CHARS` | 100,000 | execution_log 截断限制 |
| `_MAX_SQL_PREVIEW_CHARS` | 200,000 | SQL 内容预览截断限制 |
| `_MAX_SQL_EXECUTION_DETAIL_CHARS` | 1,000,000 | API 读取结果文件截断限制 |
| `_SQL_EXECUTION_RESULT_DIR` | `/docker/devops/mysql/mysql-excuse` | 结果文件目录 |
| `_SQL_REPO_BRANCH` | `rel执行且投产SQL` | SQL 仓库分支 |
| `_MAX_QUERY_RESULT_ROWS` | 200 | 日志中最大结果行数 |
| `_MAX_QUERY_CELL_LEN` | 4,000 | 单元格值最大字符数 |
| `_MAX_QUERY_RESULT_LOG_CHARS` | 60,000 | 单条查询日志段大小限制 |

---

## 九、数据库迁移

| 迁移 | 内容 |
|------|------|
| 0001_initial | 创建 SqlExecutionRequest 表（5 个状态） |
| 0002 | 添加 running 状态 + execution_tip 字段 |

---

## 十、测试

`tests.py` 包含 6 个测试类，覆盖范围全面：

| 测试类 | 内容 |
|--------|------|
| SqlStatementResultLogTests | SELECT 日志 G 风格格式、DML 影响行数 |
| SqlStatementSplitTests | 字符串/注释中的分号不被分割 |
| SqlExecutionLogParserTests | 日志解析：空日志、跳过、ERROR 关联、G 字段、进度 API 集成 |
| NearestReleaseDateStrTests | 最近投产日期的智能匹配 |
| SqlExecutionOrderTests | 模拟 pymysql 的执行顺序 + 失败不回滚 |
| SqlMachineReviewTests | 机器审查：必需/可选字段、UTF-8、BOM、USE 语句、DDL/回滚规范 |
| SqlApprovalPermissionTests | 权限：ops 不能审批、repo_sync 权限、自动审批禁用、进度查看 |

---

## 十一、依赖关系

- **accounts** 应用：GitPlatformConfig（Git/DB 配置）、权限系统、Git 运行时配置
- **branch_create** 应用：ReleaseBatch（投产日期参照）
- **pymysql**：MySQL 数据库连接和执行
