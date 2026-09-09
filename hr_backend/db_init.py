# -*- coding: utf-8 -*-
"""
hr-backend 初始化: 独立 MySQL 库 hr_bi(员工与人力域) + 独立账号(hr_ro/hr_app)。
说明: 这是"另一套业务系统"的数据底座, 与 bi_workbench 完全分离, 用于演示:
      两个后端各自实现 MCP -> dsh 一个入口同时问两个系统的信息。
用法: python db_init.py --password <root密码>
幂等: 重建 5 张表并重造数据。
埋点 H1: 客服部 2026-07/08 离职数突增 -> 离职率异常(供后端预警/异常洞察)。
"""
import argparse
import datetime as dt
import random

import pymysql

random.seed(20260901)
DEPT = ["研发", "销售", "客服", "职能"]
MONTHS = []
y, m = 2024, 9
for _ in range(24):
    MONTHS.append(f"{y:04d}-{m:02d}")
    m += 1
    if m > 12:
        y, m = y + 1, 1
H1_DEPT, H1_M1, H1_M2 = "客服", "2026-07", "2026-08"

DDL = [
    "CREATE DATABASE IF NOT EXISTS hr_bi DEFAULT CHARSET utf8mb4",
    """CREATE TABLE IF NOT EXISTS monthly_headcount (
        ym VARCHAR(7) NOT NULL, dept VARCHAR(20) NOT NULL,
        headcount INT NOT NULL, new_hires INT NOT NULL DEFAULT 0, exits INT NOT NULL DEFAULT 0,
        PRIMARY KEY(ym, dept)) ENGINE=InnoDB COMMENT='月度人员规模/新入职/离职'""",
    """CREATE TABLE IF NOT EXISTS monthly_payroll (
        ym VARCHAR(7) NOT NULL, dept VARCHAR(20) NOT NULL,
        payroll_amt DECIMAL(14,2) NOT NULL, avg_salary DECIMAL(10,2) NOT NULL,
        PRIMARY KEY(ym, dept)) ENGINE=InnoDB COMMENT='月度人力成本'""",
    """CREATE TABLE IF NOT EXISTS hr_user (
        user_id VARCHAR(20) PRIMARY KEY, name VARCHAR(20) NOT NULL,
        role VARCHAR(20) NOT NULL COMMENT 'hr_admin/hr_mgr', dept VARCHAR(20) DEFAULT '',
        password VARCHAR(32) NOT NULL) ENGINE=InnoDB COMMENT='HR系统用户'""",
    """CREATE TABLE IF NOT EXISTS hr_audit (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        ts DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        user_id VARCHAR(20) NOT NULL, role VARCHAR(20) NOT NULL,
        question VARCHAR(300) DEFAULT '', draft_sql TEXT, final_sql TEXT,
        status VARCHAR(20) NOT NULL, hook_result VARCHAR(300) DEFAULT '',
        row_count INT DEFAULT 0, elapse_ms INT DEFAULT 0) ENGINE=InnoDB COMMENT='HR问数审计'""",
    """CREATE TABLE IF NOT EXISTS hr_alert (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        ts DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        alert_type VARCHAR(20) NOT NULL, title VARCHAR(100) NOT NULL,
        content VARCHAR(400) DEFAULT '', level VARCHAR(10) DEFAULT 'medium',
        dept VARCHAR(20) DEFAULT '', is_read TINYINT DEFAULT 0) ENGINE=InnoDB COMMENT='HR预警'""",
    # 账号: 读账号只读全库; 应用账号仅能写审计/预警(业务表只读) — 与 bi_workbench 同理念
    "CREATE USER IF NOT EXISTS 'hr_ro'@'localhost' IDENTIFIED BY 'hr_ro_pass_2026'",
    "CREATE USER IF NOT EXISTS 'hr_app'@'localhost' IDENTIFIED BY 'hr_app_pass_2026'",
    "GRANT SELECT ON hr_bi.* TO 'hr_ro'@'localhost'",
    "GRANT SELECT ON hr_bi.* TO 'hr_app'@'localhost'",
    "GRANT INSERT, UPDATE ON hr_bi.hr_audit TO 'hr_app'@'localhost'",
    "GRANT INSERT, UPDATE ON hr_bi.hr_alert TO 'hr_app'@'localhost'",
    "FLUSH PRIVILEGES",
]

# 部门基准人数/月薪区间
BASE_HC = {"研发": 420, "销售": 260, "客服": 180, "职能": 90}
SAL = {"研发": (15000, 26000), "销售": (9000, 18000), "客服": (6500, 11000), "职能": (8000, 16000)}


def seed(c):
    rows_h, rows_p = [], []
    for i, ym in enumerate(MONTHS):
        for dept in DEPT:
            hc = int(BASE_HC[dept] * (1 + i * 0.01 + random.uniform(-0.02, 0.02)))
            nh = max(0, int(hc * random.uniform(0.015, 0.05)))
            # 离职: 平时 ~1.5-3.5%; H1 客服 2026-07/08 突增 ~9%/11%
            if dept == H1_DEPT and ym in (H1_M1, H1_M2):
                ratio = 0.09 if ym == H1_M1 else 0.11
            else:
                ratio = random.uniform(0.015, 0.035)
            ex = max(0, int(hc * ratio))
            rows_h.append((ym, dept, hc, nh, ex))
            lo, hi = SAL[dept]
            avg = round(random.uniform(lo, hi), 2)
            rows_p.append((ym, dept, round(hc * avg, 2), avg))
    with c.cursor() as cur:
        for t in ["monthly_headcount", "monthly_payroll", "hr_user", "hr_audit"]:
            cur.execute(f"DELETE FROM {t}")
        cur.executemany(
            "INSERT INTO monthly_headcount(ym,dept,headcount,new_hires,exits) VALUES(%s,%s,%s,%s,%s)",
            rows_h)
        cur.executemany(
            "INSERT INTO monthly_payroll(ym,dept,payroll_amt,avg_salary) VALUES(%s,%s,%s,%s)", rows_p)
        users = [
            ("hr_admin", "刘主任", "hr_admin", "", "hr_admin123"),
            ("zhaoliu", "赵六", "hr_mgr", "研发", "123456"),
            ("qianqi", "钱七", "hr_mgr", "销售", "123456"),
        ]
        cur.executemany("INSERT INTO hr_user(user_id,name,role,dept,password) VALUES(%s,%s,%s,%s,%s)", users)
    print(f"[hr] headcount={len(rows_h)} payroll={len(rows_p)} users={len(users)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=3306)
    ap.add_argument("--user", default="root")
    ap.add_argument("--password", required=True)
    args = ap.parse_args()
    c = pymysql.connect(host=args.host, port=args.port, user=args.user,
                        password=args.password, charset="utf8mb4", autocommit=True)
    try:
        with c.cursor() as cur:
            cur.execute(DDL[0])           # CREATE DATABASE
            c.select_db("hr_bi")          # 之后的建表/账号均在 hr_bi 语境
            for d in DDL[1:]:
                cur.execute(d)
        seed(c)
        print("hr_bi OK")
    finally:
        c.close()


if __name__ == "__main__":
    main()
