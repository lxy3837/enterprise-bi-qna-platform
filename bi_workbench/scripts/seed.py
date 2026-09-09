# -*- coding: utf-8 -*-
"""
造数脚本: bi_workbench
数据时点: 最近完整月 = 2026-08（本月）, 2026-07 = 上月, "下月延期风险" = plan_end 落在 2026-09。
前置: 先执行 sql/schema.sql 建库建表（用 root）。
用法: python seed.py --user root --password <pwd>
幂等: 先清空业务表再插入。
埋点(保证演示用例确定性命中):
  F1 客户"中科智达"(华南) 2026-08 收入骤降至 22%, 毛利为负 → 环比暴跌告警
  F2 三家客户"恒信科技"(华北)/"蓝海实业"(华东)/"云帆物流"(西南) 近月实际毛利持续低于预算(偏差约-25%~-40%)
  P1 三个进行中项目 plan_end=2026-09 且含"进行中-延期"里程碑 → 下月延期风险
  M1 llm_usage 预埋 21 天, 构造"交付二部本周成本增速最快"
"""
import argparse
import datetime as dt
import random
import sys

import pymysql

random.seed(20260831)

REGIONS = ["华北", "华东", "华南", "西南"]
CUST_BY_REGION = {
    "华北": ["恒信科技", "华远集团", "九州智造", "启明能源", "盛世传媒"],
    "华东": ["蓝海实业", "东辰半导体", "南湖精密", "旭日食品", "汇金控股"],
    "华南": ["中科智达", "腾飞网络", "星河电子", "广达供应链", "海纳生物"],
    "西南": ["云帆物流", "锦城文旅", "蜀道装备", "嘉陵汽车", "春熙百货"],
}
# F2: 预算目标过高, 导致实际毛利持续低于预算的客户
OVER_BUDGET_CUST = {"恒信科技": "华北", "蓝海实业": "华东", "云帆物流": "西南"}
# F1: 暴跌客户
CRASH_CUST = "中科智达"  # 华南
EXPENSE_TYPES = ["销售费用", "管理费用", "研发费用"]
MONTHS = []  # 2024-09 ~ 2026-08 (24个月)
y, m = 2024, 9
for _ in range(24):
    MONTHS.append(f"{y:04d}-{m:02d}")
    m += 1
    if m > 12:
        y, m = y + 1, 1
NOW = dt.date(2026, 8, 31)          # 数据时点
THIS_MONTH = "2026-08"
LAST_MONTH = "2026-07"
DEPT_LIST = ["研发", "交付一", "交付二", "实施"]
CLIENTS = ["国家电网华北公司", "中石油炼化", "招商银行零售部", "顺丰航空", "京东方光电",
           "比亚迪乘用车", "小米生态链", "三一重工", "美团到家", "蔚来汽车",
           "宁德时代", "京东物流", "字节跳动飞书", "海尔智家", "格力电器", "上海医药",
           "中国平安寿险", "茅台股份", "云南白药", "万科地产"]


def connect(host, port, user, password, db=None):
    return pymysql.connect(host=host, port=port, user=user, password=password,
                           database=db, charset="utf8mb4", autocommit=True)


def clear(c):
    with c.cursor() as cur:
        for t in ["audit_log", "alert_message", "llm_usage", "project_milestone",
                  "project_info", "finance_expense", "finance_sales",
                  "metric_definition", "sys_user"]:
            cur.execute(f"DELETE FROM {t}")


