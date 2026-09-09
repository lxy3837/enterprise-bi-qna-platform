# 平台化企业智能问数工作台 — Agent 设计文档

> 对应《要求.txt》§1.7（角色卡、职责、工具权限、输入输出、交接条件、人工审批点、失败降级策略）。
> 版本：v1.0（2026-09-09）｜文档类型：Agent 体系设计
> **代码即真**：本文所有角色卡、工具名、状态取值、审批点、降级行为均能从源码对应行号核对。证据文件：
> - 后端①（财务/项目/MaaS 域）：`bi_workbench/backend/server.py`、`bi_workbench/backend/core.py`
> - 后端②（人力域）：`hr_backend/server.py`
> - 会话层宿主与挂接：`dsh门户可行性验证报告.md`、`bi_workbench/scripts/dual_backends.py`、`session.v2.jsonl`
> - 系统要求与禁止项：《题目.txt》《要求.txt》《技术方案.md》

---

## 1. 设计总则

### 1.1 本系统的 Agent 体系是什么（先澄清定位）

**它不是"一个自由 Agent"，而是"LLM/规则 + MCP 工具 + 受控执行"的多角色协作体系**，刻意把"聪明的部分"和"危险的部分"分开：

| 层次 | 放什么 | 真实载体 | 有无自由裁量 |
| --- | --- | --- | --- |
| 会话层（入口） | 听懂自然语言、选后端、把结果讲给人听 | dsh（DeepSeek Harness）会话 Agent / 等价宿主 `dual_backends.py` | 有（仅限"说什么、调哪个后端工具"），**无写库/无系统命令能力** |
| 业务后端 | 语义层指标、SQL 生成、执行 | bi-workbench / hr-backend 两个自治 MCP server | **无**。SQL 由**规则引擎 + 语义层**确定性生成（`core.gen_sql`），LLM 不直接写库、不持有任何数据库连接串 |
| 防线层 | 语法/AST、语义层、权限/隔离/脱敏、行数/成本 | 后端执行路径内的 hook 链（`core.enforce_sql` + `prepare_query` 内权限预检） | 无（纯代码） |
| 审计层 | 谁、何时、问了什么、草稿/最终 SQL、拦截原因 | `audit_log` / `hr_audit` | 无（纯代码） |

一句话：**模型负责"人话 ↔ 受控动作"的翻译，后端负责"能不能做"的裁决，人负责"要不要做"的最终拍板。** 模型永远没有"自己把 SQL 跑掉"的路径——要执行任何查询都必须经过：`prepare_query`（出草稿，不执行）→ **人确认** → `execute_query`（对最终 SQL **重跑全链 hook** → 只读账号执行）。

### 1.2 为什么不能让它自由执行（呼应题目禁止项）

《题目.txt》硬性约束与《要求.txt》禁止性要求直接对应本设计的选择：

| 禁止/风险 | 本设计的对策 |
| --- | --- |
| 模型直接执行危险 SQL / 自动处置业务 | SQL 由后端规则引擎生成，仅 SELECT；`execute_query` 对**最终 SQL（含被用户改过的）**重跑全链 hook 才执行；连接账号是只读账号（`bi_ro`/`hr_ro`），root 仅建库/造数用（`core.py CONF`、`hr_backend/server.py RO/APP`） |
| 高影响决策缺人工复核 | **两段式确认**：`prepare_query → draft → 人工确认 → execute_query`（§4 A1）；歧义口径由人选定再继续（§4 A2） |
| 越权查询 / 口径错误 | 权限标签→角色、表白名单、行隔离（pm/hr_mgr 自动限本人部门）、字段脱敏（pm 看 client 打码）、语义层注册表（指标/口径/同义词/血缘），均在后端内部、非 LLM 判断 |
| 成本不可控 | hook④ 自动补 `LIMIT 500`、显式 `LIMIT`>5000 拒绝、结果截断 5000、执行时长 3000ms 熔断（`SET SESSION max_execution_time`） |
| 结果不可审计 | 拦截/澄清即落审计（bi 在 `prepare` 即写 clarified/denied/blocked；hr 于 `execute` 落），成功落 `approved`（同一条记录含 draft_sql 与 final_sql 双 SQL），另含 `row_count`/`elapse_ms`（§3.5、§8） |
| 编造无来源输出 | 指标必须命中语义层注册表才能生成 SQL；未命中返回 `none`/`denied` 拒答并给替代问法（评测 U03），后端从不在没有指标口径的情况下输出"业务结论" |

### 1.3 权限最小化与可追溯

- 每个后端自治：自己的库、只读账号、语义层、权限矩阵、审计、告警（技术方案 §2.3）。入口不感知后端内部，只做"能力发现 + 路由 + 确认卡"。
- 两个库（`bi_workbench` 与 `hr_bi`）无任何表关联；会话与草稿上下文（`SESSIONS`/`REQUESTS`）各自独立，跨后端不串扰（`dual_backends.py` 双账号并发回归为证）。
- 一切可追溯：问数（审计双 SQL）、导出（`export_log` 含内容 md5）、改密、ETL、报表均留痕。

---

## 2. 协作总览图

一次标准问数的角色交接与**两处人工点**（A1 执行前最终确认、A2 口径澄清）：

