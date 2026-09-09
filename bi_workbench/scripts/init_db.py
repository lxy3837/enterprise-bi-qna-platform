# -*- coding: utf-8 -*-
"""
一键初始化: 执行 sql/schema.sql 建库建表 -> 调用 seed.py 造数。
用法: python init_db.py --password <root密码> [--host 127.0.0.1] [--port 3306]
"""
import argparse
import os
import subprocess
import sys

import pymysql
from pymysql.constants import CLIENT

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = os.path.join(BASE, "sql", "schema.sql")


def run_schema(host, port, password):
    with open(SCHEMA, encoding="utf-8") as f:
        sql = f.read()
    conn = pymysql.connect(host=host, port=port, user="root", password=password,
                           charset="utf8mb4",
                           client_flag=CLIENT.MULTI_STATEMENTS, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        print("schema.sql 执行成功 ✔ (库 bi_workbench + 只读账号 bi_ro)")
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=3306)
    ap.add_argument("--password", required=True, help="MySQL root 密码")
    args = ap.parse_args()
    run_schema(args.host, args.port, args.password)
    py = sys.executable
    subprocess.check_call([py, os.path.join(BASE, "scripts", "seed.py"),
                           "--host", args.host, "--port", str(args.port),
                           "--user", "root", "--password", args.password])
    print("初始化完成 ✔ 用只读账号 bi_ro 即可连接")


if __name__ == "__main__":
    main()
