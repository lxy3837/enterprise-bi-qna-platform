---
name: enterprise-qna
description: 企业问数工作台接入指南。当用户提出财务/项目/MaaS 指标或人力资源(HR)领域的自然语言问数/看数请求(如收入、成本、毛利、回款、预算偏差、费用率、里程碑、延期风险、模型成本、token、离职率、在编人数、工资、人力报表、审计)，需要路由到 MCP 后端 bi_workbench 或 hr_backend 完成查询时使用。给出真实指标字典、账号权限、标准流程(登录→prepare_query→用户确认→execute_query)与推荐问法；严禁凭空猜测或拼凑字典外的指标。
---

# 企业问数接入指南（bi_workbench + hr_backend）

本工作台把自然语言问题路由到两套独立 MCP 后端。模型必须按本指南提供的事实字典与流程操作，**禁止猜测指标口径、禁止自造 SQL 绕过语义层**。

## 0. 最高优先级规则

1. 先判断领域（domain）：BI（财务/项目/MaaS）还是 HR。
2. 任何查询先 `login_vault(别名)` 登录，再 `prepare_query`；`execute_query` 只允许在用户确认 `draft_sql` 之后调用（用户若明确授权"直接查/一键执行"才可连做）。**请求确认必须调用 `ask_user_question`（dsh 提问板块）把 draft_sql 与选项渲染给用户，不要用普通文本发问。**
3. `prepare_query` 返回 `none`/`denied`/`clarified` 时按第 3 节分支处理，**不要猜、不要编**。
4. **信任语义层**：`prepare_query` 返回的 `draft_sql` 就是后端按注册口径生成的产物，默认直接采纳。一次 `prepare` + 一次 `execute_query` 拿到结果就作答；**不要**为了"验证正确性"反复重试 prepare、要求生成不同的 SQL、或用手写 ABS/改 ORDER BY 的方式另做一条查询。
5. 指标只允许使用第 4/5 节字典中注册过的；字典外的口径（如"毛利率""业务线"）一律不自行合成，向用户说明支持范围并引导换问法。
6. 数据时点 = 2026-08-31；本月 = 2026-08，上月 = 2026-07；"下月"指 plan_end 落在 2026-09。
7. **凭据红线**：只用 `login_vault(别名)` 登录；密码永不写入对话/参数/回答；涉及密码（改密、用户输入密码）时见第 2 节规则，必须提醒清理聊天记录。

## 1. 两个后端与工具命名

MCP 服务器在 dsh 中以 `mcp__<serverName>__<tool>` 暴露，按需直接调用，**无需猜测工具名**。

| 后端 | 覆盖领域 | 工具清单 |
|---|---|---|
| `mcp__bi_workbench__*` | 财务 / 项目 / MaaS | `login` `login_vault` `list_aliases` `change_password` `logout` `get_metric_catalog` `prepare_query` `execute_query` `run_alert_scan` `list_alerts` `list_audit_logs` `gen_report` `export_query` `list_exports` `etl_ingest` |
| `mcp__hr_backend__*` | 人力资源 | `login` `login_vault` `list_aliases` `change_password` `logout` `get_metric_catalog` `prepare_query` `execute_query` `run_hr_scan` `list_alerts` |

## 2. 账号与权限（vault 别名登录）

### 凭据安全红线（最高优先级，违反即为事故）

- 登录一律用 `login_vault(别名)`；**任何密码不得出现在对话文本、工具参数或回答中**——密码只存后端凭据库。
- 不确定可用账号时先调 `list_aliases`（无需登录）核对，绝不臆造账号或密码。
- 修改密码：仅当**用户本人主动给出新密码**时，调 `change_password(别名, 新密码)`；改完**不得复述密码**，且必须提醒用户清理包含密码的聊天记录。
- 本对话一旦出现过密码明文（含用户输入），回答末尾固定附一句安全提醒，建议删除/清理该会话。
- 绝不向用户索要密码；用户没给密码时一律走 `login_vault`。

### BI（bi_workbench）别名表 —— 推荐默认 `bi-admin`

| 别名 | 实际账号/角色 | 可见域 |
|---|---|---|
| bi-admin | admin（信息中心-管理员） | 全部；`list_audit_logs`/`list_exports` 仅此角色 |
| bi-finance | zhangmin（财务部） | finance.* + 项目域（客户字段脱敏） |
| bi-pm1 | liqiang（交付一部经理） | project.*（部门隔离，客户脱敏） |
| bi-pm2 | wangfang（交付二部经理） | project.*（部门隔离，客户脱敏） |
| bi-staff | xiaowang（市场部员工） | 仅 public.maas（模型成本/token） |

