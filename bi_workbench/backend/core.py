# -*- coding: utf-8 -*-
"""
bi_workbench 后端核心: 连接/会话/语义层/受控SQL/hook链/审计
规则 SQL 引擎先落地(不依赖网络), LLM 增强见 llm_gen.py(C阶段)。
"""
import datetime as dt
import csv
import hashlib
import io
import json
import random
import re
import uuid

import pymysql
import sqlglot
from sqlglot import exp

# ---------------------------------------------------------------- 配置
THIS_MONTH = "2026-08"          # 数据时点"本月"
LAST_MONTH = "2026-07"
MAX_ROWS = 5000                 # hook4 行数上限
EXEC_TIME_LIMIT_MS = 3000       # hook4 执行时长上限(ms): MySQL max_execution_time, 超时熔断
AUTO_LIMIT = 500                # 默认自动补 LIMIT
CONF = {
    "ro":  dict(host="127.0.0.1", port=3306, user="bi_ro",  password="bi_ro_pass_2026", charset="utf8mb4"),
    "app": dict(host="127.0.0.1", port=3306, user="bi_app", password="bi_app_pass_2026", charset="utf8mb4"),
}
DB = "bi_workbench"

REGIONS = ["华北", "华东", "华南", "西南"]
DEPT_LIST = ["研发", "交付一", "交付二", "实施"]
EXPENSE_TYPES = ["销售费用", "管理费用", "研发费用"]

# 角色 -> 可访问表前缀 (安全矩阵, LLM无裁量权)
ROLE_TABLES = {
    "admin":   ["finance_", "project_", "llm_usage", "audit_", "alert_", "metric_"],
    "finance": ["finance_", "project_", "metric_", "alert_"],
    "pm":      ["project_", "metric_", "alert_"],
    "staff":   ["llm_usage", "metric_", "alert_"],
}
MASK_ROLES = {"pm"}               # 这些角色看 project.client 需打码
# 组织隔离: 这些角色查询 project_* 自动注入 dept = 登录用户部门
ROW_ISOLATION_ROLES = {"pm"}

# ---------------------------------------------------------------- 会话
SESSIONS = {}   # token -> {user_id,name,role,dept,created}
REQUESTS = {}   # request_id -> {user, question, draft_sql, metric, note}  (确认前草稿上下文)


def conn(kind):
    c = dict(CONF[kind])
    c["database"] = DB
    return pymysql.connect(autocommit=True, **c)


def now():
    return dt.datetime.now()


# ---------------------------------------------------------------- 语义层
class Metrics:
    """从 metric_definition 加载; parse: 文本 -> 指标候选(支持歧义)"""
    def __init__(self):
        self.rows = []          # 全量
        self.by_id = {}
        self.alias_idx = {}     # alias -> [metric_id,...]

    def load(self):
        c = conn("ro")
        try:
            with c.cursor() as cur:
                cur.execute("SELECT metric_id,domain,name,aliases,formula,unit,grain,"
                            "base_table,lineage,permission_tag,mask_fields FROM metric_definition")
                for r in cur.fetchall():
                    mid, domain, name, aliases, formula, unit, grain, bt, lin, perm, mask = r
                    m = dict(metric_id=mid, domain=domain, name=name,
                             aliases=[a.strip() for a in aliases.split(",") if a.strip()],
                             formula=formula, unit=unit, grain=grain, base_table=bt,
                             lineage=lin, permission_tag=perm,
                             mask_fields=[f.strip() for f in mask.split(",") if f.strip()])
                    self.rows.append(m)
                    self.by_id[mid] = m
                    for a in m["aliases"]:
                        self.alias_idx.setdefault(a, []).append(mid)
        finally:
            c.close()
        # 实体字典
        c = conn("ro")
        try:
            with c.cursor() as cur:
                cur.execute("SELECT DISTINCT customer FROM finance_sales")
                self.customers = [r[0] for r in cur.fetchall()]
                cur.execute("SELECT DISTINCT client FROM project_info")
                self.clients = [r[0] for r in cur.fetchall()]
        finally:
            c.close()

    def match(self, text):
        """返回 [{metric_id,name,alias},...] 命中的指标(一个词命中多个=歧义候选)"""
        hits = []
        seen = set()
        for alias in sorted(self.alias_idx, key=len, reverse=True):
            if alias and alias in text:
                for mid in self.alias_idx[alias]:
                    key = (mid, alias)
                    if key not in seen:
                        seen.add(key)
                        hits.append({"metric_id": mid, "name": self.by_id[mid]["name"],
                                     "alias": alias})
        return hits


SEM = Metrics()
SEM.load()


# ---------------------------------------------------------------- 时间/维度解析
MONTH_SEQ = [f"{y:04d}-{m:02d}" for y in range(2025, 2027) for m in range(1, 13)]


def shift(ym, k):
    i = MONTH_SEQ.index(ym)
    return MONTH_SEQ[i + k]


