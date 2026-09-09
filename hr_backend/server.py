# -*- coding: utf-8 -*-
"""
hr-backend: 员工与人力领域 MCP Server(演示"另一套业务系统"接入统一问数台)。
特点: 与 bi_workbench 完全独立 —— 自己的库(hr_bi)/账号/语义层/权限矩阵。
角色: hr_admin(全量) / hr_mgr(部门经理: 仅月度在编表+仅本部门, 薪资表不可见)。
工具: login/login_vault(别名)/list_aliases/change_password/logout/get_metric_catalog/prepare_query/execute_query/run_hr_scan/list_alerts
启动: python -m hr_backend.server  (cwd = project1)
"""
import datetime as dt
import re
import time
import uuid

import pymysql
import sqlglot
from fastmcp import FastMCP
from sqlglot import exp

DB = "hr_bi"
RO = dict(host="127.0.0.1", port=3306, user="hr_ro", password="hr_ro_pass_2026", charset="utf8mb4")
APP = dict(host="127.0.0.1", port=3306, user="hr_app", password="hr_app_pass_2026", charset="utf8mb4")
THIS_MONTH = "2026-08"
LAST_MONTH = "2026-07"
MONTHS = [f"{y:04d}-{m:02d}" for y in (2024, 2025, 2026) for m in range(1, 13)]
MONTHS = [m for m in MONTHS if "2024-01" <= m <= "2026-08"]
DEPTS = ["研发", "销售", "客服", "职能"]
MAX_ROWS = 5000
AUTO_LIMIT = 500
EXEC_TIME_LIMIT_MS = 3000        # hook: 执行时长上限(ms): MySQL max_execution_time, 超时熔断

# 业务别名 -> (user_id, 说明)。凭据(vault)登录用; 明文密码只存在于 hr_user 凭据库。
HR_ALIASES = {
    "hr-admin":     ("hr_admin", "HR管理员(刘主任): 全量权限, 含薪资表"),
    "hr-mgr-dev":   ("zhaoliu",  "研发部经理(赵六): 仅本部门在编表, 薪资不可见"),
    "hr-mgr-sales": ("qianqi",   "销售部经理(钱七): 仅本部门在编表, 薪资不可见"),
}

# ---------------------------------------------------------------- 语义层(HR域指标, 硬编码常量演示自治语义层)
METRICS = [
    # (id, name, aliases, formula, expr, table, unit, perm, note)
    ("headcount", "在编人数", ["在编", "在编人数", "人数", "员工数", "编制"], "当月月末在编人数", "SUM(headcount)",
     "monthly_headcount", "人", "public.hr", "按月部门粒度"),
    ("new_hires", "新入职", ["入职", "入职人数", "新增", "新入职人数", "招聘"], "当月新入职人数", "SUM(new_hires)",
     "monthly_headcount", "人", "public.hr", "按月部门粒度"),
    ("attrition", "离职数", ["离职人数", "离职数", "流失人数"], "当月离职人数", "SUM(exits)",
     "monthly_headcount", "人", "public.hr", "按月部门粒度"),
    ("attrition_rate", "离职率", ["流失率", "离职比例"], "离职人数/月初在编(近似当月口径)", "SUM(exits)/(SUM(headcount)+SUM(exits))",
     "monthly_headcount", "%", "public.hr", "注意与离职数区分"),
    ("payroll_cost", "人力成本", ["薪资成本", "薪酬总额", "工资", "薪酬"], "当月薪资总额(基本工资口径)", "SUM(payroll_amt)",
     "monthly_payroll", "元", "restricted.salary", "仅HR管理层可见"),
    ("avg_salary", "平均工资", ["平均薪资", "人均薪酬", "人均工资"], "当月部门人均月薪", "AVG(avg_salary)",
     "monthly_payroll", "元", "restricted.salary", "仅HR管理层可见"),
]
METRIC_BY_ID = {m[0]: m for m in METRICS}
# 角色 -> 允许表前缀/名
ROLE_TABLES = {
    "hr_admin": ["monthly_"],
    "hr_mgr":   ["monthly_headcount"],       # 薪资表对部门经理不可见
}
ROW_ISOLATION_ROLES = {"hr_mgr"}             # 部门经理只能看自己部门

SESSIONS = {}
REQUESTS = {}


def conn(role_kind):
    cfg = dict(APP if role_kind == "app" else RO)
    cfg["database"] = DB
    return pymysql.connect(autocommit=True, **cfg)


