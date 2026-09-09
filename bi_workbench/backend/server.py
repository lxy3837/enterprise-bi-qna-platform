# -*- coding: utf-8 -*-
"""
bi_workbench MCP Server: 平台化企业智能问数工作台 - 财务/项目 后端(受控查询)
运输: stdio (被 dsh / 任意 MCP 客户端挂接)
启动: python -m backend.server
"""
import csv
import io
import json
import time
import uuid

from fastmcp import FastMCP

from . import core

mcp = FastMCP("bi-workbench")

# 业务别名 -> (user_id, 说明)。凭据(vault)登录用; 明文密码只存在于 sys_user 凭据库。
BI_ALIASES = {
    "bi-admin":   ("admin",     "信息中心-管理员: 全域权限(含审计/报表/导出)"),
    "bi-finance": ("zhangmin",  "财务部-财务角色: finance.* + 项目域(客户脱敏)"),
    "bi-pm1":     ("liqiang",   "交付一部-项目经理: project.*(仅本部门)"),
    "bi-pm2":     ("wangfang",  "交付二部-项目经理: project.*(仅本部门)"),
    "bi-staff":   ("xiaowang",  "市场部-员工: 仅 maas 域(public.maas)"),
}


def _user(tok):
    u = core.get_user(tok)
    if not u:
        raise ValueError("未登录或会话已过期, 请先调用 login")
    return u


@mcp.tool()
def login(user_id: str, password: str) -> dict:
    """登录后端系统(账号来自 sys_user)。安全做法: 不要在此传密码, 改用 login_vault(别名)。
    别名目录见 list_aliases。本凭据接口仅供明确需要输入密码的交互场景使用。"""
    tok, err = core.login(user_id, password)
    if err:
        return {"ok": False, "error": err}
    u = core.get_user(tok)
    return {"ok": True, "session_token": tok, "user_id": u["user_id"],
            "name": u["name"], "role": u["role"], "dept": u["dept"]}


@mcp.tool()
def logout(session_token: str) -> dict:
    """退出登录"""
    core.logout(session_token)
    return {"ok": True}


@mcp.tool()
def list_aliases() -> dict:
    """凭据别名目录: 可用于 login_vault 的业务别名(不含任何密码)。
    模型在不确定可用账号时先调用本工具, 不要臆造账号或密码。"""
    return {"ok": True, "aliases": [{"alias": a, "user_id": v[0], "note": v[1]}
                                    for a, v in BI_ALIASES.items()]}


@mcp.tool()
def login_vault(alias: str) -> dict:
    """凭据保险库登录: 传业务别名(如 bi-admin/bi-finance/bi-pm1), 密码由后端凭据库校验。
    密码永不出后端, 对话/工具调用中不应出现任何密码。"""
    item = BI_ALIASES.get(alias)
    if not item:
        return {"ok": False, "error": f"未知别名 {alias!r}; 可用: " + ", ".join(BI_ALIASES)}
    tok, err = core.login_vault(item[0])
    if err:
        return {"ok": False, "error": err}
    u = core.get_user(tok)
    return {"ok": True, "session_token": tok, "alias": alias, "user_id": u["user_id"],
            "name": u["name"], "role": u["role"], "dept": u["dept"]}


@mcp.tool()
def change_password(session_token: str, target_alias: str, new_password: str) -> dict:
    """修改密码(vault): target_alias 传别名(或 user_id)。新密码由用户本人直接提供;
    仅本人或管理员(bi-admin)可改, 改后该账号旧会话全部失效。
    调用方禁止在响应中回显密码, 并应提醒用户清理包含密码的聊天记录。"""
    u = _user(session_token)
    item = BI_ALIASES.get(target_alias)
    target = item[0] if item else target_alias
    ok, err = core.change_password(u["user_id"], target, new_password)
    if err:
        return {"ok": False, "error": err}
    return {"ok": True, "changed": target,
            "note": "密码已更新(不在响应中回显), 旧会话已失效; 请提醒用户清理含密码的聊天记录"}


@mcp.tool()
def get_metric_catalog(session_token: str, domain: str = "") -> dict:
    """指标目录: 语义层已注册指标(口径/单位/血缘/权限标签)。domain=finance|project|maas, 空=全部"""
    _user(session_token)
    rows = [{"metric_id": m["metric_id"], "name": m["name"], "domain": m["domain"],
             "aliases": ",".join(m["aliases"]), "formula": m["formula"], "unit": m["unit"],
             "grain": m["grain"], "base_table": m["base_table"], "lineage": m["lineage"],
             "permission_tag": m["permission_tag"]}
            for m in core.SEM.rows if (not domain or m["domain"] == domain)]
    return {"ok": True, "count": len(rows), "catalog": rows}