```mermaid
sequenceDiagram
    autonumber
    participant U as ①用户/提问者(人)
    participant A as ②入口会话Agent(dsh会话框架/等价宿主)
    participant B as ③后端问数执行体(bi-workbench / hr-backend)
    participant G as ④权限与安全守卫(hook链①②③④)
    participant L as ⑤审计员(audit_log/hr_audit)
    participant D as MySQL(只读账号 bi_ro/hr_ro)

    U->>A: 自然语言问题(不指定系统)
    A->>A: 能力发现(tools/list)+语义路由(财务/项目/MaaS→bi, 人力→hr)
    A->>B: prepare_query(session_token, question)
    B->>G: 语义层匹配 + 权限预检(hook2/3) + gen_sql规则出草稿
    alt 歧义
        G-->>B: clarified+options
        B-->>A: {status:clarified, options:[...]}
        A-->>U: 【次级人工点A2】请选择口径(selected_metric)
        U-->>A: 选择指标
        A->>B: prepare_query(..., selected_metric=...)
    else 无权/未识别
        G-->>B: denied/none
        B-->>L: 写审计(clarified/denied/blocked)
        B-->>A: {status:denied|none}
        A-->>U: 拒答+替代问法(不编造)
    else 通过
        B->>G: enforce_sql草稿(hook1语法/hook3表权限/行隔离/脱敏/hook4 LIMIT)
        B-->>L: (draft存内存REQUESTS; 拦截才落审计)
        B-->>A: {status:draft, request_id, draft_sql, 口径note, hook_log}
        A-->>U: 【主人工点A1】确认卡: 展示最终SQL+口径 → 请确认/拒绝
        U-->>A: 确认(final_sql可编辑)
        A->>B: execute_query(session_token, request_id, final_sql)
        B->>G: 对final_sql重跑全链hook(写阻断/表白名单/行隔离/行数/时长)
        alt 通过
            B->>D: 只读账号执行(SELECT, max_execution_time=3000ms)
            D-->>B: 结果(截断≤5000行)
            B-->>L: 审计 approved(draft+final双SQL,row_count,elapse_ms)
            B-->>A: {status:success, rows, sql, chart_type, 口径}
            A-->>U: 结果卡: 表格+图型建议+最终SQL(硬性约束③双展示)
        else 被改坏/越权改写
            B-->>L: 审计 blocked/denied
            B-->>A: {status:denied, reason}
            A-->>U: 拒绝原因(仅落审计,不执行)
        end
    end
```

> 说明：上图中"确认卡/提问板块"在真 dsh 宿主下由 dsh 会话框架原生提供（`ask_user_question` 工具、approval `ask` 策略、fail-closed 确认卡，见 dsh门户可行性验证报告 §3）；等价宿主（`dual_backends.py`）用 prepare→execute 两段式脚本近似（无 LLM 自由裁量，见其 `one()` 循环）。两个 MCP 后端的 `prepare/execute` 契约完全一致，与宿主无关。

---

## 3. 角色卡明细

### 3.0 角色一览

| # | 角色 | 类别 | 是否 LLM | 宿主/载体 |
| --- | --- | --- | --- | --- |
| ① | 用户/提问者 | 人 | 否 | 浏览器（dsh Web GUI）或等价宿主控制台 |
| ② | 入口会话 Agent/问数助手 | 会话层 | 是（dsh 模型）| dsh 会话框架 / 等价宿主规则分派 |
| ③ | 业务后端"问数执行体" | 自治后端 | 否（规则引擎） | bi-workbench / hr-backend（Python FastMCP） |
| ④ | 权限与安全守卫 | 防线 | 否（纯代码） | 各后端执行路径内 hook 链 ①②③④ |
| ⑤ | 审计员 | 防线 | 否 | audit_log / hr_audit 落库 |
| ⑥ | 异常洞察/预警者 | 自治后端 | 否 | run_alert_scan / run_hr_scan |
| ⑦ | 管理员 | 人（admin 角色） | 否 | 后端管理工具（审计/报表/导出/ETL） |

---

### 3.1 角色卡①：用户 / 提问者

| 项 | 内容 |
| --- | --- |
| 目标 | 用自然语言拿到**受控、可解释**的业务数据/指标答案（财务经营 / 项目交付 / MaaS / 人力），不需要会 SQL |
| 身份 | 后端账号持有者。bi 演示账号（`sys_user`）：`admin`(管理员)/`zhangmin`(finance)/`liqiang`·`wangfang`(pm，各带部门)/`xiaowang`(staff)；hr 账号（`hr_user`）：`hr_admin`/`zhaoliu`·`qianqi`(hr_mgr 部门经理)。业务别名见 `list_aliases`（bi_workbench/backend/server.py BI_ALIASES、hr_backend/server.py HR_ALIASES） |
| 职责 | ①提问；②在**确认卡**上审阅草稿/最终 SQL 并确认或拒绝（可编辑 final_sql）；③在**澄清卡**上为歧义指标做选择；④在提问板块回答 Agent 的补问 |
| 可用"工具" | 无 MCP 工具。只有界面动作：提问、点"确认/拒绝/允许一次"、选澄清选项、`selected_metric` 回填、输入最终 SQL |
| 权限边界-能 | 登录（`login_vault` 别名或 `login`）；查看指标目录（`get_metric_catalog`）；对**自己角色权限内**的指标问数；确认/拒绝/编辑自己会话的草稿 |
| 权限边界-不能 | ❌ 不能改变 SQL 执行策略（不允许写类 SQL、不能绕过 hook 链、不能放大行数/时长上限——`core.py`/hr server 常量硬编码）；❌ 不能以任何方式让后端执行其角色权限外的表/指标（pm 改问销售部行数据、hr_mgr 问薪资均被拒，见测试 Z02–Z04）；❌ 密码明文不出现在对话中——凭据仅经 `login_vault`（vault 校验，密码永不出后端，server.py `login_vault` 注释）；改密须本人/管理员经 `change_password`，响应不回显密码（U05） |
| 输入/输出契约 | 输入：自然语言问题（如"华北区2026-08预算偏差最大的客户"）；输出：确认/拒绝/澄清选择/追问 四类人工动作 |
| 交接条件 | 提问后把控制权交给②（等草稿与确认卡）；A1/A2 两处人工点出现时，**系统停下等待用户**，模型不得自答自执行 |
| 人工审批点 | **A1（主）**：确认卡——最终 SQL 执行前的人工拍板；**A2（次）**：歧义口径选择（详见 §4） |
| 失败降级策略 | 用户拒绝 → 流程终止，无执行（`execute_query` 不被调用）；用户要求越权/写数据 → 会话层如实转达"无权限/仅支持查询"拒绝结果，**不尝试绕过**；对话含密码 → 会话层提示经 `login_vault` 与清理聊天记录（server.py `change_password` 说明） |

---

### 3.2 角色卡②：入口会话 Agent / 问数助手（会话宿主）