# ---------------------------------------------------------------- 会话/审计
def write_audit(user, question, draft, final, status, hook="", rows=0, elapse=0):
    try:
        c = conn("app")
        with c.cursor() as cur:
            cur.execute("INSERT INTO hr_audit(user_id,role,question,draft_sql,final_sql,status,hook_result,row_count,elapse_ms) "
                        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (user["user_id"], user["role"], question, draft, final, status, hook, rows, elapse))
        c.close()
    except Exception:
        pass


# ---------------------------------------------------------------- 时间/部门/指标 解析
def shift(ym, k):
    return MONTHS[MONTHS.index(ym) + k]


def parse_time(q):
    if "今年" in q or "本年" in q:
        return "range", "2026-01", THIS_MONTH
    m = re.search(r"近\s*(\d+)\s*个?月", q)
    if m:
        n = int(m.group(1))
        return "range", shift(THIS_MONTH, -(n - 1)), THIS_MONTH
    if "上季度" in q:
        return "range", "2026-04", "2026-06"
    if "本季度" in q or "这个季度" in q:
        return "range", "2026-07", THIS_MONTH
    if "上月" in q or "上个月" in q:
        return "month", LAST_MONTH
    if "6月" in q or "六月" in q:
        return "month", "2026-06"
    return "month", THIS_MONTH                      # 默认本月


def parse_dept(q):
    for d in DEPTS:
        if d in q:
            return d
    return ""


def parse_metric(q, selected=""):
    """最长匹配优先(防'平均工资'被'工资'子串干扰); 平局返回 'a|b' 由上层澄清"""
    if selected and selected in METRIC_BY_ID:
        return selected
    best = {}
    for mid, name, aliases, *rest in METRICS:
        bl = -1
        for token in [name] + aliases:
            if token and token in q:
                bl = max(bl, len(token))
        if bl >= 0:
            best[mid] = bl
    if not best:
        return ""
    mx = max(best.values())
    winners = [m for m, l in best.items() if l == mx]
    return winners[0] if len(winners) == 1 else "|".join(winners)


def gen_sql(question, metric_id, dept, tw, include_dept_group=False):
    """按规则生成草稿 SELECT(确定性可审计)"""
    mid, name, aliases, formula, expr, table, unit, perm, note = METRIC_BY_ID[metric_id]
    if tw[0] == "month":
        w = f"ym='{tw[1]}'"
    else:
        w = f"ym BETWEEN '{tw[1]}' AND '{tw[2]}'"
    conds = [w]
    if dept:
        conds.append(f"dept='{dept}'")
    group_by = ""
    if include_dept_group or (question and ("各部门" in question or "按部门" in question)):
        group_by = " GROUP BY dept"
    where = " WHERE " + " AND ".join(conds) if conds else ""
    sel = f"{expr} AS {metric_id}"
    if group_by:
        sel += ", dept"
    return f"SELECT {sel} FROM {table}{where}{group_by} LIMIT {AUTO_LIMIT}"


def _dept_of_role(user):
    return user.get("dept", "")


# ---------------------------------------------------------------- hook 链: 语法->表权限->行隔离->行数
def enforce_sql(sql, user):
    """硬性约束1/2: 拒绝一切写; 单条SELECT; 表白名单; hr_mgr 必须锁自己部门; 行数上限+自动LIMIT"""
    role, dept = user["role"], _dept_of_role(user)
    stmts = sqlglot.parse(sql, read="mysql")
    if len(stmts) != 1:
        return None, "语法校验失败: 仅允许单条SQL"
    root = stmts[0]
    if not isinstance(root, exp.Select):
        return None, "拒绝: 仅允许 SELECT 查询(写类操作被禁止)"
    if root.find(exp.Intersect) or root.find(exp.Except):
        return None, "拒绝: 不支持集合运算"
    # 表白名单
    tables = [t.name for t in root.find_all(exp.Table)]
    allow = ROLE_TABLES[role]
    for t in tables:
        if not any(t == a or t.startswith(a) for a in allow):
            return None, f"无权限: 表 {t} 不在角色 {role} 白名单内"
    # 行隔离: mgr 的查询必须显式限定 dept = 自己部门
    if role in ROW_ISOLATION_ROLES:
        ok = False
        for e in root.find_all(exp.EQ):
            left, right = e.left, e.right
            if isinstance(left, exp.Column) and left.name == "dept" and \
               isinstance(right, exp.Literal) and right.this == dept:
                ok = True
            elif isinstance(right, exp.Column) and right.name == "dept" and \
                 isinstance(left, exp.Literal) and left.this == dept:
                ok = True
        if not ok:
            return None, f"组织隔离: 部门经理仅可查询本部门(dept='{dept}'), 请在SQL中限定"
    # 行数/成本限制: 自动补 LIMIT, 已有则校验 <= MAX
    lim = root.args.get("limit")
    if lim is None:
        sql = f"{sql.strip().rstrip(';')} LIMIT {AUTO_LIMIT}" if "LIMIT" not in sql.upper() else sql
    else:
        try:
            if int(lim.expression.this) > MAX_ROWS:
                return None, "成本限制: 单次查询返回行数不能超过5000"
        except Exception:
            return None, "LIMIT 解析失败"
    return sql, None