def seed_finance(c):
    """finance_sales + finance_expense"""
    rows = []
    for region in REGIONS:
        for cust in CUST_BY_REGION[region]:
            # 基准(万元/月)
            base = round(random.uniform(320, 2600), 2)
            # 预算毛利: 目标利润率(target)基准; F2客户目标过高 -> 难达标
            target = 0.50 if cust in OVER_BUDGET_CUST else random.uniform(0.30, 0.42)
            budget = round(base * target, 2)
            phase = random.uniform(0, 6.28)
            for i, ym in enumerate(MONTHS):
                # 季节+噪声平滑波动
                wave = 1 + 0.05 * ((i % 12) - 6) / 6 + random.uniform(-0.08, 0.08)
                revenue = round(base * wave, 2)
                # F1 暴跌: 2026-08 收入骤降, 成本不降 -> 负毛利
                if cust == CRASH_CUST and ym == THIS_MONTH:
                    revenue = round(base * 0.22, 2)
                cost_ratio = random.uniform(0.58, 0.68)
                # F1 月成本保持高位, 强化亏损
                if cust == CRASH_CUST and ym == THIS_MONTH:
                    cost_ratio = random.uniform(0.95, 1.10)
                cost = round(revenue * cost_ratio, 2)
                col_ratio = random.uniform(0.62, 1.02) if i >= 20 else random.uniform(0.85, 1.05)
                collection = round(revenue * col_ratio, 2)
                rows.append((ym, region, cust, revenue, cost, collection, budget))
    with c.cursor() as cur:
        cur.executemany(
            "INSERT INTO finance_sales(ym,region,customer,revenue,direct_cost,collection,budget_gross_profit) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s)", rows)

    # 费用: 区域当月总收入的比例, 华北研发费用偏高
    exp_rows = []
    with c.cursor() as cur:
        cur.execute("SELECT ym, region, SUM(revenue) FROM finance_sales GROUP BY ym, region")
        rev_map = {(ym, rg): r for ym, rg, r in cur.fetchall()}
    ratio_by_type = {"销售费用": 0.05, "管理费用": 0.035, "研发费用": 0.025}
    for (ym, rg), total in rev_map.items():
        for et, ratio in ratio_by_type.items():
            r = ratio
            if et == "研发费用" and rg == "华北":
                r *= 2.0
            amount = round(float(total) * r * random.uniform(0.85, 1.15), 2)
            exp_rows.append((ym, rg, et, amount))
    with c.cursor() as cur:
        cur.executemany(
            "INSERT INTO finance_expense(ym,region,expense_type,amount) VALUES(%s,%s,%s,%s)",
            exp_rows)
    print(f"[finance] sales={len(rows)} expense={len(exp_rows)}")


def _month_len(y, m):
    return (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.date(y, m, 1)).days


