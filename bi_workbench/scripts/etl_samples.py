# -*- coding: utf-8 -*-
"""生成 ETL 演示样本文件(幂等覆盖): samples/llm_usage_20260827.csv/.xlsx/.json
供 etl_ingest 的 csv/excel/api 三类数据源演示与冒烟。"""
import csv
import json
from pathlib import Path

import openpyxl

BASE = Path(__file__).resolve().parent.parent / "samples"
BASE.mkdir(exist_ok=True)

HEAD = ["ts", "dept", "model", "prompt_tokens", "completion_tokens", "cost", "success", "blocked"]
ROWS = [
    ["2026-08-27 09:10:00", "研发", "deepseek-chat", 1800, 3200, 0.0320, 1, 0],
    ["2026-08-27 09:15:12", "交付一部", "deepseek-chat", 2400, 5100, 0.0510, 1, 0],
    ["2026-08-27 09:20:05", "交付二部", "deepseek-coder", 4200, 9800, 0.0860, 1, 0],
    ["2026-08-27 09:31:44", "实施部", "deepseek-chat", 1300, 2100, 0.0210, 1, 0],
    ["2026-08-27 09:42:18", "研发", "deepseek-reasoner", 5200, 12400, 0.2400, 1, 0],
    ["2026-08-27 10:03:37", "交付二部", "deepseek-chat", 0, 0, 0.0050, 0, 1],   # 被hook拦截示例
    ["2026-08-27 10:11:02", "其他", "deepseek-chat", 900, 1500, 0.0140, 1, 0],
]


def main():
    # CSV (含 BOM, Excel 直接打开不乱码)
    with open(BASE / "llm_usage_20260827.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEAD)
        w.writerows(ROWS)
    # Excel
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(HEAD)
    for r in ROWS:
        ws.append(r)
    wb.save(BASE / "llm_usage_20260827.xlsx")
    # 模拟第三方计费API响应
    api = {"code": 0, "msg": "ok", "data": [
        dict(zip(HEAD, r)) for r in ROWS
    ]}
    with open(BASE / "llm_usage_api_20260827.json", "w", encoding="utf-8") as f:
        json.dump(api, f, ensure_ascii=False, indent=1)
    print(f"ETL 样本已生成: {BASE}")


if __name__ == "__main__":
    main()
