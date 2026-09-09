# 平台化企业智能问数工作台

> 课程项目主 README · 简体中文 · 覆盖：启动步骤、账号说明、测试数据导入、主要功能、已知限制。
> 架构与运行方式总纲见仓库内 **Agent设计文档.md**，dsh 底座可行性源码级查证见 **dsh门户可行性验证报告.md**；数据字典与埋点见各脚本/建表文件头注释（本 README 不再重复长篇设计说明）。

## 1. 项目简介

一句话：**企业数据系统只管实现标准 MCP 接口（tools/list、tools/call），用户在"一个统一问数入口"用自然语言提问，即可跨系统拿全所有想查的数据**——不知道数据在哪张表、不知道 SQL 怎么写、甚至不知道数据在哪个系统，都能问。

系统定位是课程题目要求的 **"平台化但非全能"**：平台化的硬边界（语义层、权限、受控 SQL、审计）由各后端自治保证，模型与工具调用无法绕过；同时诚实声明边界内暂不支持的复合/纠偏问法、非法输入校验等（见 §9）。

本次交付两个可插拔 MCP 后端（均 Python FastMCP 2.x，stdio 运输）：

| 后端 | 业务域（满足"≥2 领域包"） | 独立数据底座 |
| --- | --- | --- |
| 后端① `bi_workbench` | 财务经营 + 项目交付 + LLM MaaS 治理（评分主体） | MySQL 库 `bi_workbench` |
| 后端② `hr_backend` | 人力域（演示"第二个任意后端"插拔） | MySQL 库 `hr_bi`（独立账号） |

## 2. 功能特性一览

| 功能（对应题目核心功能） | 落地形态（全部真实代码/工具） |
| --- | --- |
| 统一问数入口 + 多后端插拔 | 等价宿主 `dual_backends.py`（仓库内，推荐演示）/ 真 dsh 门户（`dsh-cordis.patch.yml` 挂接）；入口启动即对每后端 `tools/list` 动态能力发现，按问题语义自动路由，用户不选系统 |
| 两段式确认（硬性约束 3） | `prepare_query` 出草稿 SQL + 口径卡（不执行）→ 用户确认 → `execute_query` 对最终 SQL **再走一遍全链 hook** 后只读执行；draft/final 两次落审计 |
| 语义层指标治理 | bi：`metric_definition` 表 15 指标（口径/同义词/公式/血缘/权限标签）；hr：代码常量 6 指标；`get_metric_catalog` 可查目录；歧义（毛利 vs 净利润、离职数 vs 离职率）自动澄清，无指标/过宽口语拒答不编造 |
| 受控 SQL + 权限 + 审计（硬性约束 1/2/4） | 每个后端内部 hook 链：sqlglot AST 语法（仅单条 SELECT、禁写）→ 语义层指标标签 → 表白名单 + 行隔离 + 字段脱敏 → 行数 ≤5000 / 自动 LIMIT 500 / 执行时长上限 3000ms；业务查询走 DB 只读账号（bi_ro/hr_ro）兜底禁写；审计表 `audit_log` / `hr_audit` 记录 question、draft/final SQL、拦截原因 |
| 细粒度数据权限 | 角色→表/指标矩阵、行隔离（pm 部门经理自动注入 `dept`、hr_mgr 强制限定本部门）、字段脱敏（pm 看 `project.client` 打码）、薪资 `restricted.salary` 仅管理员 |
| 凭据安全（会话层红线） | 登录统一走 `login_vault(业务别名)`，明文密码只存凭据库、永不进对话；`change_password` 改密后旧会话全部失效且响应不回显密码 |
| 异常洞察与预警 | `run_alert_scan`（环比暴跌/预算偏差/延期风险/成本突增）与 `run_hr_scan`（离职率突增）规则扫描写站内告警，`list_alerts` 消息中心 |
| 经营快报 | `gen_report`（daily/weekly/period，仅 finance/admin）生成 markdown 快报 |
| 受控导出 | `export_query` 导出 CSV（先过全链 hook，仅 SELECT ≤5000 行），`export_log` 留痕（谁/何时/行数/content_md5），`list_exports` 仅管理员 |
| 数据接入（领域包三） | `etl_ingest` 将 CSV/Excel/MySQL/模拟 API 外部数据清洗归一接入 `llm_usage`（仅 finance/admin） |