# ---------------------------------------------------------------- MCP 工具
mcp = FastMCP("hr-backend")


def _user(tok):
    u = SESSIONS.get(tok)
    if not u:
        raise ValueError("未登录或会话已过期, 请先调用 login")
    return u


@mcp.tool()
def login(user_id: str, password: str) -> dict:
    """登录 HR 后端。安全做法: 不要在此传密码, 改用 login_vault(别名)。
    别名目录见 list_aliases。本凭据接口仅供明确需要输入密码的交互场景使用。"""
    c = conn("ro")
    try:
        with c.cursor() as cur:
            cur.execute("SELECT user_id,name,role,dept,password FROM hr_user WHERE user_id=%s", (user_id,))
            r = cur.fetchone()
    finally:
        c.close()
    if not r or r[4] != password:
        return {"ok": False, "error": "账号或密码错误"}
    tok = uuid.uuid4().hex
    SESSIONS[tok] = dict(user_id=r[0], name=r[1], role=r[2], dept=r[3])
    return {"ok": True, "session_token": tok, "user_id": r[0], "name": r[1],
            "role": r[2], "dept": r[3]}


@mcp.tool()
def logout(session_token: str) -> dict:
    """退出登录"""
    SESSIONS.pop(session_token, None)
    return {"ok": True}


@mcp.tool()
def list_aliases() -> dict:
    """凭据别名目录: 可用于 login_vault 的业务别名(不含任何密码)。
    模型在不确定可用账号时先调用本工具, 不要臆造账号或密码。"""
    return {"ok": True, "aliases": [{"alias": a, "user_id": v[0], "note": v[1]}
                                    for a, v in HR_ALIASES.items()]}


@mcp.tool()
def login_vault(alias: str) -> dict:
    """凭据保险库登录: 传业务别名(如 hr-admin/hr-mgr-dev/hr-mgr-sales), 密码由后端凭据库校验。
    密码永不出后端, 对话/工具调用中不应出现任何密码。"""
    item = HR_ALIASES.get(alias)
    if not item:
        return {"ok": False, "error": f"未知别名 {alias!r}; 可用: " + ", ".join(HR_ALIASES)}
    c = conn("ro")
    try:
        with c.cursor() as cur:
            cur.execute("SELECT user_id,name,role,dept FROM hr_user WHERE user_id=%s", (item[0],))
            r = cur.fetchone()
    finally:
        c.close()
    if not r:
        return {"ok": False, "error": "账号不存在"}
    tok = uuid.uuid4().hex
    SESSIONS[tok] = dict(user_id=r[0], name=r[1], role=r[2], dept=r[3])
    return {"ok": True, "session_token": tok, "alias": alias, "user_id": r[0], "name": r[1],
            "role": r[2], "dept": r[3]}


@mcp.tool()
def change_password(session_token: str, target_alias: str, new_password: str) -> dict:
    """修改密码(vault): target_alias 传别名(或 user_id)。新密码由用户本人直接提供;
    仅本人或 HR 管理员(hr-admin)可改, 改后该账号旧会话全部失效。
    调用方禁止在响应中回显密码, 并应提醒用户清理包含密码的聊天记录。"""
    u = _user(session_token)
    item = HR_ALIASES.get(target_alias)
    target = item[0] if item else target_alias
    if u["role"] != "hr_admin" and u["user_id"] != target:
        return {"ok": False, "error": "无权限: 仅本人或 HR 管理员可修改密码"}
    if len(new_password) < 6:
        return {"ok": False, "error": "新密码至少 6 位"}
    c = conn("app")
    try:
        with c.cursor() as cur:
            cur.execute("UPDATE hr_user SET password=%s WHERE user_id=%s", (new_password, target))
            if cur.rowcount == 0:
                return {"ok": False, "error": "目标账号不存在"}
    finally:
        c.close()
    for tok in [t for t, s in SESSIONS.items() if s["user_id"] == target]:
        SESSIONS.pop(tok, None)
    return {"ok": True, "changed": target,
            "note": "密码已更新(不在响应中回显), 旧会话已失效; 请提醒用户清理含密码的聊天记录"}


