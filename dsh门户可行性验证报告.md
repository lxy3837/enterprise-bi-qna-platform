# dsh 门户可行性验证报告

> 验证对象：DeepSeek Harness（deepseek-ai/deepseek-harness）**v0.1.3-alpha.1**（master 分支，2026-09 源码快照），本地目录 `deepseek-harness`。
> 验证目的：回答技术方案 v2.0 的三个悬而未决问题——① 一个实例挂多个 MCP 后端怎么配；② 工具权限与"用户确认卡"机制能否支撑"先展示 SQL 再执行"；③ 在其上做"门户级 UI（系统切换/管理页）+ 多角色"的成本。
> 方法：对 mcp-client、core/tools、interaction/user-approval、client(ui-layout/ui-approval/ui-chat/ui-tool)、sdk、identity、host/webserver、bundle 及各官方文档逐源码核实。

---

## 1. 结论速览

| 验证项 | 结果 | 一句话 |
| --- | --- | --- |
| ① 一个 dsh 挂多个 MCP 后端 | 🟢 **可行，工程化完备** | 多行配置即可，工具自动发现、`mcp__后端__工具` 命名、与原工具有同一套权限治理；但连接集合是**静态声明式**，运行时增删只能靠改配置热重载/重启（无管理命令） |
| ② 确认卡机制（先展示再执行） | 🟡 **底座可靠，需集成补 2 处** | 原生有 allow/ask/deny + "允许一次/拒绝" 确认卡 + fail-closed；但审批请求**不带工具参数**、无 per-user 策略，需自渲染 SQL 卡片 + 策略持久化 |
| ③ 门户级 UI + 多角色 | 🔴 **原生不支持，改造成本偏高** | 单机单用户、无账号/RBAC、无全局顶栏槽、无多页面路由；要做门户壳基本要 fork 改 ui-layout 或自建前端接入 |

**总体判断**：dsh 适合做"Agent 会话引擎 + 多 MCP 后端的宿主 + 交互原型验证"，**不适合直接当"统一门户"交付本体**。与技术方案 v2.0 的差异：原"门户首选 dsh"应下调为"dsh 仅做原型验证与交互参考，门户本体自建"。

---

## 2. 验证①：多 MCP 后端挂接 —— 可行

### 2.1 配置方式

架构事实：dsh 是 Cordis 插件合成模型，**一个 mcp-client 插件实例只连一个 MCP server**，多个后端 = 配置文件里插入多行同名插件实例。两种传输：`stdio`（本地进程）与 `streamable-http`（远程，带 headers 鉴权）。见 `deepseek-harness/packages/mcp/mcp-client/src/index.ts#L49-134`（StdioConfig/StreamableHttpConfig schema）与 `deepseek-harness/packages/acp/acp/src/mcp.ts#L36-74`。

官方示例（挂两个后端）：

```yaml
# cordis.patch.yml（用户层配置补丁）
- insert:
    - id: mcp-backend-finance
      name: '@deepseek-ai/dsh-mcp-client'
      config: { serverName: finance, transport: streamable-http,
                url: http://127.0.0.1:9001/mcp, headers: { Authorization: 'Bearer xxx' } }
    - id: mcp-backend-project
      name: '@deepseek-ai/dsh-mcp-client'
      config: { serverName: project, transport: stdio,
                command: python, args: ['-m', 'bi_backend.project_server'] }
```

（同形态示例见 `deepseek-harness/packages/mcp/mcp-client/README.md#L34-53`；启用方式：`dsh web --patch <file>` 或写入 `$DSH_HOME/cordis.patch.yml`）

- 工具注册名：`mcp__<serverName>__<rawName>`（`deepseek-harness/packages/mcp/mcp-client/src/tools.ts#L112-118`），权限规则按此稳定名长期有效。
- 注意事项：配置里**没有** Claude Code 风格的 `allowRemote` 字段（全仓无匹配）；远程 HTTP 只能显式 headers 鉴权，URL 只放行 http/https。

### 2.2 能力发现（tools/list）

启动即做、运行期增量更新，正好支撑门户"能力快照/自适应渲染"：

- 插件激活前 await 初次连接与同步（`deepseek-harness/packages/mcp/mcp-client/src/index.ts#L184-187`），用分页 `tools/list` 拉全量工具，注册进 ToolRuntime（`deepseek-harness/packages/mcp/mcp-client/src/tools.ts#L144-193`）。
- 监听 `notifications/tools/list_changed` → 排队整体 re-sync（`deepseek-harness/packages/mcp/mcp-client/src/connection.ts#L257-270`），重连成功也会刷新工具集。

### 2.3 局限性（影响"运行时热插拔"演示）

