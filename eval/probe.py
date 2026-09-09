# -*- coding: utf-8 -*-
"""探查: 对给定问题逐条 prepare, 打印 status/reason/sql, 用于锁定合法问法(校准 eval/cases.py)。"""
import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

BASE = Path(__file__).resolve().parent.parent
VENV = BASE / "bi_workbench" / ".venv" / "Scripts" / "python.exe"

BI = dict(args=[str(BASE / "bi_workbench" / "run_server.py")], cwd=str(BASE / "bi_workbench"))
HR = dict(args=["-m", "hr_backend.server"], cwd=str(BASE))

BI_PROBES = [
    ("revenue", ["华北区2026-08收入是多少", "华北区2026-08销售额是多少", "华北区2026-08销售收入是多少",
                 "华北区2026-08收入总额是多少", "本月华北区销售额"]),
    ("direct_cost", ["华北区2026-08直接成本是多少", "华北区2026-08成本是多少", "华北区本月直接成本"]),
    ("gross_profit", ["华北区2026-08毛利是多少", "华北区2026-08毛利润是多少", "2026-08华南区毛利总额"]),
    ("collection", ["华北区2026-08回款是多少", "华北区2026-08回款额是多少", "华北区2026-08回款金额是多少"]),
    ("expense", ["华北区2026-08费用是多少", "2026-08华北区期间费用"]),
    ("expense_rate", ["华北区2026-08费用率是多少", "华北区2026-08费用占比是多少"]),
    ("net_profit", ["华北区2026-08净利润是多少"]),
    ("budget_gross_profit", ["华北区2026-08预算毛利是多少"]),
    ("budget_deviation", ["华北区2026-08预算偏差最大的客户是谁", "华北区2026-08预算偏差率是多少"]),
    ("risk_next_month", ["交付一部下月延期风险项目数", "交付一部延期风险项目"]),
    ("milestone_rate", ["交付一部里程碑完成率"]),
    ("llm_cost", ["哪个部门本周模型成本增长最快", "本月各部门模型成本", "本周各部门LLM成本", "交付二部本周模型成本"]),
]
HR_PROBES = [
    ("attrition_rate", ["客服部2026-08离职率是多少"]),
    ("headcount", ["研发部2026-08在编人数"]),
    ("new_hires", ["2026-07各部门新入职人数"]),
    ("payroll_cost", ["研发部2026-08人力成本"]),
    ("avg_salary", ["研发部2026-08平均工资"]),
]


async def call(c, name, **args):
    r = await c.call_tool(name, args)
    if getattr(r, "isError", False):
        return {"_error": r.content[0].text if r.content else ""}
    try:
        return json.loads(r.content[0].text)
    except Exception:
        return {"_raw": str(r.content)}


async def probe(srv, alias, items):
    sp = StdioServerParameters(command=str(VENV), args=srv["args"], cwd=srv["cwd"])
    async with stdio_client(sp) as (r, w):
        async with ClientSession(r, w) as c:
            await c.initialize()
            lg = await call(c, "login_vault", alias=alias)
            tok = lg["session_token"]
            for expect, qs in items:
                for q in qs:
                    res = await call(c, "prepare_query", session_token=tok, question=q)
                    st = res.get("status")
                    sql = (res.get("draft_sql") or "")[:70].replace("\n", " ")
                    rs = "; ".join(o.get("name", "") for o in res.get("options", [])) if res.get("options") else res.get("reason", "")
                    mark = "OK " if st == "draft" else "   "
                    print(f"{mark}[{expect}] {q}\n     -> status={st} {rs or ''}{' SQL: ' + sql if sql else ''}")


async def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "bi"
    if which == "bi":
        await probe(BI, "bi-admin", BI_PROBES)
    else:
        await probe(HR, "hr-admin", HR_PROBES)


if __name__ == "__main__":
    asyncio.run(main())