| 项 | 内容 |
| --- | --- |
| 本质 | **会话层由 dsh 会话框架（DeepSeek Harness Web GUI）/等价宿主提供**，不是本项目自研代码。本项目在其上定义"问数编排协议"：能力发现 → 路由 → prepare → 确认卡 → execute。等价宿主 = `bi_workbench/scripts/dual_backends.py`（一个进程并发挂两个 stdio MCP server，规则显式分派，作为无 LLM 的确定性回归与演示载体） |
| 目标 | 用户只面对一个对话窗口，不用知道数据在哪个系统、哪张表、哪条 SQL |
| 职责 | ① 启动时对每个注册后端 `tools/list` 能力发现（工具/指标清单）；② 按问题语义路由到正确后端：财务/项目/MaaS 词（毛利/回款/预算/延期/成本…）→ bi-workbench，人力词（离职/在编/薪酬/入职…）→ hr-backend；③ 把 `(session_token, question)` 组装为后端 `prepare_query` 调用；④ 按返回 `status` 驱动交互：`draft`→向用户展示【草稿 SQL+口径确认卡】并请确认；`clarified`→展示澄清选项供选择后以 `selected_metric` 重试；`denied/none`→如实转达拒答原因与替代问法，**不编造**；⑤ 用户确认后调用 `execute_query(session_token, request_id, final_sql)`（final_sql 为用户确认/编辑后的文本）；⑥ 对结果做口径解释并展示最终 SQL |
| 可用工具 | 后端池暴露的全部 MCP 工具，经 dsh 命名空间 `mcp__bi_workbench__*` / `mcp__hr_backend__*`（见 `session.v2.jsonl` 真实会话工具清单）。另有 dsh 框架内建 `ask_user_question`（提问板块：向用户发确认/选择问题，答案带回稳定 id） |
| 权限边界-可用 | 只调用后端暴露的 MCP 工具；只能在用户登录态（拿到 `session_token`）下发起 prepare/execute |
| 权限边界-不可用 | ❌ 不直连任何数据库（不持有连接串）；❌ 不能绕过后端 hook 决定"能否执行"（后端 deny 是终局）；❌ 不能替用户做口径选择；❌ 不能凭空生成"业务结论"，指标必须来自 `get_metric_catalog`/prepare 返回值 |
| 输入/输出契约 | 输入：用户自然语言问题 + 后端工具能力清单。输出：一次 `prepare_query` 调用与按 `status` 分派的交互动作（展示确认卡/澄清卡/拒答文案）。规则引擎为后端 `prepare` 兜底——即使会话层不派 LLM，`dual_backends.py` 也能把同一批问题跑成 draft→execute 闭环 |
| 交接条件 | 拿到 `status=draft` 且已向用户展示 → **等待人工确认（A1）**，不自行 execute；`status=clarified` → 交给用户选择（A2）；`status=denied/none` → 把结论交还用户并给替代问法，结束本轮；确认后 execute 结果交还用户，本轮结束（可追问开启下一轮） |
| 人工审批点 | 呈现并等待 **A1（确认卡）/ A2（澄清选择）**，两处均以人为终审；dsh 侧另有工具级 approval `ask` 策略，无应答者 fail-closed 拒绝（dsh门户可行性验证报告 §3） |
| 失败降级策略 | 后端返回 denied/none → 如实转述，建议换问法或查指标目录（U03 实证）；后端进程掉线/工具发现失败 → 提示"后端不可用，请联系管理员或稍后重试"（宿主行为，见 `dual_backends.py` 连接失败处理与 server.py `_user` 抛错路径）；会话/request_id 失效 → 提示重新 `login_vault`/重新 `prepare_query` |

---

### 3.3 角色卡③：业务后端"问数执行体"（bi-workbench / hr-backend 自治 MCP server）

| 项 | 内容 |
| --- | --- |
| 本质 | 两个**自治 MCP server**（Python FastMCP 2.x，stdio），各带自己的库/账号/语义层/权限/审计/告警。把 `prepare_query`/`execute_query` 等建模为它的能力。**内部规则解析器决定 SQL 草稿，无 LLM 自由裁量**（core.py 顶部注释："规则 SQL 引擎先落地…LLM 增强见 llm_gen.py(C 阶段)"；`llm_gen.py` 未实现 → 当前 SQL 100% 由规则引擎生成） |
| 职责 | ① `get_metric_catalog` 暴露语义层（指标/口径/公式/血缘/权限标签）；② `prepare_query`：语义解析（`SEM.match`/`parse_metric`，最长匹配）→ 歧义给 `clarified+options` → 命中指标走权限预检 → `gen_sql` 规则生成草稿 → 草稿过全链 hook → 返回 `draft`；③ `execute_query`：校验 `request_id` 归属 → 对最终 SQL 重跑全链 hook → 只读账号执行 → 审计 → 返回结果；④ 扩展工具：告警扫描/报表/导出/ETL（bi）与 HR 扫描（hr） |
| 可用工具 | bi-workbench 15 项（当前 `server.py`）：`login` / `logout` / `list_aliases` / `login_vault` / `change_password` / `get_metric_catalog` / `prepare_query` / `execute_query` / `run_alert_scan` / `list_alerts` / `list_audit_logs` / `gen_report` / `export_query` / `list_exports` / `etl_ingest`。hr-backend 10 项：`login` / `logout` / `list_aliases` / `login_vault` / `change_password` / `get_metric_catalog` / `prepare_query` / `execute_query` / `run_hr_scan` / `list_alerts`。注：技术方案 §6 记载的 12/7 为早期快照（vault 凭据族为后续新增），以 server.py 当前为准 |
| 关键入参/返回要点 | `prepare_query(session_token, question, selected_metric="") → {ok, status: draft\|denied\|clarified\|none, request_id?, draft_sql?, note?, options?, reason?, hook_log?, confirm_hint?}`；`execute_query(session_token, request_id, final_sql) → {ok, status: success\|denied\|error, columns?, rows(≤200)…, row_count, sql, chart_type?, reason?}`（bi server.py `prepare_query`/`execute_query`） |
| 权限边界-可用 | 只执行**本库、本角色**白名单内的查询；用 `app` 账号写审计/告警/导出/ETL 表（授权写面仅四类表，技术方案 §1） |
| 权限边界-不可用 | ❌ 不执行任何写类 SQL（正则+AST 双层拒，`core.enforce_sql`）；❌ 不访问其它库/系统表（`ROLE_TABLES`/hr `ROLE_TABLES` 白名单）；❌ 不给角色外用户签发可执行的草稿 |
| 输入/输出契约 | 自然语言问题（+可选的 `selected_metric`）→ 上文 JSON。成功执行输出含 `columns`/`rows`/`row_count`/`sql`(最终生效 SQL)/`metric`/`chart_type`/`note`(口径) |
| 交接条件 | 语义层无法唯一判定 → 交还 `clarified` 等用户选择（A2）；越权/未知 → `denied/none` 交还会话层；`draft` → 交还会话层进入 A1 等待；execute 结束（success/denied/error）→ 交还会话层呈现 |
| 人工审批点 | 自身不裁决"要不要执行"——只产出 `draft` 与 `request_id`，**执行必须由 execute_query 承接且其前置条件是用户已在 A1 确认**；`request_id` 绑定用户（`REQUESTS[request_id]["user_id"] == 当前用户`，server.py `execute_query`），杜绝跨会话冒用 |
| 失败降级策略 | 见 §5 矩阵；核心原则：**宁可拒答不编造、宁可 denied 不执行**。任何异常（hook 失败/DB 异常/超时）都走"审计 + 结构化返回"，不外抛裸堆栈给用户 |