## 3. 仓库结构

```
project1/
├─ bi_workbench/                     # 后端①（财务/项目/MaaS，FastMCP server）
│  ├─ backend/server.py              #    MCP 工具注册（15 个，见 Agent设计文档 契约）
│  ├─ backend/core.py                #    语义层/hook 链/审计/告警/快报/ETL 核心
│  ├─ run_server.py                  #    stdio 启动入口
│  ├─ sql/schema.sql                 #    建库 bi_workbench + bi_ro/bi_app 账号 + 指标表
│  ├─ scripts/init_db.py             #    一键初始化（schema.sql + seed.py 造数）
│  ├─ scripts/seed.py                #    确定性造数（时点 2026-08-31，含 F1/F2/P1/M1 埋点）
│  ├─ scripts/dual_backends.py       #    等价 dsh 宿主：一进程并发挂双后端，推荐只读演示
│  ├─ scripts/smoke_mcp.py / smoke_ext.py / etl_samples.py / verify_seed.py
│  └─ samples/                       #    演示样例数据（etl_samples.py 生成）
├─ hr_backend/                       # 后端②（人力域，独立库/独立账号，演示第二后端插拔）
│  ├─ server.py                      #    MCP 工具注册（10 个）+ HR 语义层常量
│  ├─ db_init.py                     #    建库 hr_bi + hr_ro/hr_app 账号 + 造数（H1 埋点）
│  └─ smoke_hr.py                    #    HR 后端冒烟
├─ eval/                             # 评测（自动 47 条 + UI 用例，≥30 要求）
│  ├─ cases.py                       #    47 条自动 + 7 条 UI 用例（正常/模糊/冲突/越权/注入/异常/SQL安全）
│  ├─ runner.py                      #    以 stdio 真实拉起双后端执行并生成报告
│  └─ result.json                    #    最近一次运行结果（47/47 PASS）
├─ dsh-cordis.patch.yml              # 真 dsh 门户双后端挂接补丁模板（{{ROOT}} 占位，见 §5）
├─ deployment/dsh-home/              # dsh 门户运行时模板（profile web / agent-presets / skills）
│                                    #   setup.bat 会自动装配到 %USERPROFILE%\.dsh
├─ requirements.txt                  # Python 依赖锁定（重建 venv 用，见 §4）
├─ setup.bat / setup.ps1             # 一键入口：产品模式 `setup.bat -Product` 直达浏览器门户（见 §5）
├─ dsh门户可行性验证报告.md          # dsh 底座可行性源码级查证
└─ Agent设计文档.md                  # 交互与实现纪要
```

> 说明：`.venv`、`.runtime`、`.pnpm-*`、`node_modules` 等运行产物不入库，执行 `setup.bat` 会自动重建/补装；等价宿主与评测（无需 Node/门户）clone 后即可跑。

## 4. 环境要求

| 依赖 | 版本/说明 | 备注 |
| --- | --- | --- |
| Python | 3.12+（推荐 3.13，仓库用 `bi_workbench\.venv`） | 可用 `setup.bat` 一键重建/复用 venv |
| MySQL | 8.x，本机 `127.0.0.1:3306`，需 root 密码做一次性初始化 | 业务运行只使用只读账号，root 仅初始化 |
| Node / pnpm（可选） | Node `^22.19` 或 `>=24` + pnpm，仅"真 dsh 门户"需要 | 等价宿主 `dual_backends.py` 演示不需要 Node，也不需要 LLM API Key |

**Python 依赖（已提供 `requirements.txt` 锁定版本）**：`fastmcp==2.14.7`、`mcp==1.30.0`、`pymysql==1.2.0`、`sqlglot==30.18.0`、`openpyxl==3.1.5`。重建 venv 一条命令：

```powershell
python -m venv .\bi_workbench\.venv
& .\bi_workbench\.venv\Scripts\python.exe -m pip install -r .\requirements.txt
```

> 一键完成「venv 复用/重建 + 依赖安装 + MySQL 双库建库造数 + dsh 门户运行时装配」可运行仓库根的 **`setup.bat`**（详见 §5）。脚本内所有路径按自身位置推导，**可整体拷贝到任意目录/机器后直接运行**。