def parse_time(q):
    """返回 (kind, start, end)。kind in month/range; 财务默认本月"""
    if "上季度" in q or "上个季度" in q:
        return "range", "2026-04", "2026-06"
    if "本季度" in q or "这个季度" in q or "本季" in q:
        return "range", "2026-07", "2026-09"
    if "今年" in q or "本年" in q:
        return "range", "2026-01", "2026-08"
    m = re.search(r"近\s*(\d+)\s*个?月", q)
    if m:
        n = int(m.group(1))
        return "range", shift(THIS_MONTH, -(n - 1)), THIS_MONTH
    if "上月" in q or "上个月" in q:
        return "month", LAST_MONTH, LAST_MONTH
    if "本月" in q or "当月" in q:
        return "month", THIS_MONTH, THIS_MONTH
    return "month", THIS_MONTH, THIS_MONTH


def has_dim(q, words):
    for w in words:
        if w in q:
            return w
    return None


def parse_dims(q):
    d = dict(region=None, customer=None, dept=None, etype=None)
    d["region"] = has_dim(q, REGIONS)
    d["customer"] = has_dim(q, SEM.customers)
    d["dept"] = has_dim(q, DEPT_LIST)
    d["etype"] = has_dim(q, EXPENSE_TYPES)
    return d


def ym_filter(start, end, col="ym"):
    if start == end:
        return f"{col} = '{start}'"
    return f"{col} BETWEEN '{start}' AND '{end}'"


# ---------------------------------------------------------------- 规则 SQL 生成
def gen_sql(question, metric, dims, time_win, forced_metric=None):
    """metric: dict(metric_id...) 已确认的指标; 返回 (sql, 口径note)"""
    mid = metric["metric_id"]
    kind, start, end = time_win
    reg, cust, dept, etype = dims["region"], dims["customer"], dims["dept"], dims["etype"]
    tf = ym_filter(start, end)
    top = ""
    if "top" not in question and ("最多" in question or "前" in question or "下降最多" in question or "最高" in question):
        top = " ORDER BY v DESC LIMIT 10"
    dim_col, dim_sel = None, None
    if mid in ("revenue", "direct_cost", "gross_profit", "collection", "budget_gross_profit"):
        table = "finance_sales"
        expr = {"revenue": "SUM(revenue)", "direct_cost": "SUM(direct_cost)",
                "gross_profit": "SUM(revenue-direct_cost)", "collection": "SUM(collection)",
                "budget_gross_profit": "SUM(budget_gross_profit)"}[mid]
        col, label = "v", metric["name"]
        gcols, gsel = [], ""
        if cust:
            gcols.append("customer"); gsel += ", customer"
        if reg:
            gcols.append("region"); gsel += ", region"
        sql = f"SELECT {expr} AS v{gsel} FROM {table} WHERE {tf}"
        if cust:
            sql += f" AND customer='{cust}'"
        if reg:
            sql += f" AND region='{reg}'"
        if gcols:
            sql += " GROUP BY " + ", ".join(gcols)
        return sql, f"指标口径:{metric['formula']}"
    if mid == "budget_deviation":
        cond = tf + (f" AND region='{reg}'" if reg else "")
        sql = (f"SELECT (SUM(revenue-direct_cost)-SUM(budget_gross_profit))"
               f"/SUM(budget_gross_profit)*100 AS v, customer, region FROM finance_sales "
               f"WHERE {cond} GROUP BY customer, region HAVING v < 0 "
               f"ORDER BY v ASC LIMIT 10")
        return sql, "指标口径: 预算偏差率=(实际毛利-预算毛利)/预算毛利"
    if mid == "expense":
        sql = f"SELECT SUM(amount) AS v FROM finance_expense WHERE {tf}"
        if etype:
            sql = f"SELECT amount AS v, expense_type FROM finance_expense WHERE {tf} AND expense_type='{etype}' ORDER BY amount DESC"
        elif reg:
            sql = f"SELECT amount AS v, expense_type FROM finance_expense WHERE {tf} AND region='{reg}' ORDER BY amount DESC"
        elif "各月" in question or "走势" in question:
            sql = f"SELECT ym, SUM(amount) AS v FROM finance_expense WHERE {tf} GROUP BY ym ORDER BY ym"
        return sql, metric["formula"]
    if mid == "expense_rate":
        cond = f"fe.region='{reg}'" if reg else "1=1"
        sql = (f"SELECT SUM(fe.amount)/SUM(fs.revenue)*100 AS v, fe.region FROM finance_expense fe "
               f"JOIN finance_sales fs ON fe.ym=fs.ym AND fe.region=fs.region "
               f"WHERE {tf} AND {cond} GROUP BY fe.region")
        return sql, "费用率=费用/收入"
    if mid == "net_profit":
        if cust:
            return None, "费用无客户维度, 净利润无法按客户分摊 -> 口径不明确, 请改用 毛利 或按 区域/整体 口径"
        cond = tf + (f" AND fs.region='{reg}'" if reg else "")
        sql = (f"SELECT (SUM(fs.revenue)-SUM(fs.direct_cost)-IFNULL(fe.amt,0)) AS v, fs.region "
               f"FROM finance_sales fs LEFT JOIN "
               f"(SELECT region, SUM(amount) amt FROM finance_expense WHERE {tf} GROUP BY region) fe "
               f"ON fs.region=fe.region WHERE {cond} GROUP BY fs.region")
        return sql, "净利润=毛利-费用(按区域归集)"
    # ---------------- 项目域
    if mid == "milestone_rate":
        extra = f" AND i.dept='{dept}'" if dept else ""
        sql = (f"SELECT ROUND(SUM(CASE WHEN m.status LIKE '已完成%' THEN 1 ELSE 0 END)/COUNT(*)*100,1) AS v, i.dept "
               f"FROM project_milestone m JOIN project_info i ON m.project_id=i.project_id "
               f"WHERE i.actual_end IS NULL{extra} GROUP BY i.dept")
        return sql, "里程碑完成率=已完成里程碑/全部里程碑(仅统计进行中项目)"
    if mid == "delay_rate":
        extra = f" AND dept='{dept}'" if dept else ""
        sql = (f"SELECT ROUND(SUM(actual_end>plan_end)/COUNT(*)*100,1) AS v, dept FROM project_info "
               f"WHERE actual_end IS NOT NULL{extra} GROUP BY dept")
        return sql, "延期率=延期完成项目/已完成项目"
    if mid == "hours_deviation":
        extra = f" AND dept='{dept}'" if dept else ""
        sql = (f"SELECT ROUND(SUM(actual_hours-plan_hours)/SUM(plan_hours)*100,1) AS v, dept "
               f"FROM project_info WHERE actual_end IS NOT NULL{extra} GROUP BY dept")
        return sql, "工时偏差=(实际-计划)/计划"
    if mid == "risk_next_month":
        extra = f" AND pi.dept='{dept}'" if dept else ""
        sql = (f"SELECT pi.dept, COUNT(DISTINCT pi.project_id) AS v FROM project_milestone pm "
               f"JOIN project_info pi ON pm.project_id=pi.project_id "
               f"WHERE pm.status='进行中-延期' AND pi.actual_end IS NULL "
               f"AND pi.plan_end BETWEEN '2026-09-01' AND '2026-09-30'{extra} GROUP BY pi.dept")
        return sql, "下月延期风险=下月到期且存在'进行中-延期'里程碑的项目"
    if mid == "llm_cost":
        sql = (f"SELECT dept, SUM(cost) AS v FROM llm_usage "
               f"WHERE ts>='2026-08-25' GROUP BY dept ORDER BY v DESC")
        return sql, "模型成本=SUM(cost), 本周(08-25~31)"
    return None, "暂不支持该指标/口径"