---

### 3.4 角色卡④：权限与安全守卫（hook 链 ①②③④）

| 项 | 内容 |
| --- | --- |
| 本质 | 纯代码防线，**无 LLM 参与**。分布在 `core.enforce_sql`（语法/hook1、表权限/隔离/脱敏/hook3、LIMIT/hook4）与 `prepare_query` 内（语义层 hook2、指标权限标签预检），以及 hr 后端同构实现（`hr_backend/server.py enforce_sql`）。**prepare 草稿与 execute 最终 SQL 各走一遍全链**（技术方案 §3、§7） |
| 职责 | 四道检查（对应《题目.txt》硬性约束 2）：**hook①语法/AST**：`sqlglot` 解析、必须是单条 `SELECT`（bi 另拒写类关键字、多语句；hr 另拒集合运算 Intersect/Except）；**hook②语义层**：指标必须在 `metric_definition`（bi 15 指标）/HR 常量（hr 6 指标）注册，歧义（毛利 vs 净利润、离职数 vs 离职率）→ clarified；**hook③权限**：指标 `permission_tag`→角色（bi：`finance.*`→finance/admin，`project.*`→pm/admin/finance，`public.maas`→全员按表级再过滤）、表前缀白名单（`ROLE_TABLES`/hr `ROLE_TABLES`）、行隔离（bi `ROW_ISOLATION_ROLES={"pm"}` 自动注入 `dept=本人部门`；hr `ROW_ISOLATION_ROLES={"hr_mgr"}` 要求 SQL 必须显式 `dept='本人部门'`）、字段脱敏（bi `MASK_ROLES={"pm"}` 将 `project.client` 投影改写为 `CONCAT(LEFT(client,2),'***')`）；**hook④成本/行数**：无 LIMIT 自动补 `LIMIT 500`（bi/hr `AUTO_LIMIT`），显式 `LIMIT>5000` 拒绝（hr），执行时长 3000ms 熔断（`SET SESSION max_execution_time`，bi `EXEC_TIME_LIMIT_MS`/hr 同），返回行数截断 ≤5000（`MAX_ROWS`） |
| 可用工具 | 无独立 MCP 工具；它是一组被 `prepare_query`/`execute_query`/`export_query` 调用的内部函数（`core.enforce_sql`、`_analyze`、`role_allowed` 表、`gen_sql`、hr `parse_metric`+`enforce_sql`） |
| 权限边界 | 判定结果只有三种放行态之一：`PASS`（返回改写后 SQL）/ `DENY`（带原因）/ `REWRITE`（行隔离注入、脱敏改写）。守卫不做"建议"，只做"允许/拒绝" |
| 输入/输出契约 | `enforce_sql(sql, role, dept="") → (ok, rewrite_sql, reason)`；语义层 `_analyze(question, user, selected_metric) → kind ∈ {metric, intent, none, ambiguity}`（bi server.py `_analyze`，其 docstring 中写到的 `deny` 无实际返回路径，权限拒绝发生在 prepare 的指标标签检查）。hook 日志以 `[hookN 名称]` 文本追加进 `hook_log` 返回给用户看（可解释） |
| 交接条件 | hook② 判歧义 → 交接 clarified 等用户（A2）；hook①③④ 任一失败 → denied/blocked 交会话层并向用户解释原因（原因即 `hook_result`/审计内容）；全部通过 → 放行到执行（A1 之后） |
| 人工审批点 | 无（纯裁决层）；**它恰恰保证"人只能确认规则允许的东西"**——用户在确认卡上把 SQL 改坏/改成越权查询，execute 重跑 hook 即被拒（测试 P03/P06） |
| 失败降级策略 | 一律 fail-closed：规则无法判定的（如空 SQL、语法错）按拒绝处理并写审计；DB 只读账号构成兜底第二道闸（即使 hook 出现绕过，`bi_ro`/`hr_ro` 也无写权限） |

---

### 3.5 角色卡⑤：审计员（audit_log / hr_audit 落库者）

| 项 | 内容 |
| --- | --- |
| 本质 | 后端 `app` 写账号落库的纯代码角色：`core.write_audit` / `hr write_audit`。每次人工点/拦截/执行/报表/导出/ETL 都写一条 |
| 职责 | 记录 `(user_id, role, question, draft_sql, final_sql, status, hook_result, row_count, elapse_ms)`（bi `audit_log` 与 hr `hr_audit` 同构，schema 见 bi sql/schema.sql） |
| 真实记录到的状态值 | `clarified`（歧义/口径不明，含候选 alias）/ `denied`（指标权限不足、未知指标拒答）/ `blocked`（hook 拦截：写操作、越权表、被改坏的最终 SQL）/ `error`（执行期异常、超时熔断）/ `approved`（成功执行，**一条记录同时含 draft_sql 与 final_sql**）/ `report` / `export`（bi 扩展动作）。**落库节奏两后端不同（源码核对）**：bi 在 `prepare_query` 即对 clarified/denied/blocked 各写一条（server.py L140/162/174/181），hr 的 `prepare_query` **不落审计**，其 blocked/error/approved 均在 `execute_query` 内写入（hr server.py L365/377/384）。注：draft 草稿本身存内存 `REQUESTS`（bi core.py / hr server.py），成功执行时以 `approved` 行把 draft+final 双 SQL 一并落库，故"草稿与最终 SQL 均可追溯"成立；`list_audit_logs` 查询面即此表（bi） |
| 权限边界 | 写入用 `app` 账号（仅授权表）；读取：bi 仅 admin 可经 `list_audit_logs`（limit≤100）查询（非 admin 调用抛"仅管理员可查看审计日志"，测试 Z05）；hr 后端**暂未暴露**审计列表工具（`hr_audit` 只落库，管理查询为规划项，见 §9） |
| 输入/输出契约 | `write_audit(user, question, draft, final, status, hook_result, rows, elapse)` → 无返回（try/except 吞写失败，不阻断主流程）；查询输出见 `list_audit_logs` 返回结构 |
| 交接条件 | bi 后端：prepare 判定为非正常态（clarified/denied/blocked）即写一条再交还；hr 后端：prepare 不落审计，拦截与结果在 execute_query 内以 blocked/error/approved 落库；两后端 execute 终态（success→approved / denied→blocked / error→error）各写一条；bi 报表/导出/ETL 各写一条 |
| 人工审批点 | 无（记录者）。审计失败**不放行**：本系统审计写入与执行放行解耦——执行成功后才写 approved，审计写失败不影响已完成的执行；反过来，hook 拦截的查询**永不执行**（拦截先于执行） |
| 失败降级策略 | 审计写库异常被吞掉（`except Exception: pass`，core.py `write_audit`），保证查询主链路不被审计拖垮；如需严格审计链保证（审计先行再执行）属规划项（§9） |