## 5. 快速开始（可照做跑通）

以下命令假定在**仓库根目录**执行；用 `<py>` 指代 `.\bi_workbench\.venv\Scripts\python.exe`（`setup.bat` 第 1 步会创建/复用该 venv）。

### 第 0 步（推荐）：一键到门户 —— 当产品用

**产品用法（演示/交付主推）**：双击仓库根 **`setup.bat`** 后在菜单选 **[F]**；或无人值守一条命令（自动补环境/MySQL、装完直接拉起门户并打开浏览器）：

```powershell
.\setup.bat -Product
```

脚本自动完成：**环境体检 → venv 复用/重建 + 依赖安装 → 双库建库造数（已就绪自动跳过）→ dsh 门户装配与依赖自举 → 后台启动图形门户 → 自动打开浏览器**。把整个目录拷到任意机器，双击或一条 `-Product` 即可演示到"浏览器门户"，无需手敲建库/装配/启动命令。

> 幂等：重复运行 `-Product` 会识别已装配的 `~/.dsh`（标记 `.setup-assembled`）与已初始化的双库（`bi_ro`/`hr_ro` 只读探活）而自动跳过，不反复打扰、不覆盖正在运行的门户；强制刷新配置用 `-ResetProfile`。

**环境识别（不以 PATH 命令为准，装了服务但无命令行工具也不会误判）：**
- Python：依次找 PATH `python` → `py` 启动器 → 用户级常见安装目录；找不到且加了 `-AutoInstall`，则自动下载官方 python.org 安装器（Python 3.13）并静默安装（无需 winget）；
- MySQL：**TCP 探测 127.0.0.1:3306**（服务在跑即算可用）；不通且加了 `-AutoMySQLZip`，则自动下载官方 `mysql-8.0.45-winx64.zip`（CDN 直链 + 国内镜像多路重试，`dev.mysql.com/get` 网关常 403 故仅作兜底）到仓库 `.runtime\`，本地初始化（root 空密码）并后台启动，可自举演示；
- node / pnpm：仅 dsh 图形门户相关；`-Product` 模式下会自动下载官方 nodejs.org LTS 便携 zip（Node v24，解压到 `.runtime\nodejs`，无需管理员）→ npm 装 pnpm → 在装配的 dsh profile 上 `pnpm install`（需联网）→ 即可 `pnpm dsh web`；
- winget：仅作官网下载失败时的兜底通道（非必需）；便携 node/pnpm 仅在本脚本会话内临时加入 PATH。

参数速查（详见 setup.bat 头注释）：`-Product`（一键产品：补环境+装配+后台启动门户并开浏览器）、`-ResetProfile`（强制重建 `~/.dsh/profiles/web`）、`-AutoInstall`、`-AutoMySQLZip`、`-MySQLRootPassword xxx`、`-SkipDB`、`-SkipPortal`（跳过门户装配与依赖自举）、`-Verify`。等效一句话：**全新机器 = 双击 `setup.bat` 选 [F]，或一条 `setup.bat -Product`，仅剩"网络/首次下载"这一个外部依赖；若只做命令行演示（等价宿主/评测、不拉门户）则加 `-SkipPortal` 即可。**

### 第 1 步：初始化 MySQL 双库并造数（root 仅此一步）

（若已用 `setup.bat` 完成可跳过本步）

```powershell
# ① 后端①：执行 sql/schema.sql 建库 bi_workbench + 账号(bi_ro/bi_app)，再自动跑 seed.py 造数
& "<py>" .\bi_workbench\scripts\init_db.py --password <你的MySQL root密码>
# 输出末尾："初始化完成 ✔ 用只读账号 bi_ro 即可连接"

