# -*- coding: utf-8 -*-
"""
后端闭环冒烟(B3): 报告快报 / 导出留痕 / ETL(csv·excel·api) / 异常访问告警 / chart_type收敛
走标准 MCP client(mcp SDK, stdio), 与 dsh 相同协议。全 PASS 才提示通过。
用法: python scripts/smoke_ext.py
"""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

sys.stdout.reconfigure(encoding="utf-8")
BASE = Path(__file__).resolve().parent.parent
SAMPLES = BASE / "samples"

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


async def run_session(c):
    tok = {}
    r = await call(c, "login", user_id="admin", password="admin123")
    check("admin 登录", r.get("ok"), r.get("user_id", ""))
    tok["admin"] = r["session_token"]
    r = await call(c, "login", user_id="zhangmin", password="123456")
    tok["finance"] = r["session_token"]
    r = await call(c, "login", user_id="xiaowang", password="123456")
    tok["staff"] = r["session_token"]

    # ---------- 报告快报 ----------
    r = await call(c, "gen_report", session_token=tok["finance"], report_type="daily")
    check("finance 生成日报", r.get("ok") and "经营总览" in r.get("markdown", ""), r.get("title", ""))
    r = await call(c, "gen_report", session_token=tok["admin"], report_type="period")
    check("admin 生成经期快报", r.get("ok") and "预算达成" in r.get("markdown", ""),
          f"metrics={r.get('metrics')}")
    r = await call(c, "gen_report", session_token=tok["staff"], report_type="daily")
    check("staff 生成报告被拒", "_error" in r, str(r)[:90])

    # ---------- ETL 三源接入 ----------
    for st, path in (("csv", SAMPLES / "llm_usage_20260827.csv"),
                     ("excel", SAMPLES / "llm_usage_20260827.xlsx"),
                     ("api", SAMPLES / "llm_usage_api_20260827.json")):
        r = await call(c, "etl_ingest", session_token=tok["admin"],
                       source_type=st, source_path=str(path))
        check(f"ETL[{st}] 接入", r.get("ok") and r.get("inserted") == 7,
              f"inserted={r.get('inserted')} rejected={r.get('rejected')}")

    # ---------- 受控查询 chart_type 收敛 ----------
    r = await call(c, "prepare_query", session_token=tok["finance"],
                   question="今年各月华南的收入", selected_metric="revenue")
    if r.get("status") == "draft":
        ex = await call(c, "execute_query", session_token=tok["finance"],
                        request_id=r["request_id"], final_sql=r["draft_sql"])
        check("execute 返回 chart_type(无占位chart)", "chart_type" in ex and "chart" not in ex,
              f"chart_type={ex.get('chart_type')} rows={ex.get('row_count')}")
    else:
        check("prepare(今年各月华南收入)出草稿", False, json.dumps(r, ensure_ascii=False)[:200])

    # ---------- 导出留痕 ----------
    r = await call(c, "export_query", session_token=tok["finance"],
                   final_sql="SELECT ym, region, ROUND(SUM(revenue),0) v FROM finance_sales "
                             "WHERE ym='2026-08' GROUP BY ym, region ORDER BY v DESC LIMIT 4",
                   description="2026-08 区域收入导出")
    check("finance 导出CSV", r.get("status") == "success" and r.get("exported_rows") == 4,
          f"md5={str(r.get('content_md5'))[:8]} rows={r.get('exported_rows')}")
    r = await call(c, "list_exports", session_token=tok["admin"])
    check("admin 导出日志留痕", r.get("ok") and r.get("exports"), f"{len(r.get('exports', []))}条")
    r = await call(c, "list_exports", session_token=tok["finance"])
    check("finance 查看导出日志被拒", "_error" in r, str(r)[:70])

    # ---------- 异常访问: 3次失败 -> 告警 ----------
    for i in range(3):
        await call(c, "login", user_id="liqiang", password="wrong_pwd")
    r = await call(c, "list_alerts", session_token=tok["admin"])
    hit = any("连续登录失败" in str(a.get("title", "")) for a in r.get("alerts", []))
    check("3次失败触发异常访问告警", hit, str(r.get("alerts", []))[:140])

    failed = [x for x in CHECKS if not x[1]]
    print(f"\n==== BACKEND CLOSED-LOOP SMOKE: {'PASS' if not failed else f'{len(failed)} FAILED'} ====")
    return 1 if failed else 0


async def main():
    params = StdioServerParameters(command=sys.executable,
                                   args=["-m", "backend.server"], cwd=str(BASE))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as c:
            await c.initialize()
            rc = await run_session(c)
    sys.exit(rc)


if __name__ == "__main__":
    asyncio.run(main())