---

### 3.6 角色卡⑥：异常洞察/预警者

| 项 | 内容 |
| --- | --- |
| 本质 | 规则扫描器（非 LLM 洞察）：bi `run_alert_scan` → `core.scan_alerts()`；hr `run_hr_scan` → 离职率突增扫描。产出写站内消息表 `alert_message`（bi）/`hr_alert`（hr），供 `list_alerts` 读取 |
| 职责 | 按预置阈值规则扫描异常并写预警（幂等：同 type+title 只写一次）。bi 规则 R1–R6：环比暴跌(≤-50%)、预算偏差(≤-15%)、下月延期风险、LLM 成本突增(≥+150%)、同比骤降(≤-40%)、客户集中度(≥40%)；hr 规则：部门离职率 >8%（2026-08 vs 07）。另有登录侧"异常访问"告警（连续 3 次失败写 `alert_type=异常访问`，core.py `login`） |
| 可用工具 | `run_alert_scan(session_token)`（bi，登录即可触发）；`run_hr_scan(session_token)`（hr）；`list_alerts(session_token[, limit])`（消息中心，50 条内） |
| 权限边界 | 任何已登录用户可触发扫描/读消息中心（代码仅做 `_user` 校验）；但**写告警内容由扫描规则产生，调用方无法注入任意告警文案**；告警读取不限角色（当前实现） |
| 输入/输出契约 | `run_alert_scan → {ok, added:[{type,title,level,dept}...], count, note}`；`list_alerts → {ok, alerts:[{id,ts,type,title,content,level,dept,read}]}` |
| 交接条件 | 扫描完成 → 新增告警列表交还调用者展示；用户再经 `list_alerts` 或报表查看（`gen_report` 内的"异常与风险提示"节引用同类规则） |
| 人工审批点 | 无（写站内消息，非外发/非自动处置业务，符合《要求.txt》对"自动外发邮件/自动处置"的禁止边界） |
| 失败降级策略 | 扫描查询失败仅跳过该规则（try/finally 逐规则执行）；写告警失败吞掉；幂等键避免重复刷屏（core.py `scan_alerts` seen 集合） |

---

### 3.7 角色卡⑦：管理员

| 项 | 内容 |
| --- | --- |
| 本质 | 人（`admin`/`finance` 角色的职责集合）。管理动作全部是**后端工具授权操作**，各有专属审计 |
| 职责 | 查看审计日志、导出留痕、生成经营快报、受控导出、ETL 接入、改密（他人）、全域问数 |
| 可用工具 | bi：`list_audit_logs`（仅 admin）、`list_exports`（仅 admin）、`gen_report(daily/weekly/period)`（仅 finance/admin，`REPORT_ROLES`）、`etl_ingest(csv/excel/api/mysql)`（仅 finance/admin）、`export_query`（SQL 全链 hook 后执行并写 `export_log`）、`change_password`（本人或 admin）、`run_alert_scan`/`list_alerts`（登录即可）。hr：`hr_admin` 全域 HR 指标（含 restricted.salary）、`change_password`(本人或 hr_admin) |
| 权限边界-可用 | admin 可访问 bi 全部表前缀：`finance_*`/`project_*`/`llm_usage`/`audit_*`/`alert_*`/`metric_*`（core.py `ROLE_TABLES`）；finance 可访问 `finance_*`/`project_*`/`metric_*`/`alert_*`；hr_admin 可访问 `monthly_*` |
| 权限边界-不可用 | ❌ 管理工具不放开写库/危险 SQL（`list_audit_logs`/`gen_report`/`export_query` 均只读路径）；❌ 导出仅 SELECT 且最多 5000 行（server.py `export_query` docstring）；❌ ETL 仅写授权表 `llm_usage`（`etl_ingest` 目标固定）；❌ hr 后端无审计列表工具（管理查询未实现） |
| 输入/输出契约 | `list_audit_logs(limit) → {ok, logs:[{id,ts,user,role,question,status,hook,rows}]}`；`gen_report(report_type) → {ok, report_type, title, markdown, metrics}`；`export_query(final_sql, description) → {ok, status, exported_rows, content_md5, csv_preview,...}`（导出内容只留 md5 摘要，`export_log`） |
| 交接条件 | 管理员调用管理工具即视为授权操作；审计/导出日志由 ⑤ 落库后供其随时复查；发现异常（如 `hook_result` 高发 denied/blocked、`异常访问`告警）→ 线下处置账号/口径/数据 |
| 人工审批点 | **导出无二次确认卡**（代码中 `export_query` 直接受控执行，其"人工授权"体现为：仅已登录业务用户发起 + 全链 hook + 每次导出强制 `export_log` 留痕——与问数的两段式不同，如实标注）；ETL/报表同理为一次性授权动作 |
| 失败降级策略 | 无权限调用 → 后端抛 `ValueError` 拒绝（如"仅管理员可查看审计日志"）；SQL 未过 hook → `export_query` 返回 denied 仅留审计；ETL 数据源失败 → `{ok:false, error}` + 审计 blocked 行 |

---

## 4. 人工审批点清单（评分重点）