# ② 后端②：建独立库 hr_bi + 账号(hr_ro/hr_app) + 造数（幂等）
& "<py>" .\hr_backend\db_init.py --password <你的MySQL root密码>
# 输出："hr_bi OK"
```

两个脚本均幂等（seed 先清空业务表再插入），可重复执行。库表/账号/造数细节见 `bi_workbench\sql\schema.sql`、`bi_workbench\scripts\seed.py`、`hr_backend\db_init.py`。

### 第 2 步（可选）：校验造数埋点

```powershell
& "<py>" .\bi_workbench\scripts\verify_seed.py   # 用只读账号 bi_ro 核对表行数与 F1/F2/P1 埋点命中
```

### 第 3 步：启动统一问数入口

**方式 A（推荐，只读演示，无需 Node/API Key）——等价宿主 `dual_backends.py`**：一个进程同时以 stdio 拉起两个后端，模拟 dsh"工具发现 → 路由 → prepare → 确认 → execute"全闭环：

```powershell
& "<py>" .\bi_workbench\scripts\dual_backends.py
# 尾部输出：==== DUAL-BACKEND(等价dsh宿主): PASS ====
```

**方式 B——真 dsh 门户（图形化，UI 用例 U01–U03 已通过）**：需在 dsh 底座工程目录中运行（需 Node/pnpm）。挂接补丁见仓库根 `dsh-cordis.patch.yml`（模板用 `{{ROOT}}` 占位，`setup.bat` 第 3 步会自动替换为实际根路径并生成 `.runtime\dsh-cordis.generated.yml`）：

```powershell
pnpm dsh web --patch <把 dsh-cordis.patch.yml 中 {{ROOT}} 替换为仓库绝对路径后的补丁>
```

> 真 dsh 门户运行前置：dsh profile 需已装配插件与运行时（`setup.bat` 第 3 步自动把 `deployment/dsh-home` 装配到 `%USERPROFILE%\.dsh`），agent preset 默认 `enterprise-qna`（挂接 skill 通道 + genui 渲染）。

两个后端也可各自作为独立 stdio MCP server 被任意 MCP 客户端挂接（dsh patch / 评测 runner 均按此拉起）：

```powershell
& "<py>" .\bi_workbench\run_server.py        # 后端①（cwd=bi_workbench）
& "<py>" -m hr_backend.server                 # 后端②（cwd=仓库根）
```

### 第 4 步：问数示例（真实问法）

| 提问（任一） | 路由后端 | 预期（评测/造数实测结果） |
| --- | --- | --- |
| `华北区2026-08预算偏差最大的客户是谁` | bi（财务） | 恒信科技，预算偏差率 -28.33%（UI 用例 U02 已跑通） |
| `交付一部下月延期风险项目数` | bi（项目） | 命中 P1 埋点，返回风险项目数（N06 PASS） |
| `哪个部门本月模型成本增长最快` | bi（MaaS） | 交付二部（M1 埋点，成本增速最快） |
| `客服部2026-08离职率是多少` | hr | 9.54%（N08/U01 执行闭环） |
| `今年各部门离职人数` | hr | 4 部门离职人数（demo 全闭环） |

演示路径：提问 → 后端 `prepare_query` 返回 **草稿 SQL + 口径卡**（含 hook 日志）→ 展示供用户确认 → 确认后 `execute_query` 返回表格结果 + 口径说明 + 最终 SQL + 图表类型建议（渲染交给前端）。

## 6. 账号说明

> ⚠️ 以下为 **seed/db_init 写死的演示账号与明文密码**，仅用于教学/评测演示；生产部署应改用 `login_vault` 凭据库 + 强密码（`change_password` 工具已实现），并修改 `schema.sql` / `db_init.py` / `backend/core.py` / `hr_backend/server.py` 中的 DB 账号密码。

### 6.1 业务账号（对应业务别名，登录推荐走 `login_vault(别名)`，密码永不出后端）

**后端① bi（`sys_user`，来源 `bi_workbench/scripts/seed.py`）**

| 业务别名 | user_id | 姓名 | 角色/部门 | 明文密码 | 权限摘要 |
| --- | --- | --- | --- | --- | --- |
| bi-admin | admin | 赵子昂 | admin / 信息中心 | `admin123` | 全域 + 审计/导出日志 + ETL + 经营快报 |
| bi-finance | zhangmin | 张敏 | finance / 财务部 | `123456` | finance.* + 项目域（基本）+ MaaS；审计/导出不可见 |
| bi-pm1 | liqiang | 李强 | pm / 交付一部 | `123456` | 仅 project.*；自动注入本部门行隔离；client 脱敏打码；财务指标拒绝 |
| bi-pm2 | wangfang | 王芳 | pm / 交付二部 | `123456` | 同 bi-pm1（另一部门） |
| bi-staff | xiaowang | 王小虎 | staff / 市场部 | `123456` | 仅 MaaS 域（public.maas）+ 基础 |

**后端② hr（`hr_user`，来源 `hr_backend/db_init.py`）**

| 业务别名 | user_id | 姓名 | 角色/部门 | 明文密码 | 权限摘要 |
| --- | --- | --- | --- | --- | --- |
| hr-admin | hr_admin | 刘主任 | hr_admin | `hr_admin123` | 全量 HR 指标，含薪资表（restricted.salary） |
| hr-mgr-dev | zhaoliu | 赵六 | hr_mgr / 研发 | `123456` | 仅本部门月度在编表（行隔离）；薪资不可见；hr_user 等表不可读 |
| hr-mgr-sales | qianqi | 钱七 | hr_mgr / 销售 | `123456` | 同 hr-mgr-dev（销售部） |

### 6.2 MySQL 连接账号（只读兜底 / 应用写账号，密码写死在脚本与配置中）

| 账号 | 密码 | 权限 | 用途 | 定义位置 |
| --- | --- | --- | --- | --- |
| bi_ro | `bi_ro_pass_2026` | SELECT `bi_workbench`.* | 业务查询只读（DB 层禁写兜底） | `bi_workbench/sql/schema.sql` |
| bi_app | `bi_app_pass_2026` | SELECT 全库 + INSERT/UPDATE 仅 `audit_log`/`alert_message`/`llm_usage`/`export_log` | 审计/告警/用量/导出写入 | 同上 |
| hr_ro | `hr_ro_pass_2026` | SELECT `hr_bi`.* | HR 查询只读 | `hr_backend/db_init.py` |
| hr_app | `hr_app_pass_2026` | SELECT 全库 + INSERT/UPDATE 仅 `hr_audit`/`hr_alert` | HR 审计/告警写入 | 同上 |

## 7. 测试数据说明

- **造数入口**：`init_db.py`（后端①：内部先执行 `schema.sql` 再调 `seed.py`）、`db_init.py`（后端②）。均为确定性 seed（`random.seed(20260831)` / `random.seed(20260901)`），幂等可重跑。
- **数据时点**：最近完整月 = **2026-08**（上月 2026-07）；月窗口 2024-09 ~ 2026-08（24 个月）；"下月延期风险" = `plan_end` 落在 2026-09。
- **后端①（库 `bi_workbench`）**：`finance_sales` 4 区域 × 20 客户 × 24 月；`finance_expense` 3 费用类型 × 区域 × 月；`project_info` 40 个（24 已完成 + 16 进行中，其中 6 个 9 月到期）；`project_milestone` 按里程碑造数；`metric_definition` 15 指标；`llm_usage` 预埋 21 天 × 6 部门；`sys_user` 5 账号。
- **后端②（库 `hr_bi`）**：`monthly_headcount` / `monthly_payroll` 4 部门 × 24 月；`hr_user` 3 账号；`hr_audit` / `hr_alert` 审计与告警。
- **确定性演示埋点**（保证问数/告警演示稳定命中）：F1 中科智达（华南）2026-08 收入骤降至 22% 且负毛利 → 环比暴跌告警；F2 恒信科技/蓝海实业/云帆物流 实际毛利持续低于预算（-25%~-40%）；P1 三个进行中项目（研发/交付一/交付二）9 月到期且含"进行中-延期"里程碑；M1 `llm_usage` 构造"交付二部本周成本增速最快"；H1 客服部 2026-07/08 离职率突增（9%/11%）→ HR 预警。
- **指标口径**：以 `metric_definition` 表 / HR 语义层常量为准，明细口径见 `bi_workbench/backend/core.py` 与 `hr_backend/server.py`。
- 完整数据字典/脱敏说明见 `bi_workbench/sql/schema.sql` 各表注释与 `seed.py` 头部注释。

## 8. 评测与回归

评测以 **MCP stdio 真实拉起** 双后端（非 mock），登录统一走 `login_vault(别名)`（评测代码不接触明文密码），逐条断言后自动重写报告。

```powershell
# 前置：MySQL 已起、两库已 init+seed
& "<py>" .\eval\runner.py
# 输出：==== 评测完成: 47/47 PASS (100.0%) ====
# 生成：eval/result.json
```

**最近一次结果**（见 `eval/result.json`）：自动用例 **47/47 PASS（100%）**；UI 人工复核用例 7 条（U01–U07）；合计 54 条 ≥ 30。场景覆盖：正常 11、模糊/歧义 7、冲突/多意图 4、越权/权限 8、提示注入 6、异常输入 5、SQL 写阻断 6；题目硬性约束 1–4 全部由用例覆盖通过。

**单后端/扩展冒烟脚本**（回归绿）：

| 脚本 | 覆盖 | 说明 |
| --- | --- | --- |
| `bi_workbench/scripts/smoke_mcp.py` | tools/list→登录→目录→歧义澄清→受控执行 | 在 `bi_workbench` 目录执行 |
| `bi_workbench/scripts/etl_samples.py` | 生成 ETL 演示样例 `samples/llm_usage_*.csv/.xlsx/.json` | 先跑它再跑 smoke_ext |
| `bi_workbench/scripts/smoke_ext.py` | 快报 / 导出留痕 / ETL(csv·excel·api) / 告警 / chart_type | `python scripts/smoke_ext.py` |
| `hr_backend/smoke_hr.py` | 行隔离 / 薪资表权限 / 写阻断 / HR 预警 | 用 bi_workbench 的 venv 解释器运行 |

## 9. 已知限制与边界

**功能/语义层局限（评测非阻断观察项，边界内不改后端）：**
- C01：复合/纠偏问法会被静默解析为单指标（如"销售额和回款额，但我只要毛利"→ 按回款额出 SQL，否定短语不参与指标选择）——后续增强点；
- X04：非法月份（如 2026-99）未拒答，被静默兜底为库内最新月（2026-08）返回当月数据；
- 以上均落在受控 SELECT + 白名单内，无写/越权风险。

**UI 界面待补测：**
- U04 会话层凭据红线仅部分验证（索密试探待界面复测）；U05 改密码不回显+清理提醒待界面复测；U07 dsh-usage 用量/成本显示待重启验证；U06 已跑通但 BI 问数自动出图待演示。

**覆盖缺口：**
- CSV/Excel 接入（`etl_ingest`）评测未覆盖；演示样例可由 `scripts/etl_samples.py` 生成后自行验证；
- 未做并发/压测；数据源**当前仅 MySQL**（PostgreSQL 接入未实现）；
- 会话层（模型/提示词）防线依赖 dsh SKILL 与确认卡，需 U04–U07 闭环补测。

**部署现状（如实说明）：**
- 无 docker-compose、无公网测试部署——**本地可完整运行**（等价宿主/评测/冒烟均已本地验证；真 dsh 门户本机运行通过 UI 用例 U01–U03）；
- 本机一键入口：`setup.bat`——产品模式双击选 **[F]** 或 `setup.bat -Product`：补环境 → 双库建库造数（已就绪自动跳过）→ dsh 运行时装配 → **后台启动门户并自动打开浏览器**；幂等重跑不打扰（见 §5）。运行时目录模板 `deployment/dsh-home` 已随仓库提供，可拷到 `%USERPROFILE%\.dsh` 复现门户环境；
- 演示账号/DB 账号密码明文写死在脚本与配置中（见 §6 警示），仅教学用途，生产必须改造。

## 10. 仓库内文档索引

| 文档 | 说明 |
| --- | --- |
| [README.md](README.md) | 本文档：启动步骤、账号、测试数据、功能、已知限制 |
| [Agent设计文档.md](Agent设计文档.md) | 交互与实现纪要：宿主机制、角色卡、hook 链、评测证据 |
| [dsh门户可行性验证报告.md](dsh门户可行性验证报告.md) | dsh 底座多后端挂接/确认卡的源码级查证 |
| [dsh-cordis.patch.yml](dsh-cordis.patch.yml) | 真 dsh 双后端挂接补丁模板（`{{ROOT}}` 占位，`setup.bat` 自动替换） |
| `bi_workbench/sql/schema.sql` 等 | 数据字典与 F1/F2/P1/M1/H1 埋点细节，见各文件头注释 |
