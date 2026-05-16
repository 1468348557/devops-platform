# 账户与权限 / Accounts 模块开发文档

## 一、模块概述

`accounts` 是 DevOps 平台的基础模块，负责**用户认证、角色权限管理（RBAC）、Git/SQL 平台配置管理**。所有其他应用（branch_create、release_track、sql_execute）的权限检查和 Git 操作都依赖本模块。

核心功能：
1. 用户注册/登录/注销
2. 基于角色的访问控制（Role-Based Access Control）
3. 用户审批工作流
4. Git 平台及 SQL 数据库的中央配置管理
5. 字段级和数据范围级权限控制

---

## 二、数据模型（models.py）

### 2.1 RoleDefinition — 角色定义

| 字段 | 类型 | 说明 |
|------|------|------|
| key | CharField(32) unique | 角色标识，如 `ops`、`developer`、`qa_tester` |
| name | CharField(64) unique | 角色显示名称 |
| is_system | BooleanField | 是否系统角色（不可禁用） |
| enabled | BooleanField | 是否启用 |
| can_be_registered | BooleanField | 注册时是否可选 |
| is_staff_role | BooleanField | 审批通过后是否授予 `is_staff` |
| created_at / updated_at | DateTimeField | 时间戳 |

类方法：
- `get_by_key(key)` — 按 key 查找角色
- `get_default_role()` — 优先返回 Developer，否则返回第一个启用的角色

### 2.2 UserProfile — 用户配置扩展

通过 `OneToOneField` 扩展 Django 内置 User 模型。

| 字段 | 类型 | 说明 |
|------|------|------|
| user | OneToOneField(User) | 关联用户 |
| role | FK → RoleDefinition | 所属角色 |
| approval_status | CharField(16) | `pending` / `approved` / `rejected` |
| approved_by | FK → User | 审批人 |
| approved_at | DateTimeField | 审批时间 |
| rejection_reason | CharField(255) | 拒绝原因 |

审批状态流转：
- **pending（待审批）**：注册后的初始状态，此状态下无法登录
- **approved（已通过）**：审批通过，可正常使用平台
- **rejected（已拒绝）**：审批拒绝，无法登录

### 2.3 GitPlatformConfig — Git 平台 / SQL 数据库配置

单例模式（`singleton_key=1 unique`），全局仅一行配置。

**Git 配置字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| git_base_url | CharField(200) | GitLab 基础 URL |
| git_group | CharField(64) | GitLab 组名 |
| work_base_dir | CharField(255) | Git 仓库本地工作目录 |
| git_username | CharField(64) | Git 认证用户名 |
| git_password | CharField(128) | Git 认证密码 |
| git_pat | CharField(128) | GitLab Personal Access Token |

**SQL 配置字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| sql_repo_path | CharField(255) | SQL 脚本仓库本地路径 |
| sql_repo_clone_url | CharField(255) | SQL 仓库克隆 URL |
| sql_db_host | CharField(128) | MySQL 主机 |
| sql_db_port | IntegerField(3306) | MySQL 端口 |
| sql_db_name | CharField(64) | 数据库名 |
| sql_db_user | CharField(64) | 数据库用户 |
| sql_db_password | CharField(128) | 数据库密码 |
| sql_keyword_ddl | CharField(64) | DDL 脚本关键字，默认 `ddl` |
| sql_keyword_backup | CharField(64) | 备份脚本关键字，默认 `backup,bak,备份` |
| sql_keyword_execute | CharField(64) | 执行脚本关键字，默认 `execute,执行` |
| sql_keyword_rollback | CharField(64) | 回滚脚本关键字，默认 `rollback,回滚` |
| sql_auto_approve_order | CharField(128) | 自动审批顺序规则，默认 `backup,ddl,execute,rollback` |

类方法：
- `get_solo()` — `get_or_create` 获取单例
- `get_solo_safe()` — 安全获取，数据库未就绪时返回默认对象（`_db_ready=False`）
- `mask_secret(value)` — 脱敏显示：保留前 2 + 后 2 字符，中间用 `*` 填充

