# -*- coding: utf-8 -*-
"""hr-backend 冒烟: tools/list/登录/歧义澄清/受控执行/行隔离/薪资表权限/写阻断/预警。
用法: python smoke_hr.py (cwd=hr_backend, 用 bi_workbench/.venv 解释器)"""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

sys.stdout.reconfigure(encoding="utf-8")
CWD = Path(__file__).resolve().parent.parent        # <repo>
VENV = CWD / "bi_workbench" / ".venv" / "Scripts" / "python.exe"
CHECKS = []


def check(name, cond, detail=""):
    CHECKS.append((name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}")


async def call(c, name, **args):
    r = await c.call_tool(name, args)
    if getattr(r, "isError", False):
        return {"_error": r.content[0].text if r.content else ""}
    try:
        return json.loads(r.content[0].text)
    except Exception:
        return {"_raw": str(r.content)}


async def run(c):
    r = await c.list_tools()
    names = {t.name for t in r.tools}
    check("tools/list 7个", {"login", "logout", "get_metric_catalog", "prepare_query",
                             "execute_query", "run_hr_scan", "list_alerts"} <= names, str(sorted(names)))
    tok = {}
    r = await call(c, "login", user_id="hr_admin", password="hr_admin123")
    check("hr_admin 登录", r.get("ok"), r.get("role", ""))
    tok["admin"] = r["session_token"]
    r = await call(c, "login", user_id="zhaoliu", password="123456")
    tok["mgr"] = r["session_token"]

    # 管理员: 问数闭环
    r = await call(c, "prepare_query", session_token=tok["admin"], question="客服部2026-08离职率")
    check("prepare(客服离职率)", r.get("status") == "draft", json.dumps(r, ensure_ascii=False)[:220])
    rid = r["request_id"]
    ex = await call(c, "execute_query", session_token=tok["admin"],
                    request_id=rid, final_sql=r["draft_sql"])
    check("execute 成功", ex.get("status") == "success" and ex.get("row_count") == 1,
          f"rows={ex.get('row_count')} v={ex.get('rows')}")

    # 歧义: 离职
    r = await call(c, "prepare_query", session_token=tok["admin"], question="客服部上个月离职")
    check("歧义->clarified(离职数/离职率)", r.get("status") == "clarified",
          [o["name"] for o in r.get("options", [])])
    # 接上一步: 选定离职率继续
    r = await call(c, "prepare_query", session_token=tok["admin"], question="客服部上月离职",
                   selected_metric="attrition_rate")
    check("澄清后出草稿", r.get("status") == "draft", r.get("draft_sql", ""))

    # 管理员: 写阻断 + 无部门薪资
    r = await call(c, "prepare_query", session_token=tok["admin"], question="今年人力成本")
    rid2 = r["request_id"]
    ex = await call(c, "execute_query", session_token=tok["admin"], request_id=rid2,
                    final_sql="UPDATE monthly_payroll SET payroll_amt=0")
    check("UPDATE 被拦", ex.get("status") == "denied", ex.get("reason", "")[:50])

    # 部门经理: 行隔离(无部门条件拒绝)
    r = await call(c, "prepare_query", session_token=tok["mgr"], question="各部门离职率")
    check("mgr 无部门条件被拒(行隔离)", r.get("status") == "denied",
          r.get("reason", "")[:60])
    # mgr 只查自己部门 OK
    r = await call(c, "prepare_query", session_token=tok["mgr"], question="研发部上月离职率")
    check("mgr 本部门查询OK", r.get("status") == "draft", r.get("draft_sql", ""))
    if r.get("status") == "draft":
        ex = await call(c, "execute_query", session_token=tok["mgr"],
                        request_id=r["request_id"], final_sql=r["draft_sql"])
        check("mgr execute 成功", ex.get("status") == "success", f"v={ex.get('rows')}")
    # mgr 试图看薪资
    r = await call(c, "prepare_query", session_token=tok["mgr"], question="研发部8月平均工资")
    check("mgr 查薪资被拒", r.get("status") == "denied", r.get("reason", "")[:40])
    # mgr 篡改 final_sql 看销售部
    if r.get("status") != "draft":
        r = await call(c, "prepare_query", session_token=tok["mgr"], question="研发部8月离职率")
    ex = await call(c, "execute_query", session_token=tok["mgr"], request_id=r["request_id"],
                    final_sql="SELECT SUM(exits)/(SUM(headcount)+SUM(exits)) AS r FROM monthly_headcount WHERE ym='2026-08' AND dept='销售'")
    check("mgr 改看销售部被拒", ex.get("status") == "denied", ex.get("reason", "")[:60])

    # 预警扫描
    r = await call(c, "run_hr_scan", session_token=tok["admin"])
    check("hr 预警扫描", r.get("ok"), r.get("added"))
    r = await call(c, "list_alerts", session_token=tok["admin"])
    hit = any("客服" in str(a.get("title", "")) for a in r.get("alerts", []))
    check("客服离职率预警存在", hit, str(r.get("alerts", []))[:100])

    failed = [x for x in CHECKS if not x[1]]
    print(f"\n==== HR-BACKEND SMOKE: {'PASS' if not failed else f'{len(failed)} FAILED'} ====")
    return 1 if failed else 0


async def main():
    params = StdioServerParameters(command=str(VENV), args=["-m", "hr_backend.server"], cwd=str(CWD))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as c:
            await c.initialize()
            rc = await run(c)
    sys.exit(rc)


if __name__ == "__main__":
    asyncio.run(main())