| 编号 | 名称 | 出现在 | 谁能绕过 | 绕过后果 | 证据/代码 |
| --- | --- | --- | --- | --- | --- |
| **A1** | **最终人工确认点（两段式确认 = execute_query 前的人工拍板）** | `prepare_query` 返回 `status=draft` 之后、`execute_query` 之前。会话层向用户展示【草稿 SQL + 口径说明（note/hook_log）】确认卡，用户可**编辑 final_sql** 后确认或直接拒绝 | **无人能绕过**。执行必须持有 `request_id`（仅 prepare 成功才签发）且绑定发起用户；即使会话层模型"自作主张"直接调 `execute_query`，也因缺少 A1 语义上的人确认而被视为违规——更关键的是：`final_sql` 无论是否被编辑，execute 都会**重跑全链 hook**，改写成越权/写操作即 `denied`+审计（P03/P06 实证）。dsh 侧另有一道工具级 approval `ask` 门，无应答 fail-closed | 模型跳过确认 = 后端仍以 hook 兜底拦截非法 SQL；被改坏的 SQL → `status=denied`、`audit_log.status=blocked`，仅落审计不执行 | 技术方案 §3、§7；bi server.py `prepare_query`/`execute_query`；评测 U01/U02（UI 提问板块确认后拿到结果） |
| **A2** | **次级人工确认点（歧义口径澄清选择）** | `prepare_query` 命中歧义（bi：利润=毛利/净利润、"成本"多义；hr：离职=离职数/离职率）→ 返回 `status=clarified` + `options[]`。会话层展示澄清卡，**必须由用户选择 metric** 后带 `selected_metric` 重试 | 无人能替用户选口径；`selected_metric` 必须存在于后端语义层注册表（`by_id`），乱传无效 | 模型若自行挑一个口径继续 → 违反"口径由人定"的设计（规则引擎不会自动选择歧义分支，歧义必返回 clarified，测试 A01–A05）；用户拒不选择 → 停留 clarified，不产生任何执行 | bi server.py `_analyze` ambiguity 分支；hr server.py `parse_metric`；评测 A01–A05 |
| 附注 | 拒绝澄清卡/确认卡不产生专门审计行的说明 | 歧义/拒绝/拦截在 bi 的 prepare 阶段**已**落审计（`clarified`/`denied`/`blocked`），hr 在 execute 阶段落（§3.5）；用户在确认卡上"拒绝"表现为会话层不发起 execute_query（`REQUESTS` 残留内存可忽略/复用），系统无"半执行"状态 | — | — | bi server.py `prepare_query` 各分支；`REQUESTS`（core.py 注释"确认前草稿上下文"） |

> 设计要点：**A1 是唯一的"执行闸门"，A2 是唯一的"口径闸门"**。两处都要求人类在环，正面对应《要求.txt》禁止项"高影响决策场景缺少人工复核机制"与《题目.txt》硬性约束 3（展示模型 SQL 与确认后最终 SQL）。

---

## 5. 失败降级矩阵（场景 × 行为）

> 行为均取自源码真实分支；标注"见 server.py 实现"处表示该行为由宿主/调用方编排决定而非后端代码内定。
> 审计列说明：bi 后端在 `prepare` 即写 clarified/denied/blocked、`execute` 写 blocked/error/approved；hr 后端 `prepare` 不落审计，全部在 `execute_query` 内写入（§3.5）；`none` 分支（未识别）不产生审计行。

| # | 场景 | 后端真实行为 | 会话层呈现 | 审计 | 可否重试/替代 |
| --- | --- | --- | --- | --- | --- |
| 1 | 语义歧义（多指标命中同义词） | `status=clarified` + `options[]`（bi `_analyze` ambiguity；hr `parse_metric` 平局） | 澄清卡，等用户 A2 选择后带 `selected_metric` 重试 | `clarified` | 是（用户选口径） |
| 2 | 未知/过宽口语（"销售情况""人员流动"） | bi `status=none`（未识别到可计算指标）；hr `status=denied`+支持列表 | 拒答不编造，建议参照 `get_metric_catalog` 换指标问法（评测 U03） | bi `none` 分支**不落审计**；越权/语义层失败分支见下与 §3.5 | 是（换问法） |
| 3 | 指标越权（pm 问毛利；hr_mgr 问薪资） | `status=denied`（指标 permission_tag 检查，bi server.py `role_allowed`；hr restricted.salary 检查） | 显示无权原因与所需标签 | `denied` | 否（换有权账号） |
| 4 | 行/表越权（pm 查他部门、hr_mgr 读薪资表、改读系统表） | hook③ 表白名单拒绝；行隔离：bi 自动注入本人 dept、hr 要求 SQL 显式 `dept='本人部门'`（不符即拒） | 显示拦截原因（测试 Z02/Z03/Z04/Z06/P03/P06） | `blocked`/`denied` | 否（越权不可协商） |
| 5 | 提示注入 / 诱导写操作（UPDATE/DELETE/DROP/多语句/集合运算） | hook① 正则+`sqlglot` AST：仅单条 SELECT；禁写关键字；hr 禁 Intersect/Except；DB 只读账号兜底（测试 S01–S06、P01–P05） | 显示"仅允许 SELECT/语法校验失败" | `blocked` | 否（注入一律拒绝） |
| 6 | 超行数 | hook④：无 LIMIT 自动补 `LIMIT 500`；显式 `LIMIT>5000` 拒绝（hr）；返回行数截断 5000（`MAX_ROWS`） | 草稿 SQL 可见自动 LIMIT；超限拒绝给原因 | `blocked`（拒绝时） | 是（改窄时间/加条件） |
| 7 | 执行超时 | `SET SESSION max_execution_time=3000ms`，MySQL 超时中断 → 捕获异常 `status=error` | 提示执行失败（超时熔断） | `error`（含 elapse_ms） | 是（收敛问题） |
| 8 | 后端进程掉线 / MCP 不可用 | MCP 调用失败/工具发现失败（宿主侧） | 提示"后端不可用"，检查进程或重连（见 server.py 实现/等价宿主连接错误路径） | 无（未达后端） | 是（恢复后重试） |
| 9 | 未登录 / 会话过期 | `_user(tok)` 为空 → 抛 `ValueError("未登录或会话已过期, 请先调用 login")`（bi/hr server.py） | 提示重新 `login_vault` | 无 | 是（重新登录） |
| 10 | request_id 失效/跨用户冒用 | `REQUESTS` 无此 id 或 `user_id` 不匹配 → `{ok:false,error:"request_id 无效或不属于当前用户, 请重新 prepare"}`（bi/hr server.py `execute_query`）；服务重启后 REQUESTS 内存清空 | 提示重新 prepare | 无 | 是（重新 prepare） |
| 11 | 执行期 DB 异常 / SQL 运行错误 | 捕获异常 → `status=error` + 原因 | 展示错误原因，不改写、不重试编造 | `error` | 是（换问法） |
| 12 | 复合/纠偏问法（"销售额和回款额，只要毛利"） | **已知局限**：被静默解析为单指标（回款额），纠偏短语不参与（评测 C01 观察项，非阻断，落在受控 SELECT 内） | 返回单指标结果（如实展示所用口径 SQL） | `approved`（按实际执行口径） | 是（拆成单指标问） |
| 13 | 非法时间词（"2026-99"） | **已知局限**：未拒答，静默兜底到库内最新月 `ym='2026-08'`（评测 X04 观察项） | 结果含实际 SQL，用户可核对时间条件 | `approved`（按兜底 SQL） | 是（修正时间重问） |
| 14 | 指标所需客户维度不存在（净利润按客户） | `gen_sql` 返回空 → `status=clarified`，提示"口径不明确，请改用毛利或按区域/整体口径"（bi `gen_sql` net_profit 分支） | 澄清/引导替代口径 | `clarified` | 是 |

