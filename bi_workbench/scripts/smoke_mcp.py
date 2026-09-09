# -*- coding: utf-8 -*-
"""
冒烟测试: 以 MCP Client 通过 stdio 拉起 backend.server, 验证核心闭环:
  tools/list -> login(zhangmin) -> get_metric_catalog
  -> prepare_query(有歧义问题, 应 clarified) -> prepare_query(选定指标, 应 draft 含草稿SQL)
  -> execute_query(用确认后SQL, 应 success 返回行)
先决: 数据库已 init + seed。
用法: python scripts/smoke_mcp.py  (在 bi_workbench 目录下执行)
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

BASE = Path(__file__).resolve().parent.parent


def show(title, obj):
    print(f"\n===== {title} =====")
    print(json.dumps(obj, ensure_ascii=False, indent=2, default=str)[:1500])


def parse(res):
    """fastmcp Client.call_tool 返回值: dict 直接是结果; CallToolResult 取 data 或 content 文本"""
    if isinstance(res, dict):
        return res
    if res.data is not None:
        return res.data
    text = ""
    for c in res.content:
        text += (getattr(c, "text", None) or "") if not isinstance(c, dict) else c.get("text", "")
    return json.loads(text)


async def main():
    transport = StdioTransport(command=sys.executable,
                               args=[str(BASE / "run_server.py")], cwd=str(BASE))
    async with Client(transport) as client:
        tools = await client.list_tools()
        names = sorted(t.name for t in tools)
        show("MCP tools", names)

        # 1) 登录 finance 角色
        login = parse(await client.call_tool("login", {"user_id": "zhangmin", "password": "123456"}))
        show("login", login)
        tok = login["session_token"]

        # 2) 指标目录
        r = await client.call_tool("get_metric_catalog", {"session_token": tok, "domain": "finance"})
        cat = parse(r)
        show("catalog count", {"count": cat.get("count"), "ids": [m["metric_id"] for m in cat.get("catalog", [])]})

        # 3) 歧义问题: "利润" -> clarified(毛利/净利润)
        r = await client.call_tool("prepare_query",
                                   {"session_token": tok, "question": "上个月华北的利润是多少"})
        a1 = parse(r)
        show("prepare-ambiguity", a1)

        # 4) 选定指标重试 -> draft
        r = await client.call_tool("prepare_query",
                                   {"session_token": tok,
                                    "question": "上个月华北的毛利是多少",
                                    "selected_metric": "gross_profit"})
        a2 = parse(r)
        show("prepare-draft", a2)
        assert a2.get("status") == "draft", "未得到 draft"
        assert "2026-07" in a2.get("draft_sql", ""), f"上个月应为2026-07, 实际: {a2.get('draft_sql')}"
        rid = a2["request_id"]

        # 5) 用户确认(此处按原样确认) -> execute
        r = await client.call_tool("execute_query",
                                   {"session_token": tok, "request_id": rid, "final_sql": a2["draft_sql"]})
        a3 = parse(r)
        show("execute-result", {k: a3.get(k) for k in ("status", "row_count", "columns", "metric", "sql")})

        # 6) 恶意改写: 新 prepare 一次, 提交时改成 UPDATE -> 应 denied
        r = await client.call_tool("prepare_query",
                                   {"session_token": tok,
                                    "question": "本月收入是多少", "selected_metric": "revenue"})
        a2b = parse(r)
        rid2 = a2b["request_id"]
        r = await client.call_tool("execute_query",
                                   {"session_token": tok, "request_id": rid2,
                                    "final_sql": "UPDATE finance_sales SET revenue=1 WHERE ym='2026-08'"})
        a4 = parse(r)
        show("execute-blocked", a4)

        # 7) 异常洞察 -> 扫描生成告警 -> 告警列表
        r = await client.call_tool("run_alert_scan", {"session_token": tok})
        sc = parse(r)
        show("alert-scan", {"added_count": sc.get("count"), "items": sc.get("added")})
        r = await client.call_tool("list_alerts", {"session_token": tok})
        al = parse(r)
        show("alerts", {"count": len(al.get("alerts", []))})
        assert len(al.get("alerts", [])) >= 3, "告警应至少覆盖多个规则"

        print("\nSMOKE OK")


if __name__ == "__main__":
    asyncio.run(main())