权限标签：`finance.*`=admin/finance；`project.*`=admin/pm/finance；`public.maas`=所有角色；`gen_report`=admin/finance。

### HR（hr_backend）别名表 —— 推荐默认 `hr-admin`

| 别名 | 实际账号/角色 | 可见域 |
|---|---|---|
| hr-admin | hr_admin（HR管理员 刘主任） | 全部表（含薪资 restricted.salary） |
| hr-mgr-dev | zhaoliu（研发部经理 赵六） | 仅月度在编表 + **仅本部门**；薪资不可见 |
| hr-mgr-sales | qianqi（销售部经理 钱七） | 同上 |

若用户以 hr_mgr 身份问"工资/人力成本/平均工资"，属权限不足（restricted.salary）——如实说明需 `hr-admin` 别名登录，不要返回假数据。

## 3. 标准流程（两后端一致）

1. 依据问题选择后端并 `login_vault(别名)` 登录（见第 2 节别名表，绝不用密码登录）。
2. 不确定口径时可先 `get_metric_catalog`（BI 不传 domain=全部；HR 直接取）核对真实指标/别名。
3. `prepare_query(question, ...)` 按返回的 `status` 处理：
   - `draft` → 调用 `ask_user_question` 把 draft_sql 与选项（如"确认执行 (Recommended) / 换种口径 / 取消"）渲染给用户；用户确认后再 `execute_query(request_id)`；
   - `clarified` → 把 options 原样列给用户挑选（**不要代替用户选**），按选择重新 prepare；
   - `none` → 指标不在字典中：改用字典内措辞重问，或明确告知不支持，严禁自行造 SQL/gen_report 硬凑；
   - `denied` → 权限不足：如实说明所需角色，建议换账号。
4. 得到结果后组织为简明中文回答；需要预警/清单时再调 `run_hr_scan`/`run_alert_scan`、`list_alerts`。

## 4. BI 指标字典（语义层真源，与 `get_metric_catalog` 一致）

维度事实：财务销售只有 **区域/客户/月** 三层；区域 = 华北/华东/华南/西南；时间轴 2024-09 ~ 2026-08。**没有"业务线"维度。**

**finance 财务域（单位：万元）**

| 指标 id | 名称 | 后端别名 | 粒度 | 口径 |
|---|---|---|---|---|
| revenue | 收入 | 销售额、销售收入 | 区域/客户/月 | SUM(revenue) |
| direct_cost | 直接成本 | 成本、直接成本 | 区域/客户/月 | SUM(direct_cost) |
| gross_profit | 毛利 | 毛利润、利润 | 区域/客户/月 | 收入 - 直接成本 |
| net_profit | 净利润 | 净利、利润 | 区域/月 | 毛利 - 费用 |
| collection | 回款 | 回款额、回款金额 | 区域/客户/月 | SUM(collection) |
| budget_gross_profit | 预算毛利 | 预算、预算额 | 区域/客户/月 | SUM(budget_gross_profit) |
| budget_deviation | 预算偏差率 | 预算偏差、偏差率 | 区域/客户/月 | (实际毛利-预算毛利)/预算毛利×100% |
| expense | 费用 | 期间费用、总费用 | 区域/类型/月 | SUM(amount)，类型=销售/管理/研发 |
| expense_rate | 费用率 | 费用占比 | 区域/月 | 费用/收入×100% |

**project 项目域**

| 指标 id | 名称 | 后端别名 | 粒度 | 口径 |
|---|---|---|---|---|
| milestone_rate | 里程碑完成率 | 完成率、里程碑达成率 | 项目/部门 | 已完成里程碑占比（%）；含客户字段脱敏 |
| delay_rate | 项目延期率 | 延期率 | 部门 | 已延期完成/已完成×100% |
| hours_deviation | 工时偏差率 | 工时偏差、工作量偏差 | 项目/部门 | (实际-计划)/计划×100% |
| risk_next_month | 下月延期风险数 | 延期风险、风险项目数 | 部门 | plan_end 在下月且含"进行中-延期"里程碑的项目数（个） |

**maas MaaS 域（单位：元/个）**

| 指标 id | 名称 | 后端别名 | 粒度 |
|---|---|---|---|
| llm_cost | 模型成本 | 成本、LLM成本、token费用 | 部门/日 |
| llm_tokens | Token消耗 | token数、用量 | 部门/日 |

## 5. HR 指标字典（语义层真源）

部门 = 研发/销售/客服/职能。