- 连接集合本质是**静态声明式组合**（bundle 层 → profile `cordis.patch.yml` → 机器级补丁 → `--patch`），启动时一次性 compose；改配置靠 live HMR（`patchReload: live`）或重启（`deepseek-harness/apps/cli/src/profile-boot.ts#L137-174`、`deepseek-harness/apps/cli/src/profile-boot.ts#L300-309`）。
- **没有**"运行时 add/remove server"的用户命令；运行时动态挂载只存在于编程路径：ACP 会话级 `mcpServers`（`deepseek-harness/packages/acp/acp/src/mcp.ts#L26-33`）或插件内 `ctx.plugin(McpClient, config)`。
- 影响：技术方案 v2.0 中"管理页注册新后端 → 前端零改动即插即用"的**即时热插拔演示**，需在门户自建一层调度（改配置 + 触发 reload，或按会话挂载），或把演示改为"注册→写入配置→热重载→门户出现新系统"。

---

## 3. 验证②：权限确认卡 —— 底座可靠，需补 2 处

### 3.1 原生机制（已有）

- 调用前判定三值：`PreToolDecision = allow | deny(reason) | ask(reason?)`（`deepseek-harness/packages/core/tools/src/index.ts#L580-584`）。
- 流水线：`tools/pre-execute` 瀑布 → 若 `ask` 走 `ApprovalService.request`（`deepseek-harness/packages/core/tools/src/index.ts#L1680-1720`）→ 放行 `dispatchScheduledExecution` → `postExecute` 收口。
- 审批策略会话级 `'ask' | 'never'`，无应答者时 **fail-closed 拒绝**；批准是**一次性** `allowed-once`，无"始终允许"（`deepseek-harness/packages/interaction/user-approval/src/index.ts#L60-66`、`deepseek-harness/packages/interaction/user-approval/src/index.ts#L258-298`）。
- 前端确认卡：标题显示 `reason` + 工具名，按钮 **拒绝 / 允许一次**（`deepseek-harness/packages/client/ui-approval/src/client/ApprovalPanel.tsx#L12-55`）。
- hooks 机制存在（Claude Code / Codex 桥，见 `deepseek-harness/packages/hooks/hook-protocol/src/types.ts#L89-137`）：`PreToolUse`→deny/ask、`PostToolUse`→block/附加上下文；**但不能改写工具入参与结果值**（设计如此）。

### 3.2 与"先展示 SQL 再执行"的差距

1. **审批请求有意不带工具参数**（`deepseek-harness/packages/interaction/user-approval/src/index.ts#L100-101`），卡片靠 callId 关联对话中已展示的调用补上下文；通用渲染只会提取 `args.command`（`deepseek-harness/packages/client/ui-chat/src/client/chat/ApprovalCommand.tsx#L16-40`）。→ SQL 工具若以 `{sql: ...}` 传参会取不到。**对策**：把待执行 SQL 拼进 `reason` 文本，或注册 `conversation.approval.detail` / `tool.call.toolview` 专属渲染（SQL 高亮卡片）。
2. **无 per-user 权限规则**，只有会话级 ask/never 与 per-scope 的 restrict/guard。→ 门户的"角色→指标标签/字段脱敏"细粒度权限仍按技术方案落在**各后端 MCP server 内部**（dsh 侧只做粗粒度"该工具要不要 ask"），分层不变、可行。

**结论**：确认卡的"拦截 → 展示 → 一次性放行 → 审计"骨架满足企业问数形态；集成层补"SQL 自渲染卡片"即可。后端的 pre-hook 链（AST/语义层/权限/成本）仍由 Python MCP server 自治，与 dsh 无关。

---

## 4. 验证③：门户级 UI + 用户体系 —— 原生不支持

### 4.1 UI 事实

- 技术栈 React 18 + Vite；浏览器端是 Cordis 应用，由几十个 `@deepseek-ai/dsh-client-*` UI 插件经 **slot 系统**组装（`deepseek-harness/packages/client/README.md#L25-75`）。
- 布局是**三栏 grid**：sidebar | conversation | details，**没有全局顶部栏**（`deepseek-harness/packages/client/ui-layout/src/client/AppFrame.tsx#L175-217`）；`root` 是唯一内置槽，可挂的子槽只有 sidebar/conversation/details/shell.overlay（`deepseek-harness/packages/client/ui-layout/src/client/index.ts#L36-88`）。
- 结论："顶部系统切换下拉框" **没有非改源码的全局挂点**。会话内的可用位置是 `conversation.session.header.actions/utilities`（会话级，不是门户级）。

### 4.2 用户体系事实