---

## 6. 工具权限矩阵（工具 × 角色，从真实 server.py / core.py 推导）

图例：✅ 可用 ｜ ⛔ 不可用（含"后端拒绝"）｜ 🔒 登录即可（角色内再按 hook 细分） ｜ — 该后端无此工具

### 6.1 后端① bi-workbench（角色：admin / finance / pm / staff）

| 工具 | admin | finance | pm | staff | 依据（server.py / core.py） |
| --- | --- | --- | --- | --- | --- |
| `login` / `logout` / `list_aliases` / `login_vault` / `get_metric_catalog` | ✅ | ✅ | ✅ | ✅ | 通用会话/目录工具 |
| `change_password` | ✅（任意目标） | 🔒本人 | 🔒本人 | 🔒本人 | `core.change_password`：`actor=="admin" or actor==target` |
| `prepare_query` / `execute_query` | ✅ 全域 | ✅ finance.*+project.*（client 不打码但 finance 表级含 project_） | 🔒 仅 project.*（指标标签+表级+行隔离+client 脱敏） | 🔒 仅 public.maas（llm_usage） | `ROLE_TABLES`/`MASK_ROLES`/`ROW_ISOLATION_ROLES` + prepare 内 `role_allowed` |
| `run_alert_scan` / `list_alerts` | ✅ | ✅ | ✅ | ✅ | 仅 `_user` 校验 |
| `list_audit_logs` | ✅ | ⛔（抛"仅管理员"） | ⛔ | ⛔ | `if u["role"] != "admin": raise`（测试 Z05） |
| `gen_report` | ✅ | ✅ | ⛔ | ⛔ | `REPORT_ROLES={"finance","admin"}` |
| `etl_ingest` | ✅ | ✅ | ⛔ | ⛔ | `REPORT_ROLES` |
| `export_query` | ✅ | ✅（hook 内按其角色） | 🔒（仅 project 白名单内，client 脱敏后导出） | 🔒（仅 llm_usage） | 无角色硬校验，权限由 `_checked_execute`→`enforce_sql(role)` 决定；导出即写 `export_log` |
| `list_exports` | ✅ | ⛔ | ⛔ | ⛔ | `if u["role"] != "admin": raise` |

指标可达性（prepare 内 `role_allowed` 标签 → 再经表级白名单二次过滤）：

| 指标域（permission_tag） | admin | finance | pm | staff | 说明 |
| --- | --- | --- | --- | --- | --- |
| finance.*（收入/成本/毛利/净利润/回款/预算/费用…） | ✅ | ✅ | ⛔ denied | ⛔ denied | pm/staff 无 finance 标签 |
| project.*（里程碑完成率/延期率/工时偏差/风险数） | ✅ | ✅ | ✅（仅本部门+client 脱敏） | ⛔ | pm 自动行隔离+打码 |
| public.maas（模型成本/Token 消耗） | ✅ | ⛔ | ⛔ | ✅ | **防线纵深示例**：指标标签放行但表级白名单再拦——finance/pm 无 `llm_usage` 表权，实际仅 admin/staff 可问 MaaS（`ROLE_TABLES`） |

### 6.2 后端② hr-backend（角色：hr_admin / hr_mgr）

| 工具 | hr_admin | hr_mgr | 依据（hr_backend/server.py） |
| --- | --- | --- | --- |
| `login`/`logout`/`list_aliases`/`login_vault`/`get_metric_catalog` | ✅ | ✅ | 通用 |
| `change_password` | ✅（任意） | 🔒本人 | `role!="hr_admin" and user_id!=target` → 拒绝 |
| `prepare_query`/`execute_query`（非薪资指标） | ✅ | ✅（仅本部门行：SQL 必须含 `dept='本人部门'`） | `ROW_ISOLATION_ROLES={"hr_mgr"}`，`enforce_sql` AST 找 EQ 校验 |
| `prepare_query`/`execute_query`（薪资：人力成本/平均工资） | ✅ | ⛔ 双重拒绝：指标级 restricted.salary + 表级（hr_mgr 白名单仅 `monthly_headcount`） | `if mm[7]=="restricted.salary" and role!="hr_admin"` + `ROLE_TABLES`（测试 Z03/Z04） |
| `run_hr_scan` / `list_alerts` | ✅ | ✅ | 仅 `_user` 校验 |
| `list_audit_logs` 等管理工具 | — | — | hr 后端未实现（§9 规划） |

> 强调：所有"⛔/🔒"判定都是**后端代码**（hook/白名单/标签）完成，不是模型自我约束；模型只能替用户"问"，不能替后端"批"。

---

## 7. 交接与状态机

### 7.1 状态定义（后端真实取值）

- `prepare_query` 出口状态：**`draft`**（可进入确认）/ **`clarified`**（歧义，需 `selected_metric` 重试）/ **`denied`**（无权限或未知指标拒答）/ **`none`**（未识别到可计算指标，bi）。
- `execute_query` 出口状态：**`success`** / **`denied`**（最终 SQL 未过 hook）/ **`error`**（执行期异常）。
- `audit_log.status` 记录值：`clarified` / `denied` / `blocked` / `error` / `approved` / `report` / `export`。

### 7.2 状态流转