### 2.4 RolePermissionPolicy — 角色权限策略

通过 `OneToOneField` 关联 RoleDefinition。

**菜单权限（8 个布尔值）：**

| 字段 | 对应功能 |
|------|----------|
| menu_release_track | 投产追版 |
| menu_branch_create | 查询所有新建分支 |
| menu_release_entry | 投产征集 |
| menu_hobo_ledger | HOBO 需求登记 |
| menu_sql_execute | SQL 执行申请 |
| menu_admin_config | 管理员配置（仅超管可访问） |
| menu_notification | 通知铃铛 |
| menu_export_schedule | 定时导出配置 |

**操作权限（19 个布尔值）：**

| 分组 | 字段 | 说明 |
|------|------|------|
| 投产追版 | action_release_track_use | 使用追版功能 |
| 分支创建 | action_branch_task_preview | 查看分支预览 |
| | action_branch_task_execute_release | 执行投产分支创建 |
| | action_branch_task_execute_hobo | 执行需求分支创建 |
| | action_schedule_manage | 管理计划任务 |
| 投产征集 | action_release_batch_manage | 管理投产批次 |
| | action_release_item_create | 创建投产行项 |
| | action_release_item_edit_others | 编辑他人行项 |
| | action_release_item_delete_own | 删除自己的行项 |
| | action_release_entry_export | 导出投产征集 |
| HOBO | action_hobo_item_create | 创建登记 |
| | action_hobo_item_edit_own | 编辑自己登记 |
| | action_hobo_item_edit_others | 编辑他人登记 |
| | action_hobo_item_delete_own | 删除自己登记 |
| | action_hobo_ledger_export | 导出台账 |
| | action_auto_export_hobo_ledger | 定时导出 HOBO |
| | action_auto_export_release_entry | 定时导出投产征集 |
| SQL | action_sql_repo_sync | 同步 SQL 仓库 |
| | action_sql_request_apply | 创建 SQL 申请 |
| | action_sql_request_approve | 审批 SQL 申请 |
| | action_sql_request_edit_others | 编辑他人申请 |
| | action_sql_request_delete | 删除 SQL 记录 |

**字段级权限：**

| 字段 | 说明 |
|------|------|
| release_entry_editable_fields | JSONField，该角色可编辑的投产征集字段键列表 |

系统始终强制执行的操作（不受管理员配置控制）：
- `release_item_delete_own` — 始终允许
- `hobo_item_edit_own` — 始终允许
- `hobo_item_delete_own` — 始终允许

**数据范围：**

| 字段 | 选项 | 说明 |
|------|------|------|
| data_scope_release_entry | SELF / ALL | 投产征集数据范围 |
| data_scope_hobo_ledger | SELF / ALL | HOBO 台账数据范围 |
| data_scope_sql_requests | SELF / ALL | SQL 申请数据范围（默认 SELF） |

---

## 三、权限系统（permissions.py）

### 公共 API

| 函数 | 说明 |
|------|------|
| `can_access_menu(user, menu_key)` | 检查用户是否能访问某菜单 |
| `can_do_action(user, action_key)` | 检查用户是否能执行某操作 |
| `get_data_scope(user, scope_key)` | 获取用户数据范围（`all` / `self`） |
| `apply_data_scope(queryset, user, scope_key, owner_field)` | 对 queryset 应用数据范围过滤 |

### 权限检查逻辑

1. **超管**：所有检查直接返回 True，数据范围始终为 ALL
2. **普通用户**：
   - 验证用户已认证
   - 验证配置文件 `approval_status == approved`
   - 读取角色关联的 `RolePermissionPolicy`
   - 检查对应字段的值

---

## 四、Git 运行时配置（services/git_settings.py）

### RuntimeGitSettings 数据类

提供所有 Git 操作的运行时配置。