# ---------------------------------------------------------------- 安全(hook1语法 + hook3权限)
def enforce_sql(sql, role, dept=""):
    """返回 (ok, rewrite_sql, reason)。顺序: 语法AST -> 禁写/白名单 -> 隔离/脱敏改写 -> LIMIT"""
    if not sql or not sql.strip():
        return False, sql, "SQL 为空"
    if re.search(r"(insert|update|delete|drop|alter|truncate|create|grant|replace|call|rename)\b",
                 sql, re.I):
        return False, sql, "检测到写类操作, 系统仅允许 SELECT (硬性约束1)"
    try:
        tree = sqlglot.parse_one(sql, read="mysql")
    except Exception as e:
        return False, sql, f"SQL 语法校验失败: {e}"
    if not isinstance(tree, exp.Select):
        return False, sql, "仅允许单条 SELECT 查询"
    # 禁用多语句(如 `; DROP`)
    if ";" in sql.rstrip().rstrip(";"):
        tail = sql.rstrip().rstrip(";")
        if ";" in tail:
            return False, sql, "不允许多语句"
    tables = [t.name for t in tree.find_all(exp.Table)]
    allowed_prefixes = ROLE_TABLES.get(role, [])
    for t in tables:
        if not any(t.startswith(p) for p in allowed_prefixes):
            return False, sql, f"表 {t} 不在角色 {role} 的可访问范围, 拒绝查询"
    rew = sql
    # 行隔离: pm 查 project_* 自动注入 dept=登录部门 (AST 层合并条件, 无 WHERE 也能注入)
    if role in ROW_ISOLATION_ROLES and dept:
        if any(t.name.startswith("project_") for t in tree.find_all(exp.Table)):
            tree = tree.where(f"dept='{dept}'")
            rew = tree.sql(dialect="mysql")
    # 字段脱敏: pm 等角色查询 project.client 列 -> 打码(仅替换 SELECT 投影中的裸列, 防重复包裹)
    if role in MASK_ROLES:
        m = re.search(r"(?is)^(\s*SELECT\b.*?\bFROM\b)", rew)
        if m and re.search(r"(?i)\bclient\b", m.group(1)) and "CONCAT(LEFT(client" not in m.group(1):
            proj = m.group(1)
            newp = re.sub(r"(?i)\bclient\b", "CONCAT(LEFT(client,2),'***') AS client", proj, count=1)
            rew = rew.replace(proj, newp, 1)
    # LIMIT 兜底(hook4): 无 limit 自动补
    if "limit" not in rew.lower():
        rew = f"{rew.rstrip().rstrip(';')} LIMIT {AUTO_LIMIT}"
    return True, rew, ""