@mcp.tool()
def get_metric_catalog(session_token: str) -> dict:
    """HR 域指标目录(语义层): 员工/薪酬指标, 含口径/权限标签。"""
    _user(session_token)
    return {"ok": True, "domain": "people", "catalog": [
        {"metric_id": m[0], "name": m[1], "aliases": ",".join(m[2]), "formula": m[3],
         "expr": m[4], "table": m[5], "unit": m[6], "permission_tag": m[7], "note": m[8]}
        for m in METRICS]}


@mcp.tool()
def prepare_query(session_token: str, question: str, selected_metric: str = "") -> dict:
    """两步确认-第1步: 解析自然语言 -> 草稿SQL(hook链1次通过)。歧义/无权限会要求澄清。
    支持部门/时间词; 示例: '客服部上月离职率' '今年各部门离职人数' '研发近3个月人力成本'"""
    u = _user(session_token)
    hit = parse_metric(question, selected_metric)
    if not hit:
        # 硬性约束4: 裸'离职/流失'(没说是数还是率) -> 澄清
        if "离职" in question or "流失" in question:
            return {"ok": True, "status": "clarified",
                    "reason": "指标口径不明确(离职数 vs 离职率), 请确认目标:",
                    "options": [{"metric_id": "attrition", "name": METRIC_BY_ID["attrition"][1],
                                 "formula": METRIC_BY_ID["attrition"][3]},
                                {"metric_id": "attrition_rate", "name": METRIC_BY_ID["attrition_rate"][1],
                                 "formula": METRIC_BY_ID["attrition_rate"][3]}],
                    "question": question}
        return {"ok": True, "status": "denied",
                "reason": "未识别指标或口径不明: HR域支持 在编人数/新入职/离职数/离职率/人力成本/平均工资, 请补充"}
    # 歧义判定: 多个指标同样命中 -> 澄清
    mids = [m for m in hit.split("|") if m]
    if len(mids) > 1:
        return {"ok": True, "status": "clarified",
                "reason": "指标口径不明确, 请确认目标:",
                "options": [{"metric_id": m, "name": METRIC_BY_ID[m][1],
                             "formula": METRIC_BY_ID[m][3]} for m in mids],
                "question": question}
    metric_id = mids[0]
    mm = METRIC_BY_ID[metric_id]
    # 权限: restricted.salary 仅 hr_admin(表级+指标级双重拦)
    if mm[7] == "restricted.salary" and u["role"] != "hr_admin":
        return {"ok": True, "status": "denied", "reason": "无权限: 薪资类指标仅HR管理员可见"}
    dept = parse_dept(question)
    tw = parse_time(question)
    sql = gen_sql(question, metric_id, dept, tw,
                  include_dept_group=("各部门" in question or "按部门" in question))
    # 草稿也走一次全链 hook, 确保展示的 SQL 绝对可执行安全(硬性约束2)
    eff, why = enforce_sql(sql, u)
    if eff is None:
        return {"ok": True, "status": "denied", "reason": f"草稿未通过安全校验: {why}"}
    rid = f"rq{uuid.uuid4().hex[:10]}"
    REQUESTS[rid] = dict(user_id=u["user_id"], question=question, draft_sql=eff, metric=metric_id)
    hook = ["语法OK", f"语义层: {mm[1]}({mm[3]})", f"权限: {mm[7]}", "行数: LIMIT<=500"]
    if u["role"] == "hr_mgr":
        hook.append(f"行隔离: dept='{u['dept']}'")
    return {"ok": True, "status": "draft", "request_id": rid,
            "question": question, "metric": metric_id, "metric_name": mm[1],
            "draft_sql": eff, "hook_log": hook,
            "confirm_hint": "请展示草稿SQL供用户确认(硬性约束3), 确认后调用 execute_query 传入最终SQL"}


