# -*- coding: utf-8 -*-
"""
D4 最小引擎验证: 一个进程同时挂 2 个后端(与 dsh 同构) —— "不知道数据在哪个系统, 客户端自动路由"。
模拟 dsh 行为: 工具发现(union) + 按问题归属选择 server 并调用 prepare/execute 全闭环。
用法: python scripts/dual_backends.py
"""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[2]        # <repo>/bi_workbench/scripts -> <repo>
PY = ROOT / "bi_workbench" / ".venv" / "Scripts" / "python.exe"
P1 = ROOT / "bi_workbench"
P2 = ROOT

# 两个后端的 stdio 描述(与 dsh patch 文件一致)
SERVERS = {
    "bi_workbench": StdioServerParameters(command=str(PY), args=[str(P1 / "run_server.py")], cwd=P1),
    "hr_backend": StdioServerParameters(command=str(PY), args=["-m", "hr_backend.server"], cwd=P2),
}

FAILS = []


def check(name, ok, detail=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}")
    if not ok:
        FAILS.append(name)


async def call(c, name, **args):
    r = await c.call_tool(name, args)
    if getattr(r, "isError", False):
        return {"_error": r.content[0].text if r.content else ""}
    try:
        return json.loads(r.content[0].text)
    except Exception:
        return {"_raw": str(r.content)}


async def one(name, params, questions):
    """一个 server 的完整会话: 发现工具->登录->按问题逐条 prepare/execute"""
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as c:
            await c.initialize()
            tools = await c.list_tools()
            names = {t.name for t in tools.tools}
            check(f"{name}: 工具发现", len(names) >= 7, f"{len(names)}个: {sorted(names)[:5]}...")
            # 各 server 用自己的账号
            login_args = {"bi_workbench": ("admin", "admin123"),
                          "hr_backend": ("hr_admin", "hr_admin123")}[name]
            r = await call(c, "login", user_id=login_args[0], password=login_args[1])
            check(f"{name}: 登录", r.get("ok"), r.get("role", ""))
            tok = r["session_token"]
            for q in questions:
                p = await call(c, "prepare_query", session_token=tok, question=q)
                if p.get("status") == "clarified":
                    sel = p["options"][-1]
                    sel = sel["metric_id"] if isinstance(sel, dict) else sel
                    p = await call(c, "prepare_query", session_token=tok, question=q,
                                   selected_metric=sel)
                check(f"{name}: 问数[{q[:16]}]", p.get("status") == "draft",
                      (p.get("draft_sql") or p.get("reason") or "")[:80])
                if p.get("status") != "draft":
                    continue
                e = await call(c, "execute_query", session_token=tok,
                               request_id=p["request_id"], final_sql=p["draft_sql"])
                check(f"{name}: 确认执行", e.get("status") == "success",
                      f"row={e.get('row_count')} val={str(e.get('rows'))[:60]}")


async def main():
    # 后端①: 财务/项目域问题; 后端②: 人力域问题
    q1 = ["华北区2026-08预算偏差最大的客户是谁", "交付一部下月延期风险项目数",
          "哪个部门本月模型成本增长最快"]
    q2 = ["客服部2026-08离职率", "今年各部门离职人数"]
    async with asyncio.TaskGroup() as tg:
        tg.create_task(one("bi_workbench", SERVERS["bi_workbench"], q1))
        tg.create_task(one("hr_backend", SERVERS["hr_backend"], q2))
    print(f"\n==== DUAL-BACKEND(等价dsh宿主): {'PASS' if not FAILS else 'FAIL: ' + str(FAILS)} ====")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    asyncio.run(main())