def row_check(cursor, sql):
    """预检行数成本(hook4): 超限拒绝"""
    try:
        cursor.execute(f"SELECT COUNT(*) FROM ({sql.rstrip().rstrip(';')}) _t")
        n = cursor.fetchone()[0]
    except Exception:
        return None
    return n


# ---------------------------------------------------------------- 审计(hook-post)
def write_audit(user, question, draft, final, status, hook_result="", rows=0, elapse=0):
    try:
        c = conn("app")
        with c.cursor() as cur:
            cur.execute("INSERT INTO audit_log(user_id,role,question,draft_sql,final_sql,status,"
                        "hook_result,row_count,elapse_ms) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (user["user_id"], user["role"], question, draft, final, status,
                         hook_result, rows, elapse))
        c.close()
    except Exception:
        pass


def write_alert(atype, title, content, level="medium", dept=""):
    try:
        c = conn("app")
        with c.cursor() as cur:
            cur.execute("INSERT INTO alert_message(alert_type,title,content,level,dept) "
                        "VALUES(%s,%s,%s,%s,%s)", (atype, title, content, level, dept))
        c.close()
    except Exception:
        pass


# ---------------------------------------------------------------- 异常洞察/告警扫描
def scan_alerts():
    """按预置规则扫描异常并写入 alert_message(幂等, 同 type+title 只写一次)。
    规则与 seed.py 演示埋点对齐:
      R1 环比暴跌(客户收入较上月 <=-50%): 命中 F1 中科智达
      R2 预算偏差(本月实际毛利 vs 预算 <=-15%): 命中 F2 恒信/蓝海/云帆
      R3 下月延期风险(9月到期+进行中-延期里程碑): 命中 P1 研发/交付一/交付二
      R4 LLM成本突增(本周 vs 上周 部门成本 +150%): 命中 M1 交付二
      R5 同比骤降(客户收入 vs 去年同期 <=-40%): 命中 F1 中科智达(同比口径)
      R6 客户集中度(2026-08 单客户占区域收入 >=40%): 命中 华南
    返回新增告警列表(供调用方展示)。"""
    added = []
    seen = set()
    c = conn("ro")
    try:
        with c.cursor() as cur:
            cur.execute("SELECT alert_type, title FROM alert_message")
            seen = {(t, ti) for t, ti in cur.fetchall()}
    finally:
        c.close()

    def add(atype, title, content, level="medium", dept=""):
        if (atype, title) in seen:
            return
        write_alert(atype, title, content, level, dept)
        seen.add((atype, title))
        added.append({"type": atype, "title": title, "level": level, "dept": dept})

    # R1 客户收入环比暴跌
    c = conn("ro")
    try:
        with c.cursor() as cur:
            cur.execute(
                "SELECT t.customer, t.region, t.v, p.v, ROUND((t.v-p.v)/p.v*100,1) chg "
                "FROM (SELECT customer,region,SUM(revenue) v FROM finance_sales "
                "      WHERE ym='2026-08' GROUP BY customer,region) t "
                "JOIN (SELECT customer,region,SUM(revenue) v FROM finance_sales "
                "      WHERE ym='2026-07' GROUP BY customer,region) p "
                "  ON t.customer=p.customer AND t.region=p.region "
                "HAVING chg <= -50 ORDER BY chg")
            for cust, region, cv, pv, chg in cur.fetchall():
                add("环比暴跌", f"客户[{cust}]收入环比骤降{abs(float(chg)):.0f}%",
                    f"{region}客户 {cust} 本月收入 {float(cv):.0f} 万元, 较上月 {float(pv):.0f} 万元 "
                    f"下降 {abs(float(chg)):.1f}%(触发>-50%红线), 建议核查丢单/验收/回款问题", "high", region)
    finally:
        c.close()

    # R2 本月预算偏差(客户级, 实际毛利<预算)
    c = conn("ro")
    try:
        with c.cursor() as cur:
            cur.execute(
                "SELECT customer, region, SUM(revenue-direct_cost) gp, SUM(budget_gross_profit) bg, "
                "ROUND((SUM(revenue-direct_cost)-SUM(budget_gross_profit))/SUM(budget_gross_profit)*100,1) dev "
                "FROM finance_sales WHERE ym='2026-08' GROUP BY customer, region "
                "HAVING dev <= -15 ORDER BY dev LIMIT 6")
            for cust, region, gp, bg, dev in cur.fetchall():
                add("预算偏差", f"客户[{cust}]预算毛利缺口{abs(float(dev)):.0f}%",
                    f"{region}客户 {cust} 本月实际毛利 {float(gp):.0f} 万元, 低于预算 {float(bg):.0f} 万元 "
                    f"{abs(float(dev)):.1f}%(>15%缺口), 建议复核报价与成本控制", "medium", region)
    finally:
        c.close()

    # R3 下月(2026-09)到期项目的延期风险(按部门)
    c = conn("ro")
    try:
        with c.cursor() as cur:
            cur.execute(
                "SELECT pi.dept, COUNT(DISTINCT pi.project_id) n FROM project_milestone pm "
                "JOIN project_info pi ON pm.project_id=pi.project_id "
                "WHERE pm.status='进行中-延期' AND pi.actual_end IS NULL "
                "AND pi.plan_end BETWEEN '2026-09-01' AND '2026-09-30' GROUP BY pi.dept")
            for dept, n in cur.fetchall():
                add("延期风险", f"[{dept}]下月有{n}个项目存在延期风险",
                    f"{dept} 有 {n} 个项目计划 2026-09 交付但存在'进行中-延期'关键里程碑, 建议提前介入资源",
                    "high", dept)
    finally:
        c.close()

    # R4 LLM成本突增(本周 08-25~31 vs 上周 08-18~24, 部门成本)
    c = conn("ro")
    try:
        with c.cursor() as cur:
            cur.execute(
                "SELECT t.dept, ROUND((t.c-p.c)/p.c*100,1) g "
                "FROM (SELECT dept, SUM(cost) c FROM llm_usage WHERE ts>='2026-08-25' GROUP BY dept) t "
                "JOIN (SELECT dept, SUM(cost) c FROM llm_usage "
                "      WHERE ts BETWEEN '2026-08-18' AND '2026-08-24' GROUP BY dept) p "
                "  ON t.dept=p.dept HAVING g >= 150 ORDER BY g DESC LIMIT 3")
            for dept, g in cur.fetchall():
                add("成本突增", f"[{dept}]LLM调用成本环比+{float(g):.0f}%",
                    f"{dept} 本周 LLM token 成本较上周增长 {float(g):.0f}%(触发>150%红线), 建议检查是否有大模型批量任务",
                    "medium", dept)
    finally:
        c.close()

    # R5 同比骤降(客户收入 vs 去年同期 <= -40%)
    c = conn("ro")
    try:
        with c.cursor() as cur:
            cur.execute(
                "SELECT t.customer, t.region, t.v, p.v, ROUND((t.v-p.v)/p.v*100,1) yoy "
                "FROM (SELECT customer,region,SUM(revenue) v FROM finance_sales "
                "      WHERE ym='2026-08' GROUP BY customer,region) t "
                "JOIN (SELECT customer,SUM(revenue) v FROM finance_sales "
                "      WHERE ym='2025-08' GROUP BY customer) p USING(customer) "
                "HAVING yoy <= -40 ORDER BY yoy")
            for cust, region, cv, pv, yoy in cur.fetchall():
                add("同比骤降", f"客户[{cust}]收入同比骤降{abs(float(yoy)):.0f}%",
                    f"{region}客户 {cust} 本月收入 {float(cv):.0f} 万元, 较去年同期({pv:.0f} 万元) "
                    f"下降 {abs(float(yoy)):.1f}%(同比<-40%红线), 建议结合环比暴跌结论一并排查",
                    "high", region)
    finally:
        c.close()

    # R6 客户集中度(2026-08 单一客户占区域收入 >= 40%)
    c = conn("ro")
    try:
        with c.cursor() as cur:
            cur.execute(
                "SELECT region, top_cust, top_v, tot, ROUND(top_v/tot*100,1) pct FROM "
                "(SELECT region, customer top_cust, SUM(revenue) top_v "
                "   FROM finance_sales WHERE ym='2026-08' GROUP BY region, customer) t "
                "JOIN (SELECT region, SUM(revenue) tot FROM finance_sales "
                "      WHERE ym='2026-08' GROUP BY region) g USING(region) "
                "HAVING pct >= 40 ORDER BY pct DESC")
            for region, cust, top_v, tot, pct in cur.fetchall():
                add("客户集中", f"[{region}]单一客户占比{pct:.0f}%",
                    f"{region} 区域收入 {float(tot):.0f} 万元中, 客户 {cust} 占 {pct:.1f}%(>40%红线), "
                    f"收入集中度高, 建议分散客户结构以降低依赖风险", "medium", region)
    finally:
        c.close()
    return added