- identity 组只有 `anonymous-user-id`，**不识别用户、无账号体系**（`deepseek-harness/packages/identity/README.md#L10-26`）。
- web 访问是**本机设备信任**：loopback + launch token + 签名 cookie，拒绝 `--host 0.0.0.0`（`deepseek-harness/packages/client/connection/README.md#L35-63`、`deepseek-harness/docs/subsystems/web-server.md#L47`）。
- 即 dsh web = **单机单用户桌面形态**；"多角色/统一登录"需门户层自建，dsh 只有会话级权限预设。

### 4.3 可行的三条路线（成本从低到高）

| 路线 | 做法 | 成本/适用 |
| --- | --- | --- |
| A. 槽内扩展 | 用现有槽：`sidebar.footer.action`（面板）、`settings.plugins.tab`（配置卡片）、`conversation.session.header.utilities`（会话内入口） | 低成本过渡，**到不了门户级**（无全局顶栏/独立管理页） |
| B. fork 改 UI | MIT monorepo 直接 fork ui-layout 加 header 槽 + 新页面 + bundle 名册，重新 build | 中高成本，且绑定 alpha 版本，需长期维护分支 |
| C. 自建门户前端 | dsh 作引擎，stdio JSON-RPC SDK（TS/Python 官方双端）驱动会话（`deepseek-harness/packages/sdk/README.md#L6`）；门户自己写顶部切换/管理页/登录 | 门户可完全可控，复用 dsh 的 agent 循环/工具/会话能力 |

---

## 5. 对技术方案 v2.0 的结论与建议

### 5.1 原方案需修正处

| v2.0 原写法 | 修正 |
| --- | --- |
| 门户首选 dsh（Web UI 挂 3 后端 + 管理页/登录/审计/MaaS） | dsh 无法原生承载门户级 UI 与用户体系 → **门户本体自建**；dsh 降级为"原型验证工具 + 交互参考" |
| 管理页"运行时注册后端即插即用" | dsh 连接集合是静态声明式 → 演示改为"写配置→热重载→门户出现新系统"，或门户自建调度层 |

### 5.2 建议架构（最终交付）

```
自建门户前端（统一入口：顶部系统切换/问数/指标目录/消息中心/管理页[数据源注册/审计总览/MaaS看板]/登录与角色）
     │ 自己实现：会话 Agent 循环(DeepSeek function calling) + 多后端 MCP client + 确认卡/拒绝卡/澄清卡 + 门户级表
     ▼ 统一契约：标准 MCP tools/list | tools/call
Python MCP 后端池（可插拔）：① 财务 MySQL  ② 项目 MySQL  ③ 第三方模拟(JSON)
```

- 复用 dsh 的**设计形态**（多 MCP 客户端、确认卡三态、`tools/list` 能力发现），但用成熟组件自实现：Python `mcp` 客户端库已足够、agent 循环用 DeepSeek tool-calls 也就百行级、确认卡是自家前端组件。
- 想保留现成 Agent 能力的替代方案：**自建门户 + dsh 引擎化**（路线 C）——门户通过官方 SDK 驱动 dsh 会话，但要评估 SDK 会话与"确认卡数据"的可编程程度，建议另做一轮小验证再定。

### 5.3 建议的下一步（小步验证）

1. 先跑通 **Python FastMCP 后端池 + 命令行 MCP client 直连**（不碰 dsh）：验证 `tools/list` 能力发现 + 两个后端并行挂接 —— 这是门户的核心风险点，1 天内可验证。
2. 再跑 **dsh 试运行**（`npx @deepseek-ai/dsh web` + patch 挂 1 个后端），体验确认卡与多后端切换的真实手感，仅作交互参照。
3. 根据手感决定门户会话层是"自写"还是"SDK 驱动 dsh"。

---

## 6. 关键文件索引（便于后续引用）

- 多后端配置示例：`deepseek-harness/packages/mcp/mcp-client/README.md#L34-53`；schema：`deepseek-harness/packages/mcp/mcp-client/src/index.ts#L49-134`
- 工具发现/同步：`deepseek-harness/packages/mcp/mcp-client/src/tools.ts#L144-193`
- 权限判定/审批桥：`deepseek-harness/packages/core/tools/src/index.ts#L580-592`、`deepseek-harness/packages/core/tools/src/index.ts#L1680-1720`
- 确认卡 UI：`deepseek-harness/packages/client/ui-approval/src/client/ApprovalPanel.tsx#L12-55`
- UI 布局（无顶栏证据）：`deepseek-harness/packages/client/ui-layout/src/client/AppFrame.tsx#L175-217`
- 槽系统规范：`deepseek-harness/docs/subsystems/slots.md#L106-164`
- 身份/用户体系缺失证据：`deepseek-harness/packages/identity/README.md#L10-26`
- SDK 引擎化入口：`deepseek-harness/packages/sdk/README.md#L6`、`deepseek-harness/docs/user/guide/python-sdk.md#L90-106`