def _analyze(question, user, selected_metric=""):
    """语义解析 -> 意图/指标。返回 (kind, payload, reason)
       kind: metric / intent / none / ambiguity / deny"""
    q = question
    if selected_metric:
        m = core.SEM.by_id.get(selected_metric)
        if m:
            return "metric", m, ""
    hits = core.SEM.match(q)
    if hits:
        # 歧义: 同一关键词命中多个指标(如"利润"=毛利/净利润)
        by_alias = {}
        for h in hits:
            by_alias.setdefault(h["alias"], []).append(h["metric_id"])
        amb = [v for v in by_alias.values() if len(v) > 1]
        if amb:
            cands = [core.SEM.by_id[vid]["name"] for vid in amb[0]]
            return "ambiguity", {"alias": [a for a, v in by_alias.items() if len(v) > 1][0],
                                 "metric_ids": amb[0], "names": cands}, \
                f"“{amb[0][0]}”存在多个口径, 请选择: " + " / ".join(cands)
        return "metric", core.SEM.by_id[hits[0]["metric_id"]], ""
    # 意图词(无注册指标也支持): 明细/风险等
    return "intent", None, ""


@mcp.tool()
def prepare_query(session_token: str, question: str, selected_metric: str = "") -> dict:
    """两步确认-第1步: 解析问题并生成 SQL 草稿, 过完 hook 链但不执行。
    返回 status: draft(可确认) / denied(拒答) / clarified(歧义, 需用 selected_metric 重试) /
                  none(需澄清问题)。返回含 hook 日志与草稿 SQL。"""
    t0 = time.time()
    u = _user(session_token)
    hook_log = []
    kind, payload, reason = _analyze(question, u, selected_metric)
    if kind == "ambiguity":
        core.write_audit(u, question, None, None, "clarified", payload["alias"])
        return {"ok": True, "status": "clarified", "question": question,
                "options": [{"metric_id": i, "name": core.SEM.by_id[i]["name"]}
                            for i in payload["metric_ids"]],
                "reason": reason}
    if kind == "none":
        return {"ok": True, "status": "none",
                "reason": "未识别到可计算的业务指标, 请说明想看哪个指标(参考指标目录)或更具体的对象"}

    metric = payload if kind == "metric" else None
    dims = core.parse_dims(question)
    tw = core.parse_time(question)

    if metric:
        # hook2-语义层: 权限标签检查(角色->标签)
        tag = metric["permission_tag"]
        role_allowed = {"finance.*": ("finance", "admin"),
                        "project.*": ("pm", "admin", "finance"),
                        "project.mask_client": ("pm", "admin"),
                        "public.maas": ("admin", "finance", "pm", "staff")}
        if u["role"] not in role_allowed.get(tag, (u["role"],)):
            hook_log.append(f"[hook3 权限] 指标 {metric['name']} 需 {tag}, 当前角色 {u['role']} 无权")
            core.write_audit(u, question, None, None, "denied", f"指标权限不足:{tag}")
            return {"ok": True, "status": "denied", "reason": f"没有权限查看指标「{metric['name']}」(需要 {tag})",
                    "hook_log": hook_log}
        sql, note = core.gen_sql(question, metric, dims, tw)
        hook_log.append(f"[hook2 语义层] 命中指标 {metric['name']}, 口径: {note}")
    else:
        # intent 未匹配时, 交由 SQL 白名单兜底(仅当含具体对象词, 示例: 问客户/项目明细由后续扩展)
        return {"ok": True, "status": "none",
                "reason": "该问题暂未映射到受控指标, 已拒绝生成任意 SQL(保证不越权)。"}

    if not sql:
        hook_log.append(f"[hook2 语义层] {note}")
        core.write_audit(u, question, None, None, "clarified", note)
        return {"ok": True, "status": "clarified", "options": [], "reason": note}

    # hook1-语法 / hook3-权限(表级) / hook4-行数: 预检草稿
    ok, eff, why = core.enforce_sql(sql, u["role"], u["dept"])
    if not ok:
        hook_log.append(f"[hook1 语法/安全] {why}")
        core.write_audit(u, question, sql, None, "blocked", why)
        return {"ok": True, "status": "denied", "reason": why, "hook_log": hook_log}
    hook_log.append(f"[hook1 语法] 单条SELECT/AST校验通过; [hook3 表级权限] 通过; [hook4] 自动补 LIMIT {core.AUTO_LIMIT}")

    request_id = f"rq{uuid.uuid4().hex}"
    core.REQUESTS[request_id] = dict(user_id=u["user_id"], question=question,
                                     draft_sql=eff, metric=metric["name"] if metric else "")
    return {"ok": True, "status": "draft", "request_id": request_id,
            "question": question, "metric": metric["name"], "draft_sql": eff,
            "note": note, "hook_log": hook_log,
            "confirm_hint": "请展示该 SQL 供用户确认(硬性约束3); 确认后可调用 execute_query(request_id, final_sql=确认后SQL)"}