# ---------------------------------------------------------------- 报告/经期快报
def _one(conn_c, sql, args=None):
    """执行单值/首行查询(后端模板聚合, 不经用户 SQL 所以不走 enforce)"""
    with conn_c.cursor() as cur:
        cur.execute(sql, args or ())
        row = cur.fetchone()
        return row


def gen_report(report_type="daily", dept=""):
    """生成经营快报 markdown(日报/周报/经期)。纯后端聚合模板, 权限由调用层控制。
    report_type: daily=每日经营快报, weekly=经营周报, period=月度经期快报(含预算达成与下月预告)"""
    ro = conn("ro")
    try:
        # 1) 经营总览: 本月 vs 上月
        rows = {}
        for ym in (LAST_MONTH, THIS_MONTH):
            r = _one(ro, "SELECT IFNULL(SUM(revenue),0), IFNULL(SUM(revenue-direct_cost),0), "
                         "IFNULL(SUM(collection),0), IFNULL(SUM(budget_gross_profit),0), "
                         "IFNULL(SUM(revenue)*0+SUM(direct_cost),0) FROM finance_sales WHERE ym=%s", (ym,))
            rev, gp, col, bgp, _ = (float(x) for x in r)
            fee = _one(ro, "SELECT IFNULL(SUM(amount),0) FROM finance_expense WHERE ym=%s", (ym,))[0]
            rows[ym] = dict(rev=rev, gp=gp, col=col, bgp=bgp, fee=float(fee))
        cur, prev = rows[THIS_MONTH], rows[LAST_MONTH]
        chg = lambda a, b: ((a - b) / b * 100) if b else 0.0
        # 2) 预算偏差总览 + 最差客户
        worst = []
        with ro.cursor() as curx:
            curx.execute(
                "SELECT customer, region, ROUND((SUM(revenue-direct_cost)-SUM(budget_gross_profit))"
                "/SUM(budget_gross_profit)*100,1) d FROM finance_sales WHERE ym=%s "
                "GROUP BY customer, region ORDER BY d ASC LIMIT 5", (THIS_MONTH,))
            worst = [list(x) for x in curx.fetchall()]
        # 3) 环比跌幅 top5
        drops = []
        with ro.cursor() as curx:
            curx.execute(
                "SELECT t.customer, t.region, ROUND((t.v-p.v)/p.v*100,1) c FROM "
                "(SELECT customer,region,SUM(revenue) v FROM finance_sales WHERE ym=%s GROUP BY customer,region) t "
                "JOIN (SELECT customer,region,SUM(revenue) v FROM finance_sales WHERE ym=%s GROUP BY customer,region) p "
                "ON t.customer=p.customer AND t.region=p.region ORDER BY c ASC LIMIT 5",
                (THIS_MONTH, LAST_MONTH))
            drops = [list(x) for x in curx.fetchall()]
        # 4) 项目域: 进行中数/延期率/下月风险
        risk = []
        with ro.cursor() as curx:
            curx.execute(
                "SELECT pi.dept, COUNT(DISTINCT pi.project_id) FROM project_milestone pm "
                "JOIN project_info pi ON pm.project_id=pi.project_id "
                "WHERE pm.status='进行中-延期' AND pi.actual_end IS NULL "
                "AND pi.plan_end BETWEEN '2026-09-01' AND '2026-09-30' GROUP BY pi.dept")
            risk = [list(x) for x in curx.fetchall()]
        delay = _one(ro, "SELECT ROUND(SUM(actual_end>plan_end)/COUNT(*)*100,1), COUNT(*) FROM project_info "
                         "WHERE actual_end IS NOT NULL")
        delay_rate, done_cnt = float(delay[0] or 0), int(delay[1])
        # 5) MaaS: 本周成本与 tokens(部门 top)
        maas = []
        with ro.cursor() as curx:
            curx.execute(
                "SELECT dept, ROUND(SUM(cost),2), SUM(prompt_tokens+completion_tokens) FROM llm_usage "
                "WHERE ts>='2026-08-25' GROUP BY dept ORDER BY 2 DESC")
            maas = [list(x) for x in curx.fetchall()]
        total_cost = sum(float(x[1]) for x in maas)
        total_tok = sum(int(x[2]) for x in maas)
    finally:
        ro.close()

    gp_rate = cur["gp"] / cur["rev"] * 100 if cur["rev"] else 0
    col_rate = cur["col"] / cur["rev"] * 100 if cur["rev"] else 0
    dev = chg(cur["gp"], cur["bgp"])
    title = {"daily": "每日经营快报", "weekly": "经营周报", "period": "月度经期快报"}.get(report_type, "经营快报")
    md = [f"## {title}（数据时点 2026-08）", ""]
    md.append("### 一、经营总览（本月 vs 上月）")
    md.append("")
    md.append(f"| 指标 | 2026-08 | 2026-07 | 环比 |")
    md.append("|---|---|---|---|")
    md.append(f"| 收入(万元) | {cur['rev']:,.0f} | {prev['rev']:,.0f} | {chg(cur['rev'], prev['rev']):+.1f}% |")
    md.append(f"| 毛利(万元) | {cur['gp']:,.0f} | {prev['gp']:,.0f} | {chg(cur['gp'], prev['gp']):+.1f}% |")
    md.append(f"| 毛利率 | {gp_rate:.1f}% | {prev['gp'] / prev['rev'] * 100 if prev['rev'] else 0:.1f}% | - |")
    md.append(f"| 回款(万元) | {cur['col']:,.0f} | {prev['col']:,.0f} | {chg(cur['col'], prev['col']):+.1f}% |")
    md.append(f"| 回款率 | {col_rate:.1f}% | - | - |")
    md.append(f"| 期间费用(万元) | {cur['fee']:,.0f} | {prev['fee']:,.0f} | {chg(cur['fee'], prev['fee']):+.1f}% |")
    md.append("")
    md.append("### 二、预算达成")
    md.append("")
    md.append(f"- 实际毛利 {cur['gp']:,.0f} 万元，预算毛利 {cur['bgp']:,.0f} 万元，达成率 {(cur['gp']/cur['bgp']*100) if cur['bgp'] else 0:.1f}%（缺口偏差 {dev:+.1f}%）")
    if worst:
        md.append("- 预算缺口最大客户：")
        for cust, region, d in worst:
            md.append(f"  - {region}·{cust}：偏差 {float(d):.1f}%")
    md.append("")
    md.append("### 三、异常与风险提示")
    md.append("")
    if drops:
        md.append("- 收入环比跌幅 Top：")
        for cust, region, c_ in drops:
            md.append(f"  - {region}·{cust}：{float(c_):+.1f}%")
    if risk:
        md.append("- 下月(2026-09)到期延期风险：")
        for dept, n in risk:
            md.append(f"  - {dept}：{n} 个项目")
    if report_type == "period":
        md.append(f"- 历史项目延期率 {delay_rate:.1f}%（已完成 {done_cnt} 个）")
    md.append("")
    md.append("### 四、智能问数/MaaS 用量（本周）")
    md.append("")
    md.append(f"- LLM 调用成本合计 {total_cost:.2f} 元，Token 消耗 {total_tok:,}；部门分布：")
    for dept, cost, tok in maas[:4]:
        md.append(f"  - {dept}：{float(cost):.2f} 元 / {int(tok):,} tokens")
    md.append("")
    if report_type == "period":
        md.append("> 本快报用于月度经营回顾，预算达成与下月风险需结合明细跟踪。")
    return {"report_type": report_type, "title": title, "markdown": "\n".join(md),
            "metrics": {"revenue": cur["rev"], "gross_profit": cur["gp"], "collection": cur["col"],
                        "budget_dev": round(dev, 1), "risk_depts": len(risk)}}