```text
用户提问(自然语言)
   │ ①会话层路由 → ②后端 prepare_query(question[, selected_metric])
   ▼
prepare 语义解析 + 全链 hook 预检
   ├─ 歧义 ──────────────► status=clarified ──(A2 用户选 metric)──► prepare_query(selected_metric=…)
   ├─ 未识别/过宽 ────────► status=none / denied ──► 拒答+替代问法（终态，可换问法重启）
   ├─ 指标/表/行越权 ─────► status=denied / blocked ──► 审计(denied/blocked)（终态）
   └─ 通过 ──────────────► status=draft {request_id, draft_sql} ──(REQUESTS 内存暂存)
                                 │
                                 ▼  A1 确认卡（人：确认/编辑 final_sql/拒绝）
                          ┌─ 拒绝/不操作 ──► 流程终止（无执行、无 execute 调用）
                          └─ 确认 ──► execute_query(request_id, final_sql)
                                          │ 对 final_sql 重跑全链 hook
                                          ├─ 拦截 ──► status=denied + 审计(blocked)（终态）
                                          ├─ 执行异常/超时 ──► status=error + 审计(error)
                                          └─ 成功 ──► status=success + 审计(approved, 双SQL)
                                                     │ 结果卡(表格+chart_type+最终SQL+口径)
                                                     ▼
                                                 可追问开启下一轮（同会话/同草稿循环）
```

### 7.3 交接规则要点

1. **draft 不是"可执行许可"**：`REQUESTS[request_id]` 只存上下文（user/question/draft_sql/metric），执行权唯一入口是 `execute_query`，且校验 `request_id` 归属当前用户（bi/hr 一致）。
2. **clarified 无旁路**：不带 `selected_metric` 重试会再次返回 clarified；`selected_metric` 必须是语义层注册 id，否则回退规则解析。
3. **denied/blocked/error 为单轮终态**：本轮不再自动重试（避免循环空转），由用户决定换问法/换账号/收敛条件。
4. **会话过期/服务重启**：`SESSIONS`/`REQUESTS` 为进程内存态，token 与草稿一并失效 → 全部回到"重新 login_vault → prepare"起点（降级见 §5 场景 9/10）。

---

## 8. 测试与证据

- **自动评测 47/47 全绿**（评测报告.md §2–§4）：正常 11 / 模糊歧义 7 / 冲突多意图 4 / 越权 8 / 提示注入 6 / 异常输入 5 / SQL 写阻断 6。逐条驱动真实 `prepare_query`(语义层)+`execute_query`(二次确认+全链 hook)，登录统一走 `login_vault`，评测进程不接触明文密码。
  - 与本文档的对应：A01–A05 → §3.1/A2 歧义澄清；Z00a–Z06 → §6 权限矩阵与行隔离/脱敏；P01–P06/S01–S06 → §5 场景 4/5 注入与写阻断；N01/N05/N08/N11 → 两段式闭环与审计留痕；X01–X05 → §5 场景 12/13 已知局限。
- **UI 人工复核**（评测报告 §5，真实 dsh 界面会话）：U01/U02 —— **提问板块确认**后拿到结果（客服部离职率 9.54%、恒信科技 -28.33%），对应 A1 人工点闭环；U03 —— 字典外指标 `none` 拒答并给替代问法（§5 场景 2）；U04 —— 会话层凭据红线（仅 `login_vault`、密码不出现、索密被拒）；U05 改密不回显、U07 用量页为待复测（PENDING）。
- **会话层证据**：`session.v2.jsonl` 记录了 dsh Web GUI 真实会话——approval 策略 `ask`、工具集以 `mcp__bi_workbench__*`/`mcp__hr_backend__*` 形态全量注册、内建 `ask_user_question`（提问板块），印证 §3.2 角色卡与 A1 的宿主侧机制。
- **等价宿主回归**：`python bi_workbench/scripts/dual_backends.py` → `==== DUAL-BACKEND(等价dsh宿主): PASS ====`（双后端并发、双账号独立、prepare→确认→execute 全闭环）。
- **单后端冒烟**：`smoke_mcp.py`/`smoke_ext.py`/`hr_backend/smoke_hr.py` 覆盖权限拦截/歧义/SQL 安全/脱敏行隔离/告警/审计（技术方案 §8 注）。

---

## 9. 未实现 / 规划态（如实标注）

以下能力**未实现或仅规划**，本文档不将其写成已具备：

1. **LLM 自由编排多步工作流 / 复合问法语义理解**：当前 `prepare_query` 为"单指标规则解析"（规则引擎确定性生成 SQL，`core.gen_sql`）；core.py 头注提到的 LLM 增强模块 `llm_gen.py`（C 阶段）**不存在**；复合/纠偏问法（C01）、非法时间兜底（X04）为记录在案的语义局限。规划：引入 LLM 只做"自然语言→受控参数"的结构化解析（产出仍走同一 hook 链与人工确认），不做自由 SQL。
2. **OpenOPC 式"角色化 AI 员工/任务交接/同行复核"**：仅作为参考思想引用（要求.txt §4），本系统未实现跨员工任务委托与同行复核闭环。
3. **OpenHuman 式本地优先记忆/持久化任务图**：未接入。
4. **门户级 UI 与账号体系/RBAC**：dsh 原生为单机单用户、无账号/顶栏/管理页（dsh门户可行性验证报告 §4）；门户壳自建为规划路线（报告 §5.2 建议架构），本轮以"统一问数入口（dsh Web/等价宿主）"交付。
5. **多后端运行时热插拔**：dsh 连接集合为静态声明式，注册新后端需改配置+热重载/重启（可行性报告 §2.3）；等价宿主为字典加一行。
6. **hr 后端管理面**：`hr_audit` 仅落库，未暴露审计列表/导出等管理工具（bi 侧有 `list_audit_logs`/`list_exports`）。
7. **严格"审计先行"保证**：当前审计写失败被吞、执行与审计解耦（§3.5）；如需"审计成功才放行"的强链需改造。
8. **行数预检函数未接入**：`core.row_check`（COUNT 预检）已实现但当前执行路径未调用；现依赖自动 LIMIT+截断+时长熔断。
9. **其余**：CSV/Excel ETL 缺外部样例文件、PostgreSQL 数据源接入、并发/压测、`genui` 交互渲染与 `dsh-usage` 用量页（U06/U07 PENDING，插件已装待验证）。

---

*（全文完。所有角色卡、状态、审批点均可按 §3 中的代码锚点在 `bi_workbench/backend/server.py`、`bi_workbench/backend/core.py`、`hr_backend/server.py` 中核对。）*
