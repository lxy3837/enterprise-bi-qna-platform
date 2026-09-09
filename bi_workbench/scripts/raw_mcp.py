# -*- coding: utf-8 -*-
"""
协议级验证: 不借助任何 MCP 客户端库, 用裸 JSON-RPC over stdio 与 backend.server 通信。
等价于 dsh 内嵌 MCP client(TS SDK) 的行为: initialize -> notifications/initialized -> tools/list -> tools/call。
用法: python scripts/raw_mcp.py
"""
import json
import queue
import subprocess
import sys
import threading
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
BASE = Path(__file__).resolve().parent.parent

proc = subprocess.Popen(
    [sys.executable, str(BASE / "run_server.py")],
    cwd=str(BASE),
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    text=True, bufsize=1, encoding="utf-8")

q = queue.Queue()
_seq = [0]


def _reader():
    for line in proc.stdout:
        line = line.strip()
        if line:
            try:
                q.put(json.loads(line))
            except Exception:
                pass


threading.Thread(target=_reader, daemon=True).start()


def send(obj):
    proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
    proc.stdin.flush()


def rpc(method, params=None, notify=False, timeout=20):
    """send request & wait matching response; notify -> 只发不等"""
    if notify:
        send({"jsonrpc": "2.0", "method": method, "params": params or {}})
        return None
    _seq[0] += 1
    rid = _seq[0]
    send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
    while True:
        msg = q.get(timeout=timeout)
        if msg.get("id") == rid:
            return msg


def brief(msg, limit=600):
    s = json.dumps(msg, ensure_ascii=False)
    print(s[:limit] + ("..." if len(s) > limit else ""))


def main():
    steps = []
    try:
        # 1. 握手
        r = rpc("initialize", {"protocolVersion": "2024-11-05",
                               "capabilities": {}, "clientInfo": {"name": "raw-probe", "version": "1.0"}})
        steps.append(("initialize", r.get("result", {}).get("serverInfo")))
        rpc("notifications/initialized", notify=True)

        # 2. 工具发现
        r = rpc("tools/list")
        tools = r["result"]["tools"]
        names = sorted(t["name"] for t in tools)
        steps.append(("tools/list", {"count": len(tools), "names": names}))

        # 3. 登录 (zhangmin/finance)
        r = rpc("tools/call", {"name": "login", "arguments": {"user_id": "zhangmin", "password": "123456"}})
        content = r["result"]["content"]
        text = json.loads(content[0]["text"]) if content else r["result"]
        tok = text["session_token"]
        steps.append(("tools/call login", {"ok": text["ok"], "user": text["user_id"], "role": text["role"]}))

        # 4. 歧义: 利润 -> clarified
        r = rpc("tools/call", {"name": "prepare_query",
                               "arguments": {"session_token": tok, "question": "上个月华北的利润是多少"}})
        a1 = json.loads(r["result"]["content"][0]["text"])
        steps.append(("prepare(利润)", {"status": a1["status"], "options": a1.get("options")}))

        # 5. 选定指标 -> draft
        r = rpc("tools/call", {"name": "prepare_query",
                               "arguments": {"session_token": tok, "question": "上个月华北的毛利是多少",
                                             "selected_metric": "gross_profit"}})
        a2 = json.loads(r["result"]["content"][0]["text"])
        rid = a2["request_id"]
        steps.append(("prepare(毛利)", {"status": a2["status"], "draft_sql": a2.get("draft_sql")}))

        # 6. 确认执行
        r = rpc("tools/call", {"name": "execute_query",
                               "arguments": {"session_token": tok, "request_id": rid,
                                             "final_sql": a2["draft_sql"]}})
        a3 = json.loads(r["result"]["content"][0]["text"])
        steps.append(("execute", {"status": a3["status"], "row_count": a3.get("row_count")}))

        # 7. 恶意 UPDATE -> denied
        r = rpc("tools/call", {"name": "prepare_query",
                               "arguments": {"session_token": tok, "question": "本月收入是多少",
                                             "selected_metric": "revenue"}})
        rid2 = json.loads(r["result"]["content"][0]["text"])["request_id"]
        r = rpc("tools/call", {"name": "execute_query",
                               "arguments": {"session_token": tok, "request_id": rid2,
                                             "final_sql": "UPDATE finance_sales SET revenue=1"}})
        a4 = json.loads(r["result"]["content"][0]["text"])
        steps.append(("execute(UPDATE)", {"status": a4["status"], "reason": a4.get("reason")}))

        ok = (len(tools) >= 7 and a1["status"] == "clarified" and a2["status"] == "draft"
              and a3["status"] == "success" and a4["status"] == "denied")
        print("\n==== RAW JSON-RPC RESULT ====")
        for name, val in steps:
            print(f"- {name}: {json.dumps(val, ensure_ascii=False)}")
        print(f"\nRAW MCP PROTOCOL: {'PASS' if ok else 'FAIL'}")
    except Exception as e:
        print(f"RAW MCP PROTOCOL: FAIL ({type(e).__name__}: {e})")
    finally:
        try:
            proc.stdin.close()
            proc.terminate()
        except Exception:
            pass


if __name__ == "__main__":
    main()