LOGIN_FAILS = {}        # user_id -> 连续失败次数
ALERTED_FAILS = set()   # 已告警的账号(避免刷屏, 成功后复位)


def login(user_id, password):
    c = conn("ro")
    try:
        with c.cursor() as cur:
            cur.execute("SELECT user_id,name,role,dept,password FROM sys_user WHERE user_id=%s", (user_id,))
            r = cur.fetchone()
        if not r or r[4] != password:
            n = LOGIN_FAILS.get(user_id, 0) + 1
            LOGIN_FAILS[user_id] = n
            if n >= 3 and user_id not in ALERTED_FAILS:
                ALERTED_FAILS.add(user_id)
                write_alert("异常访问", f"[{user_id}]连续登录失败 {n} 次",
                            f"检测到账号 {user_id} 连续 {n} 次登录失败, 疑似暴力尝试; 已留痕并通知管理员",
                            "high", "系统")
            return None, "账号或密码错误"
        LOGIN_FAILS.pop(user_id, None)
        ALERTED_FAILS.discard(user_id)
        tok = uuid.uuid4().hex
        SESSIONS[tok] = dict(user_id=r[0], name=r[1], role=r[2], dept=r[3],
                             created=dt.datetime.now().isoformat())
        return tok, None
    finally:
        c.close()