def seed_project(c):
    """project_info + project_milestone"""
    MS_NAMES = ["需求确认", "方案设计", "开发联调", "上线验收", "试运行结束"]
    proj_rows, ms_rows = [], []

    def add_milestones(pid, plan_s, plan_e, actual_e, dept):
        """actual_e None => 进行中"""
        n = len(MS_NAMES)
        span = (plan_e - plan_s).days
        risk = None
        # 寻找该项目的风险标记(全局变量由调用方设置)
        total = n
        for i, name in enumerate(MS_NAMES):
            planned = plan_s + dt.timedelta(days=span * (i + 1) // (n + 1))
            if actual_e is None:  # 进行中
                if planned < NOW - dt.timedelta(days=20):
                    done = True
                    delay = random.randint(0, 9) if not _project_risk.get(pid) else random.randint(20, 45)
                    st = "已完成-按期" if delay <= 10 else "已完成-延期"
                    actual = planned + dt.timedelta(days=delay)
                elif _project_risk.get(pid) and planned <= NOW - dt.timedelta(days=7):
                    # 风险项目的关键里程碑: 已过计划日仍未完成
                    done, actual, st = False, None, "进行中-延期"
                    risk = name
                else:
                    done, actual, st = False, None, "未开始"
            else:
                delay = random.randint(0, 25)
                done, actual = True, planned + dt.timedelta(days=delay)
                st = "已完成-按期" if actual <= planned + dt.timedelta(days=10) else "已完成-延期"
            ms_rows.append((pid, name, planned, actual, st))
        # 风险项目强制一个"进行中-延期"关键里程碑, 保证"下月延期风险"稳定命中
        if actual_e is None and _project_risk.get(pid):
            ms_rows.append((pid, "上线验收(关键)", dt.date(2026, 8, 15), None, "进行中-延期"))
        return risk

    _project_risk = {}
    pid = 0
    # 历史已完成项目 24 个
    for i in range(24):
        pid += 1
        dept = DEPT_LIST[i % 4]
        dur = random.randint(90, 240)
        plan_e = NOW - dt.timedelta(days=random.randint(5, 400))
        plan_s = plan_e - dt.timedelta(days=dur)
        delay = random.randint(0, 30)
        actual_e = plan_e + dt.timedelta(days=delay)
        ph = random.randint(80, 400)
        proj_rows.append((f"P2025{i+1:03d}", f"{dept}{random.choice(['数字化','数据中台','CRM重构','BI升级','OA改造'])}项目-{random.randint(100,999)}",
                          random.choice(CLIENTS), dept, plan_s, plan_e, actual_e, ph,
                          round(ph * random.uniform(0.95, 1.45), 1)))
    # 进行中项目 16 个: plan_end 落在 2026-09(6个, 前3个确定性风险)/2026-10~12
    for i in range(16):
        pid += 1
        if i < 6:
            plan_e = dt.date(2026, 9, random.randint(5, 28))
        elif i < 11:
            plan_e = dt.date(2026, 10, random.randint(10, 28))
        else:
            plan_e = dt.date(2026, random.randint(11, 12), random.randint(10, 25))
        dept = DEPT_LIST[i % 4]
        dur = random.randint(120, 260)
        plan_s = plan_e - dt.timedelta(days=dur)
        ph = random.randint(80, 500)
        # P1: i=0,1,2 分别为 研发/交付一/交付二 的9月到期项目, 确定性设为延期风险
        risk = i in (0, 1, 2)
        pj = f"P2026{pid:03d}"
        _project_risk[pj] = risk
        name = f"{dept}{random.choice(['供应链平台','营销增长','安全合规','数据治理','智能客服'])}项目-{random.randint(100,999)}"
        proj_rows.append((pj, name, random.choice(CLIENTS), dept, plan_s, plan_e, None, ph,
                          round(ph * (1.25 if risk else random.uniform(0.35, 1.0)), 1)))
    with c.cursor() as cur:
        cur.executemany(
            "INSERT INTO project_info(project_id,name,client,dept,plan_start,plan_end,actual_end,plan_hours,actual_hours) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)", proj_rows)
    for row in proj_rows:
        pid_, name, client, dept, ps, pe, ae, ph, ah = row
        add_milestones(pid_, ps, pe, ae, dept)
    with c.cursor() as cur:
        cur.executemany(
            "INSERT INTO project_milestone(project_id,m_name,planned_date,actual_date,status) "
            "VALUES(%s,%s,%s,%s,%s)", ms_rows)
    print(f"[project] info={len(proj_rows)} milestone={len(ms_rows)}")


def seed_system(c):
    users = [
        ("admin", "赵子昂", "admin", "信息中心", "admin123"),
        ("zhangmin", "张敏", "finance", "财务部", "123456"),
        ("liqiang", "李强", "pm", "交付一部", "123456"),
        ("wangfang", "王芳", "pm", "交付二部", "123456"),
        ("xiaowang", "王小虎", "staff", "市场部", "123456"),
    ]
    with c.cursor() as cur:
        cur.executemany("INSERT INTO sys_user(user_id,name,role,dept,password) VALUES(%s,%s,%s,%s,%s)", users)

    metrics = [
        # domain, id, name, aliases, formula, agg_sql, unit, grain, base_table, lineage, perm, mask
        ("finance", "revenue", "收入", "销售额,销售收入",
         "SUM(收入)", "SUM(revenue)", "万元", "区域/客户/月", "finance_sales",
         "收入 ← revenue ← finance_sales", "finance.*", ""),
        ("finance", "direct_cost", "直接成本", "成本,直接成本",
         "SUM(直接成本)", "SUM(direct_cost)", "万元", "区域/客户/月", "finance_sales",
         "直接成本 ← direct_cost ← finance_sales", "finance.*", ""),
        ("finance", "gross_profit", "毛利", "毛利润,利润",
         "SUM(收入-直接成本)", "SUM(revenue-direct_cost)", "万元", "区域/客户/月", "finance_sales",
         "毛利 = 收入-直接成本 ← revenue,direct_cost ← finance_sales", "finance.*", ""),
        ("finance", "net_profit", "净利润", "净利,利润",
         "毛利-费用", "SUM(revenue-direct_cost)-(SELECT SUM(amount) FROM finance_expense WHERE ...)",
         "万元", "区域/月", "finance_sales+finance_expense",
         "净利润 = 毛利-费用", "finance.*", ""),
        ("finance", "collection", "回款", "回款额,回款金额",
         "SUM(回款)", "SUM(collection)", "万元", "区域/客户/月", "finance_sales",
         "回款 ← collection ← finance_sales", "finance.*", ""),
        ("finance", "budget_gross_profit", "预算毛利", "预算,预算额",
         "SUM(预算毛利)", "SUM(budget_gross_profit)", "万元", "区域/客户/月", "finance_sales",
         "预算毛利 ← budget_gross_profit ← finance_sales", "finance.*", ""),
        ("finance", "budget_deviation", "预算偏差率", "预算偏差,偏差率",
         "(实际毛利-预算毛利)/预算毛利*100%", "SUM(revenue-direct_cost)/SUM(budget_gross_profit)*100-100",
         "%", "区域/客户/月", "finance_sales",
         "预算偏差率 = (实际毛利-预算毛利)/预算毛利", "finance.*", ""),
        ("finance", "expense", "费用", "期间费用,总费用",
         "SUM(费用金额)", "SUM(amount)", "万元", "区域/类型/月", "finance_expense",
         "费用 ← amount ← finance_expense", "finance.*", ""),
        ("finance", "expense_rate", "费用率", "费用占比",
         "费用/收入*100%", "SUM(fe.amount)/SUM(fs.revenue)*100", "%", "区域/月",
         "finance_expense+finance_sales", "费用率 = 费用/收入", "finance.*", ""),
        # 项目域
        ("project", "milestone_rate", "里程碑完成率", "完成率,里程碑达成率",
         "已完成里程碑/全部里程碑*100%",
         "SUM(status LIKE '已完成%')/COUNT(*)*100", "%", "项目/部门", "project_milestone",
         "里程碑完成率 ← status ← project_milestone", "project.*", "client"),
        ("project", "delay_rate", "项目延期率", "延期率,按期交付率(反)",
         "已延期完成项目/已完成项目*100%",
         "SUM(actual_end>plan_end)/COUNT(*)*100", "%", "部门", "project_info",
         "项目延期率 ← plan_end,actual_end ← project_info", "project.*", ""),
        ("project", "hours_deviation", "工时偏差率", "工时偏差,工作量偏差",
         "(实际工时-计划工时)/计划工时*100%",
         "SUM(actual_hours-plan_hours)/SUM(plan_hours)*100", "%", "项目/部门", "project_info",
         "工时偏差率 = (实际工时-计划工时)/计划工时", "project.*", ""),
        ("project", "risk_next_month", "下月延期风险数", "延期风险,风险项目数",
         "下月计划结束且存在进行中-延期里程碑的项目数",
         "COUNT(DISTINCT 满足条件的project_id)", "个", "部门", "project_milestone+project_info",
         "风险数 = plan_end在下月 且 里程碑含'进行中-延期'", "project.*", ""),
        # MaaS域
        ("maas", "llm_cost", "模型成本", "成本,LLM成本,token费用",
         "SUM(费用)", "SUM(cost)", "元", "部门/日", "llm_usage",
         "模型成本 ← cost ← llm_usage", "public.maas", ""),
        ("maas", "llm_tokens", "Token消耗", "token数,用量",
         "SUM(输入+输出tokens)", "SUM(prompt_tokens+completion_tokens)", "个", "部门/日", "llm_usage",
         "Token ← prompt_tokens,completion_tokens ← llm_usage", "public.maas", ""),
    ]
    with c.cursor() as cur:
        cur.executemany(
            "INSERT INTO metric_definition(domain,metric_id,name,aliases,formula,agg_sql,unit,grain,base_table,lineage,permission_tag,mask_fields) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            [m[:12] for m in metrics])

    # llm_usage 预埋 21 天(2026-08-11~08-31), 交付二部本周(08-25~31)成本明显高于上周
    usage = []
    depts = ["研发", "交付一", "交付二", "实施", "财务", "市场"]
    for i in range(21):
        d = NOW - dt.timedelta(days=20 - i)
        for dp in depts:
            is_recent_week = i >= 14  # 近7天(08-25~08-31)
            base = 180 if is_recent_week else 120
            mult = 2.1 if (dp == "交付二" and is_recent_week) else random.uniform(0.7, 1.5)
            calls = random.randint(60, 160)
            ptok = int(base * calls * mult)
            ctok = int(ptok * random.uniform(0.25, 0.6))
            cost = round((ptok + ctok * 3) / 1e6 * 1.0, 4)  # 输入1元/M, 输出~3x权重 简化
            usage.append((d, dp, "deepseek-v4-flash", ptok, ctok, cost, 1, 0))
    with c.cursor() as cur:
        cur.executemany(
            "INSERT INTO llm_usage(ts,dept,model,prompt_tokens,completion_tokens,cost,success,blocked) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s)", usage)
    print(f"[system] users={len(users)} metrics={len(metrics)} llm_usage={len(usage)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=3306)
    ap.add_argument("--user", default="root")
    ap.add_argument("--password", required=True)
    args = ap.parse_args()
    c = connect(args.host, args.port, args.user, args.password, db="bi_workbench")
    try:
        with c.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='bi_workbench'")
            if cur.fetchone()[0] == 0:
                print("!! bi_workbench 里没有表, 请先执行 sql/schema.sql"); sys.exit(1)
        clear(c)
        seed_finance(c)
        seed_project(c)
        seed_system(c)
        print("造数完成 ✔")
    finally:
        c.close()


if __name__ == "__main__":
    main()