| 属性/方法 | 说明 |
|-----------|------|
| parsed_base_url | URL 解析结果 |
| host | Git 服务器主机名 |
| work_base_path | 工作目录路径 |
| resolve_writable_work_base_path() | 尝试使用配置目录，不可写则回退到 `.runtime/repos/` |
| preferred_auth() | 返回认证方式：`pat` / `basic` / `none` |
| with_credentials_url(project) | 构建带凭证的远程 URL |
| repo_url(project) | 构建不带动词的远程 URL |
| git_auth_config_args() | 返回 `-c http.extraHeader=...` 参数列表 |
| masked_remote_url(project) | 带凭证脱敏的 URL |

### 配置读取优先级

`get_runtime_git_settings()` 读取逻辑：
1. 从 `GitPlatformConfig` 数据库记录读取
2. 用环境变量回退补充（`GIT_BASE_URL`、`GIT_HOST`、`GIT_GROUP`、`GIT_USERNAME`、`GIT_PASSWORD`、`GIT_PAT`）

### 敏感信息脱敏

`scrub_sensitive_text(text)` 移除日志中的：
- URL 中的凭证（`https://user:pass@host`）
- `glpat-*` PAT 令牌
- `token=` / `password=` 参数

---

## 五、字段元数据（role_meta.py）

### 投产征集字段定义

- `RELEASE_ENTRY_FIELD_OPTIONS` — 32 个 `(key, label)` 元组
- `RELEASE_ENTRY_DEV_FIELD_KEYS` — 29 个研发字段键集合
- `RELEASE_ENTRY_OPS_FIELD_KEYS` — 2 个运维字段键集合：`rel_deployed`、`deploy_status`
- `DEFAULT_RELEASE_ENTRY_FIELDS_BY_ROLE_KEY` — 角色默认字段集

---

## 六、视图（views.py）

### 6.1 用户认证

| 视图 | URL | 说明 |
|------|-----|------|
| UserLoginView | `/login/` | 登录（验证审批状态） |
| UserLogoutView | `/logout/` | 注销（允许 GET） |
| RegisterView | `/register/` | 注册（创建 PENDING 状态 UserProfile） |

登录流程：
1. 正常 Django 认证
2. 超管直接通过
3. 非超管必须 `profile.approval_status == approved` 才能登录
4. 待审批/已拒绝状态显示相应错误信息

### 6.2 功能页面

| 视图 | URL | 权限 | 说明 |
|------|-----|------|------|
| dashboard | `/` | 全部 | 工作台首页，7 个功能卡片 + 通知铃铛 |
| my_password | `/my-password/` | 已认证 | 修改密码 |
| approval_list | `/approval/` | staff角色 | 用户审批管理 |
| admin_config | `/admin-config/` | 仅超管 | 全局管理配置 |
| role_permissions_config | `/role-permissions/` | 仅超管 | 角色权限策略配置 |

### 6.3 API

| 视图 | URL | 说明 |
|------|-----|------|
| notification_counts | GET `/api/notification/` | 通知铃铛数据（未建分支数+未审批SQL数） |
| list_managed_users | GET `/api/users/` | 已注册用户列表（分页+搜索） |
| approval_bulk_action | POST `/approval/bulk-action/` | 批量审批 |

### 6.4 admin_config 子操作

通过 POST `action` 参数分派：

| action 值 | 说明 |
|-----------|------|
| change_password | 修改用户密码 |
| save_project | 新增/编辑 ProjectCatalog 工程 |
| bulk_save_projects | 批量保存工程（全量替换） |
| save_sql_config | 保存 SQL 数据库配置 |
| save_git_config | 保存 Git 平台配置 |
| delete_project | 删除工程 |
| update_user_account | 更新用户账号（角色/password/is_staff） |

---

## 七、URL 路由（urls.py）