def logout(tok):
    return SESSIONS.pop(tok, None)


def get_user(tok):
    return SESSIONS.get(tok)


def login_vault(user_id):
    """凭据保险库登录: 仅凭已解析的 user_id 签发 token。
    供 login_vault 工具使用; 密码只存在于 sys_user 凭据库, 永不进入 LLM 上下文/对话。"""
    c = conn("ro")
    try:
        with c.cursor() as cur:
            cur.execute("SELECT user_id,name,role,dept FROM sys_user WHERE user_id=%s", (user_id,))
            r = cur.fetchone()
    finally:
        c.close()
    if not r:
        return None, "账号不存在"
    LOGIN_FAILS.pop(user_id, None)
    ALERTED_FAILS.discard(user_id)
    tok = uuid.uuid4().hex
    SESSIONS[tok] = dict(user_id=r[0], name=r[1], role=r[2], dept=r[3],
                         created=dt.datetime.now().isoformat())
    return tok, None


def change_password(actor, target, new_password):
    """修改 sys_user 密码(vault 改密)。仅本人或 admin 可改; 改后该账号旧会话全部失效。
    新密码由用户本人直接提供, 后端落库; 调用方不得回显。"""
    if len(new_password) < 6:
        return None, "新密码至少 6 位"
    if actor != "admin" and actor != target:
        return None, "无权限: 仅本人或管理员可修改密码"
    c = conn("app")
    try:
        with c.cursor() as cur:
            cur.execute("UPDATE sys_user SET password=%s WHERE user_id=%s", (new_password, target))
            if cur.rowcount == 0:
                return None, "目标账号不存在"
    finally:
        c.close()
    for tok in [t for t, s in SESSIONS.items() if s["user_id"] == target]:
        SESSIONS.pop(tok, None)
    return True, None


# ---------------------------------------------------------------- 导出日志(留痕)
def write_export(user, export_type, description, content):
    """导出内容只留 md5 摘要(不存全量), 行数与说明入 export_log。"""
    md5 = hashlib.md5(content.encode("utf-8")).hexdigest()
    row_count = max(content.count("\n"), 0)
    try:
        c = conn("app")
        try:
            with c.cursor() as cur:
                cur.execute("INSERT INTO export_log(user_id,role,export_type,description,row_count,content_md5) "
                            "VALUES(%s,%s,%s,%s,%s,%s)",
                            (user["user_id"], user["role"], export_type, description, row_count, md5))
        finally:
            c.close()
    except Exception:
        pass
    return md5