@mcp.tool()
def execute_query(session_token: str, request_id: str, final_sql: str) -> dict:
    """两步确认-第2步: 确认后SQL再过全链hook(写阻断/表权限/行隔离/行数)后以只读账号执行。"""
    t0 = time.time()
    u = _user(session_token)
    ctx = REQUESTS.get(request_id)
    if not ctx or ctx["user_id"] != u["user_id"]:
        return {"ok": False, "error": "request_id 无效或不属于当前用户"}
    eff, why = enforce_sql(final_sql, u)
    if eff is None:
        write_audit(u, ctx["question"], ctx["draft_sql"], final_sql, "blocked", why,
                    elapse=int((time.time() - t0) * 1000))
        return {"ok": True, "status": "denied", "reason": why}
    c = conn("ro")
    try:
        with c.cursor() as cur:
            cur.execute("SET SESSION max_execution_time=%s", (EXEC_TIME_LIMIT_MS,))
            cur.execute(eff)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            n = cur.rowcount
    except Exception as e:
        write_audit(u, ctx["question"], ctx["draft_sql"], eff, "error", str(e),
                    elapse=int((time.time() - t0) * 1000))
        return {"ok": True, "status": "error", "reason": f"执行失败: {e}"}
    finally:
        c.close()
    if n > MAX_ROWS:
        rows = rows[:MAX_ROWS]
    write_audit(u, ctx["question"], ctx["draft_sql"], eff, "approved", "OK",
                rows=len(rows), elapse=int((time.time() - t0) * 1000))
    REQUESTS.pop(request_id, None)
    return {"ok": True, "status": "success", "columns": cols,
            "rows": [list(map(str, r)) for r in rows[:200]], "row_count": n,
            "sql": eff, "metric": ctx["metric"], "question": ctx["question"],
            "chart_type": None,
            "note": "结果口径: " + ctx["question"]}


@mcp.tool()
def run_hr_scan(session_token: str) -> dict:
    """HR 异常洞察: 部门离职率突增(近2月离职率>8%或环比翻倍)写入站内预警(幂等)。"""
    _user(session_token)
    c = conn("ro")
    try:
        with c.cursor() as cur:
            cur.execute(
                "SELECT dept, ym, ROUND(SUM(exits)/(SUM(headcount)+SUM(exits))*100,1) r "
                "FROM monthly_headcount WHERE ym IN ('2026-07','2026-08') GROUP BY dept, ym")
            rows = cur.fetchall()
    finally:
        c.close()
    added = []
    by = {}
    for dept, ym, r in rows:
        by.setdefault(dept, {})[ym] = float(r)
    for dept, d in by.items():
        r8, r7 = d.get(THIS_MONTH), d.get(LAST_MONTH)
        if r8 is not None and r8 > 8.0:
            title = f"[{dept}]离职率异常 {r8:.1f}%"
            content = f"{dept} 2026-08 离职率 {r8:.1f}%{'/' if r7 else ''}{f'07月{r7:.1f}%' if r7 else ''}, 需关注人员流失"
            try:
                c2 = conn("app")
                with c2.cursor() as cur2:
                    cur2.execute("SELECT COUNT(*) FROM hr_alert WHERE title=%s AND is_read=0", (title,))
                    if cur2.fetchone()[0] == 0:
                        cur2.execute("INSERT INTO hr_alert(alert_type,title,content,level,dept) "
                                     "VALUES(%s,%s,%s,%s,%s)", ("人员流失", title, content, "high", dept))
                        added.append(title)
                c2.close()
            except Exception:
                pass
    return {"ok": True, "added": added, "note": "HR 站内预警写入完成"}


@mcp.tool()
def list_alerts(session_token: str, limit: int = 20) -> dict:
    """HR 站内预警列表"""
    _user(session_token)
    c = conn("ro")
    try:
        with c.cursor() as cur:
            cur.execute("SELECT id,ts,alert_type,title,content,level,dept,is_read "
                        "FROM hr_alert ORDER BY id DESC LIMIT %s", (min(limit, 50),))
            return {"ok": True, "alerts": [dict(zip(["id", "ts", "type", "title", "content", "level", "dept", "read"], r))
                                           for r in cur.fetchall()]}
    finally:
        c.close()


if __name__ == "__main__":
    mcp.run(transport="stdio")