def _checked_execute(u, draft_sql, final_sql, t0=None):
    """受控执行共用路径: enforce hooks -> 只读账号跑 -> 返回 dict(同 execute_query 成功结构)。
    失败写审计并返回 {ok,status:denied/error,reason}。供 execute_query 与 export_query 复用。"""
    t0 = t0 or time.time()
    ok, eff, why = core.enforce_sql(final_sql, u["role"], u["dept"])
    if not ok:
        core.write_audit(u, "", draft_sql, final_sql, "blocked", why,
                         elapse=int((time.time() - t0) * 1000))
        return {"ok": True, "status": "denied", "reason": f"最终SQL未通过安全校验: {why}"}
    c = core.conn("ro")
    try:
        with c.cursor() as cur:
            cur.execute("SET SESSION max_execution_time=%s", (core.EXEC_TIME_LIMIT_MS,))
            cur.execute(eff)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            n = cur.rowcount
    except Exception as e:
        core.write_audit(u, "", draft_sql, eff, "error", str(e),
                         elapse=int((time.time() - t0) * 1000))
        return {"ok": True, "status": "error", "reason": f"执行失败: {e}"}
    finally:
        c.close()
    if n > core.MAX_ROWS:
        rows = rows[:core.MAX_ROWS]
    return {"ok": True, "status": "success", "eff": eff, "cols": cols,
            "rows": rows, "n": n,
            "elapse": int((time.time() - t0) * 1000)}


def _chart_type(eff, cols):
    """只给图表类型建议, 不生成图表(渲染归 dsh/前端插件)。"""
    if "GROUP BY" not in eff.upper():
        return None
    return "line" if any(c in ("ym", "month", "ts") for c in cols) else "bar"


@mcp.tool()
def execute_query(session_token: str, request_id: str, final_sql: str) -> dict:
    """两步确认-第2步: 用户确认后的 SQL(可编辑) 再走一遍全链 hook, 通过后以只读账号执行。
    返回结果与口径; 任意一步被拦 => status=denied 且仅落审计不执行。"""
    t0 = time.time()
    u = _user(session_token)
    ctx = core.REQUESTS.get(request_id)
    if not ctx or ctx["user_id"] != u["user_id"]:
        return {"ok": False, "error": "request_id 无效或不属于当前用户, 请重新 prepare"}
    r = _checked_execute(u, ctx["draft_sql"], final_sql, t0)
    if r["status"] not in ("success",):
        return r
    core.write_audit(u, ctx["question"], ctx["draft_sql"], r["eff"], "approved", "OK",
                     rows=r["n"], elapse=r["elapse"])
    core.REQUESTS.pop(request_id, None)
    return {"ok": True, "status": "success",
            "columns": r["cols"],
            "rows": [list(map(str, row)) for row in r["rows"][:200]],
            "row_count": r["n"], "sql": r["eff"], "metric": ctx["metric"],
            "question": ctx["question"],
            "chart_type": _chart_type(r["eff"], r["cols"]),
            "note": "结果口径: " + ctx["question"]}


@mcp.tool()
def run_alert_scan(session_token: str) -> dict:
    """异常洞察/阈值预警: 执行规则扫描(环比暴跌/预算偏差/延期风险/成本突增), 新增告警写入消息中心。
    幂等: 同类型+标题只写一次。"""
    _user(session_token)
    added = core.scan_alerts()
    return {"ok": True, "added": added, "count": len(added),
            "note": "查看全部请调用 list_alerts"}


@mcp.tool()
def list_alerts(session_token: str) -> dict:
    """消息中心: 站内告警(异常洞察/阈值触发)"""
    _user(session_token)
    c = core.conn("ro")
    try:
        with c.cursor() as cur:
            cur.execute("SELECT id,ts,alert_type,title,content,level,dept,is_read "
                        "FROM alert_message ORDER BY ts DESC LIMIT 50")
            rows = [dict(zip(["id", "ts", "type", "title", "content", "level", "dept", "read"], r))
                    for r in cur.fetchall()]
        return {"ok": True, "alerts": rows}
    finally:
        c.close()


@mcp.tool()
def list_audit_logs(session_token: str, limit: int = 20) -> dict:
    """管理端: 查询审计日志(draft/final SQL 双记录)。仅 admin"""
    u = _user(session_token)
    if u["role"] != "admin":
        raise ValueError("仅管理员可查看审计日志")
    c = core.conn("ro")
    try:
        with c.cursor() as cur:
            cur.execute("SELECT id,ts,user_id,role,question,status,hook_result,row_count "
                        "FROM audit_log ORDER BY id DESC LIMIT %s", (min(int(limit), 100),))
            rows = [dict(zip(["id", "ts", "user", "role", "question", "status", "hook", "rows"], r))
                    for r in cur.fetchall()]
        return {"ok": True, "logs": rows}
    finally:
        c.close()


