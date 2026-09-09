# -*- coding: utf-8 -*-
"""验证造数埋点命中 + 只读账号可用。 python verify_seed.py"""
import pymysql

RO = dict(host="127.0.0.1", port=3306, user="bi_ro", password="bi_ro_pass_2026",
          database="bi_workbench", charset="utf8mb4")

CHECKS = {
    "表行数": "SELECT 'sales' t, COUNT(*) n FROM finance_sales UNION ALL SELECT 'expense',COUNT(*) FROM finance_expense "
             "UNION ALL SELECT 'project',COUNT(*) FROM project_info UNION ALL SELECT 'milestone',COUNT(*) FROM project_milestone "
             "UNION ALL SELECT 'metric',COUNT(*) FROM metric_definition UNION ALL SELECT 'user',COUNT(*) FROM sys_user "
             "UNION ALL SELECT 'usage',COUNT(*) FROM llm_usage",
    "F1-中科智达7/8月毛利": "SELECT ym, ROUND(SUM(revenue),0) rev, ROUND(SUM(revenue-direct_cost),0) gp "
             "FROM finance_sales WHERE customer='中科智达' AND ym>='2026-06' GROUP BY ym ORDER BY ym",
    "F2-三家预算偏差客户(>=05月)": "SELECT customer, ROUND((SUM(revenue-direct_cost)/SUM(budget_gross_profit)-1)*100,1) dev_pct "
             "FROM finance_sales WHERE ym>='2026-05' AND customer IN ('恒信科技','蓝海实业','云帆物流') GROUP BY customer",
    "P1-下月(9月)风险项目(按部门)": "SELECT pi.dept, COUNT(DISTINCT pi.project_id) risk_cnt "
             "FROM project_milestone pm JOIN project_info pi ON pm.project_id=pi.project_id "
             "WHERE pm.status='进行中-延期' AND pi.plan_end BETWEEN '2026-09-01' AND '2026-09-30' AND pi.actual_end IS NULL "
             "GROUP BY pi.dept",
    "M1-交付二部近7天vs前7天成本": "SELECT CASE WHEN ts>='2026-08-25' THEN '近7天' ELSE '前7天' END wk, ROUND(SUM(cost),2) cost "
             "FROM llm_usage WHERE dept='交付二' AND ts>='2026-08-18' GROUP BY wk",
}


def main():
    c = pymysql.connect(**RO)
    try:
        with c.cursor() as cur:
            for name, sql in CHECKS.items():
                cur.execute(sql)
                print(f"\n== {name} ==")
                for row in cur.fetchall():
                    print("  ", row)
        print("\n只读账号 bi_ro 连接与查询成功 ✔")
    finally:
        c.close()


if __name__ == "__main__":
    main()