| URL | 名称 | 视图 |
|-----|------|------|
| `/` | dashboard | dashboard |
| `/login/` | login | UserLoginView |
| `/logout/` | logout | UserLogoutView |
| `/register/` | register | RegisterView |
| `/approval/` | approval_list | approval_list |
| `/approval/<profile_id>/action/` | approval_action | approval_action |
| `/approval/bulk-action/` | approval_bulk_action | approval_bulk_action |
| `/admin-config/` | admin_config | admin_config |
| `/api/notification/` | notification_counts | notification_counts |
| `/api/users/` | list_managed_users | list_managed_users |
| `/role-permissions/` | role_permissions_config | role_permissions_config |
| `/my-password/` | my_password | my_password |

---

## 八、模板页面

| 模板文件 | 功能 |
|----------|------|
| accounts/login.html | 登录页，用户名+密码表单 |
| accounts/register.html | 注册页，用户名+邮箱+角色选择 |
| accounts/dashboard.html | 工作台首页，功能卡片+通知铃铛 |
| accounts/approval_list.html | 审批管理：筛选、表格、批量审批、拒绝原因 |
| accounts/my_password.html | 密码修改表单 |
| accounts/admin_config.html | 管理员配置：工程管理+Git配置+SQL配置+账号管理 |
| accounts/role_permissions_config.html | 角色权限策略：创建角色、菜单权限、操作权限、字段编辑权限、数据范围 |

### dashboard 通知铃铛

每 60 秒轮询 `/api/notification/`，显示：
- 未创建分支数（HOBO + 投产征集）
- 未审批 SQL 数

### admin_config 工程管理

- 模态弹窗编辑器，支持添加/编辑/删除行
- 通过 JSON 载荷批量提交（先删后建语义）
- `project_code` + `project_name` + `enabled`

### admin_config SQL 规则构建器

- 拖拽式阶段顺序编辑器
- 管理员可创建多条规则，每条规则定义备份/DDL/执行/回滚的顺序

### role_permissions_config

- **角色管理**：创建/编辑角色，设置启用/可注册/is_staff
- **权限策略**：每个角色可折叠的权限网格
  - 8 个菜单权限复选框
  - 19 个操作权限复选框（分组显示）
  - 字段编辑权限：打开模态弹窗从 32 个字段中选择
  - 3 个数据范围选择器

---

## 九、数据库迁移历史

| 迁移 | 内容 |
|------|------|
| 0001 | 初始模型创建 |
| 0002 | UserProfile 增加 approval_status 和 approved_at |
| 0003 | GitPlatformConfig 模型创建 |
| 0004 | GitPlatformConfig 增加 SQL 字段 |
| 0005 | RolePermissionPolicy 模型创建 |
| 0006 | RoleDefinition 模型创建 |
| 0007 | RolePermissionPolicy 增加 release_entry_editable_fields |
| 0008 | SQL 关键字字段移到 GitPlatformConfig |
| 0009 | GitPlatformConfig 增加 sql_keyword_execute |
| 0010 | RolePermissionPolicy 增加 sql_auto_approve |
| 0011 | GitPlatformConfig 增加 sql_auto_approve_order |
| 0012 | 增加 menu_notification |
| 0013 | 增加 release_entry_export 权限 |
| 0014 | 增加 hobo_ledger_export 权限 |
| 0015 | 增加自动导出权限 |
| 0016 | 增加 menu_export_schedule |
| 0017 | 增加 sql_request_delete 权限 |

---

## 十、架构要点

1. **RBAC 设计**：权限属于角色而非用户。超管绕过所有检查。
2. **审批工作流**：注册 → PENDING → staff 角色审批 → approved → 登录使用
3. **数据范围**：`apply_data_scope()` 被所有列表视图使用，支持 SELF/ALL 两种模式
4. **单例配置**：`GitPlatformConfig` 使用 `singleton_key=1` 单例模式
5. **字段级权限**：投产征集 32 个字段按角色分配可编辑子集
6. **密码/凭证管理**：配置留空表示不修改，输入表示更新，提供清空复选框
7. **系统角色保护**：`is_system=True` 的角色不可禁用
8. **通知跨应用**：通知 API 读取 branch_create 和 sql_execute 的数据