def list_exports(limit=30):
    """导出留痕列表(供管理端)"""
    c = conn("ro")
    try:
        with c.cursor() as cur:
            cur.execute("SELECT id,ts,user_id,role,export_type,description,row_count,content_md5 "
                        "FROM export_log ORDER BY id DESC LIMIT %s", (limit,))
            return [dict(zip(["id", "ts", "user", "role", "type", "desc", "rows", "md5"], r))
                    for r in cur.fetchall()]
    finally:
        c.close()


# ---------------------------------------------------------------- ETL: 外部数据接入(演示)
ETL_FIELDS = ["ts", "dept", "model", "prompt_tokens", "completion_tokens", "cost", "success", "blocked"]
DEPT_DICT = dict(zip(["研发部", "交付一部", "交付二部", "实施部", "研发", "交付一", "交付二", "实施", "其他"], 
                     ["研发", "交付一", "交付二", "实施", "研发", "交付一", "交付二", "实施", "其他"]))


def _norm_row(src):
    """原始行(dict) -> llm_usage 规范字段; 缺失或非法抛 ValueError"""
    ts = src.get("ts") or src.get("time")
    dept = str(src.get("dept") or src.get("department") or "").strip()
    if not ts or not dept:
        raise ValueError(f"缺少必填字段 ts/dept: {src}")
    ts = str(ts).replace("T", " ")[:19]
    dept = DEPT_DICT.get(dept, dept)
    cost = float(src.get("cost") or 0)
    return {
        "ts": ts, "dept": dept,
        "model": str(src.get("model") or "deepseek-chat"),
        "prompt_tokens": int(src.get("prompt_tokens") or 0),
        "completion_tokens": int(src.get("completion_tokens") or 0),
        "cost": round(cost, 6),
        "success": 0 if str(src.get("success", "1")) in ("0", "false", "False") else 1,
        "blocked": 0 if str(src.get("blocked", "0")) in ("0", "false", "False") else 1,
    }


def _read_csv(src_kw):
    path = src_kw["path"]
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


def _read_excel(src_kw):
    path = src_kw["path"]
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise RuntimeError("ETL-Excel 需要 openpyxl: pip install openpyxl")
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    head = [str(h).strip() if h is not None else "" for h in rows[0]]
    out = []
    for r in rows[1:]:
        out.append({head[i]: (r[i] if i < len(r) else None) for i in range(len(head))})
    return out


def _read_mysql(src_kw):
    """从另一 MySQL 数据源读取(模拟异构库对接, 供演示)"""
    try:
        c = pymysql.connect(host=src_kw["host"], port=int(src_kw.get("port", 3306)),
                            user=src_kw["user"], password=src_kw["password"],
                            database=src_kw["database"], charset="utf8mb4")
        try:
            with c.cursor() as cur:
                cur.execute(f"SELECT {','.join(ETL_FIELDS)} FROM {src_kw['table']} LIMIT 5000")
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            c.close()
    except pymysql.MySQLError as e:
        raise RuntimeError(f"远程数据源读取失败: {e}")


def _read_api(src_kw):
    """模拟第三方计费 API: 读取推送的 JSON 文件(含说明模拟API响应)"""
    path = src_kw["path"]
    if not path:
        raise RuntimeError("api 源需要提供响应 JSON 文件路径")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    rows = data.get("data", data if isinstance(data, list) else [])
    return rows


def ingest_llm_usage(source_type, src_kw):
    """ETL 主流程: 源 -> 校验清洗 -> 事务写入 llm_usage(bi_app 应用账号, 属可写表)。
    source_type: csv/excel/mysql/api。 返回 (inserted, rejected, sample)。"""
    readers = {"csv": _read_csv, "excel": _read_excel, "mysql": _read_mysql, "api": _read_api}
    if source_type not in readers:
        raise ValueError(f"不支持的数据源类型: {source_type}")
    raw = readers[source_type](src_kw)
    if not raw:
        return 0, 0, []
    if len(raw) > 5000:
        raw = raw[:5000]
    norm, bad, sample = [], 0, None
    for r in raw:
        try:
            row = _norm_row(r)
            norm.append(row)
            if sample is None:
                sample = row
        except (ValueError, TypeError) as e:
            bad += 1
            if bad <= 3:
                pass
    if not norm:
        raise RuntimeError(f"清洗后无有效行(共 {len(raw)} 行)")
    cols = ETL_FIELDS
    c = conn("app")
    try:
        with c.cursor() as cur:
            cur.executemany(
                f"INSERT INTO llm_usage({','.join(cols)}) VALUES({','.join(['%s'] * len(cols))})",
                [tuple(row[k] for k in cols) for row in norm])
        c.commit()
    finally:
        c.close()
    return len(norm), bad, sample