# ---------------------------------------------------------------- 报告/导出/ETL (纯后端能力)
REPORT_ROLES = {"finance", "admin"}


@mcp.tool()
def gen_report(session_token: str, report_type: str = "daily") -> dict:
    """经营快报生成(纯后端模板聚合, 不经过用户SQL所以不走enforce):
    daily=每日经营快报 / weekly=经营周报 / period=月度经期快报(含预算达成+下月延期风险预告)。
    仅 finance/admin 角色可调用; 产出 markdown 供展示与导出。"""
    u = _user(session_token)
    if u["role"] not in REPORT_ROLES:
        raise ValueError("无权限: 经营快报仅财务/管理员可见")
    if report_type not in ("daily", "weekly", "period"):
        raise ValueError("report_type 仅支持 daily/weekly/period")
    r = core.gen_report(report_type)
    core.write_audit(u, f"生成{r['title']}", "", "", "report", "OK", rows=0)
    return {"ok": True, "report_type": r["report_type"], "title": r["title"],
            "markdown": r["markdown"], "metrics": r["metrics"]}


@mcp.tool()
def export_query(session_token: str, final_sql: str, description: str = "") -> dict:
    """导出受控查询结果为 CSV: SQL 仍先走全链安全 hook(硬性约束2), 通过后只读执行;
    每次导出写入 export_log(谁/何时/行数/内容md5 留痕, 防泄密可追溯)。
    仅支持 SELECT; 最多导出 5000 行。"""
    u = _user(session_token)
    r = _checked_execute(u, "", final_sql)
    if r["status"] != "success":
        return r
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(r["cols"])
    for row in r["rows"]:
        w.writerow(list(map(str, row)))
    content = "\ufeff" + buf.getvalue()          # BOM: 兼容 Excel 中文
    md5 = core.write_export(u, "query", description or f"受控查询导出 {r['n']} 行", content)
    core.write_audit(u, description or "受控查询导出", "", r["eff"], "export", "OK", rows=r["n"])
    return {"ok": True, "status": "success", "exported_rows": r["n"], "content_md5": md5,
            "csv_preview": content[:500], "note": "导出已留痕 export_log, 仅管理员可查看记录"}


@mcp.tool()
def list_exports(session_token: str, limit: int = 30) -> dict:
    """导出日志(仅管理员): 每次受控导出的 用户/角色/时间/类型/行数/内容摘要"""
    u = _user(session_token)
    if u["role"] != "admin":
        raise ValueError("无权限: 导出日志仅管理员可查看")
    return {"ok": True, "exports": core.list_exports(limit)}


@mcp.tool()
def etl_ingest(session_token: str, source_type: str, source_path: str = "",
               mysql_options: str = "") -> dict:
    """ETL 数据接入演示: 将外部数据源接入 llm_usage(领域包三·智能问数用量), 由 bi_app 账号写入。
    source_type: csv | excel | api(模拟第三方计费API推送的JSON)
      传入 source_path=本地文件路径; 列: ts,dept,model,prompt_tokens,completion_tokens,cost,success,blocked
    source_type: mysql
      传入 mysql_options=JSON {"host","port","user","password","database","table"} 读取异构库同构表
    自动清洗/归一/行数上限5000, 全程写审计。"""
    u = _user(session_token)
    if u["role"] not in REPORT_ROLES:
        raise ValueError("无权限: ETL 接入仅财务/管理员可操作")
    kw = {}
    if source_type in ("csv", "excel", "api"):
        if not source_path:
            raise ValueError("csv/excel/api 数据源需要 source_path")
        kw["path"] = source_path
    elif source_type == "mysql":
        if not mysql_options:
            raise ValueError("mysql 数据源需要 mysql_options(JSON)")
        kw = json.loads(mysql_options)
    else:
        raise ValueError("source_type 仅支持 csv/excel/mysql/api")
    try:
        inserted, rejected, sample = core.ingest_llm_usage(source_type, kw)
    except Exception as e:
        core.write_audit(u, f"ETL[{source_type}]", "", "", "blocked", f"ETL失败: {e}", rows=0)
        return {"ok": False, "error": f"ETL 失败: {e}"}
    core.write_audit(u, f"ETL[{source_type}]", "", f"ingest_{inserted}", "approved", "ETL入库成功", rows=inserted)
    return {"ok": True, "source_type": source_type, "inserted": inserted, "rejected": rejected,
            "sample": sample, "note": f"共接入 {inserted} 行, 丢弃 {rejected} 行; 已可用指标 llm_cost 查询验证"}


if __name__ == "__main__":
    mcp.run(transport="stdio")