| 指标 id | 名称 | 后端别名 | 单位 | 权限 | 口径 |
|---|---|---|---|---|---|
| headcount | 在编人数 | 在编、人数、员工数、编制 | 人 | public.hr | 当月月末在编 |
| new_hires | 新入职 | 入职、入职人数、新增、新入职人数、招聘 | 人 | public.hr | 当月新入职 |
| attrition | 离职数 | 离职人数、离职数、流失人数 | 人 | public.hr | 当月离职 |
| attrition_rate | 离职率 | 流失率、离职比例 | % | public.hr | 离职/(在编+离职) |
| payroll_cost | 人力成本 | 薪资成本、薪酬总额、工资、薪酬 | 元 | restricted.salary | 当月薪资总额 |
| avg_salary | 平均工资 | 平均薪资、人均薪酬、人均工资 | 元 | restricted.salary | 当月人均月薪 |

## 6. 易错点与不支持口径（重要）

- **"毛利率"不是注册指标**：字典只有 `毛利`（金额）与 `费用率/预算偏差率`（比率）。不要自行除算或改名绕过；若要"利润率"类比率，须用户换问法或后续由后端新增指标。
- **财务无"业务线"维度**：只有区域/客户/月。问"各业务线"应澄清为按区域或按客户，否则属 none。
- **"利润"别名会同时命中 `毛利` 与 `净利润`**：prepare_query 应返回 `clarified`，把候选交给用户选，不代选。
- 用词要落在第 4/5 节别名上（如问"偏差"命中预算偏差率；"延期风险"命中 risk_next_month），命中失败时先 `get_metric_catalog` 自查措辞。
- HR 的 `离职率` ≠ `离职数`，`人力成本/平均工资` 属薪资权限，注意措辞与账号。
- **`budget_deviation` 排序语义**：数值为负 = 实际低于预算（未达标），为正 = 超过预算。"预算偏差最大/Top"默认取**负向偏差最大者**（演示数据埋点即为"实际毛利持续低于预算"的客户，如华北=恒信科技）。**不要**自行改成按绝对值/正向最大解读；若用户确实要另一种口径，先向用户澄清，再由后端按新问法生成，绝不手写 ABS 或改 ORDER BY 重查。
- **最大/最小/Top N 类问题**：排序逻辑已由语义层写进 draft_sql，直接采纳结果即可；不要怀疑排序对不对而追加查询验证。

## 7. 已验证可跑的推荐问法（可直接套用）

**BI（`login_vault(alias="bi-admin")`）**
- 华北区2026-08预算偏差最大的客户是谁（预期=负向偏差最大者：恒信科技，约 -28%）
- 华东区上月回款 / 本月各区域收入排行
- 交付一部下月延期风险项目数（或"下月有延期风险的项目有哪些"）
- 哪个部门本月模型成本增长最快 / 近7天模型成本 Top 部门

**HR（`login_vault(alias="hr-admin")`）**
- 客服部2026-08离职率
- 今年各部门离职人数 / 8月各部门在编人数

**跨后端组合**：先 BI 域问题，再 HR 域问题——每个后端独立 `login_vault`，互不影响。

## 8. 结果呈现与图表输出（配合 genui 插件）

本工作台已挂接 dsh 的 **genui** 渲染插件（OpenTiny 体系）。它的工作方式：插件会在**新会话开始时**注入一段提示词，教模型在合适时输出 **`schemaJson` 代码块**（内含界面/图表描述），浏览器把它渲染成可交互组件（柱/折线/饼/环/雷达/漏斗等）。规则：

1. **何时画**：`execute_query` 返回**多行聚合结果**（实体对比/排行/Top-N/时间趋势/结构占比）时优先用 genui 呈现；单值结论（如一个离职率）或一句话能讲清的不强行出图。
2. **格式纪律**：图表必须用 **genui 注入的 `schemaJson` 约定**输出——以会话里插件注入的示例/说明为准，**不要自造其他代码块协议**（如 `dsh-ui` 之类不属于本插件，渲染器不识别）。
3. **类型**：时间趋势→折线；占比→饼/环；对比/Top-N→柱状；其余按 genui 支持类型（雷达/漏斗/散点/瀑布等）自行匹配合适图型。
4. **数据纪律**：图中的数值必须逐项来自 `execute_query` 返回的真实行，不得编造/外推，也不得补第 4/5 节字典外的指标；负值按真实值画。
5. **图不是全部**：图只是回答的一部分，正文仍须给出指标口径、时间范围、过滤条件与文字结论。
6. 若会话中**没有**出现 genui 注入的 schemaJson 说明（例如会话早于插件启用而开启），就不要硬输出格式，退回纯文字 + Markdown 表格即可。
