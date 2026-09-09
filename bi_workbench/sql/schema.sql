-- ============================================================
-- 平台化企业智能问数工作台 - 建库建表脚本
-- 库: bi_workbench | 域: finance(财务) / project(项目) / maas(LLM用量)
-- 说明: 业务数据用只读账号 bi_ro 访问, 本脚本由 root 执行一次
-- ============================================================

CREATE DATABASE IF NOT EXISTS bi_workbench
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE bi_workbench;

-- ------------------------------------------------------------
-- [财务域] 销售表: 月 x 区域 x 客户
-- ------------------------------------------------------------
DROP TABLE IF EXISTS finance_sales;
CREATE TABLE finance_sales (
  id                   BIGINT PRIMARY KEY AUTO_INCREMENT,
  ym                   CHAR(7)      NOT NULL COMMENT '月份 YYYY-MM',
  region               VARCHAR(20)  NOT NULL COMMENT '区域',
  customer             VARCHAR(100) NOT NULL COMMENT '客户',
  revenue              DECIMAL(14,2) NOT NULL COMMENT '收入',
  direct_cost          DECIMAL(14,2) NOT NULL COMMENT '直接成本',
  collection           DECIMAL(14,2) NOT NULL COMMENT '回款',
  budget_gross_profit  DECIMAL(14,2) NOT NULL COMMENT '预算毛利',
  KEY idx_sales_ym      (ym),
  KEY idx_sales_region  (region),
  KEY idx_sales_customer(customer),
  KEY idx_sales_ym_region (ym, region)
) ENGINE=InnoDB COMMENT='财务销售(月x区域x客户)';

-- ------------------------------------------------------------
-- [财务域] 费用表: 月 x 区域 x 费用类型
-- ------------------------------------------------------------
DROP TABLE IF EXISTS finance_expense;
CREATE TABLE finance_expense (
  id           BIGINT PRIMARY KEY AUTO_INCREMENT,
  ym           CHAR(7)       NOT NULL,
  region       VARCHAR(20)   NOT NULL,
  expense_type VARCHAR(20)   NOT NULL COMMENT '销售费用/管理费用/研发费用',
  amount       DECIMAL(14,2) NOT NULL,
  KEY idx_expense_ym (ym),
  KEY idx_expense_region (region)
) ENGINE=InnoDB COMMENT='财务费用(月x区域x类型)';

-- ------------------------------------------------------------
-- [项目域] 项目主表
-- ------------------------------------------------------------
DROP TABLE IF EXISTS project_info;
CREATE TABLE project_info (
  project_id    VARCHAR(20)  PRIMARY KEY COMMENT 'P2026xxx',
  name          VARCHAR(100) NOT NULL,
  client        VARCHAR(100) NOT NULL COMMENT '客户(脱敏演示字段)',
  dept          VARCHAR(20)  NOT NULL COMMENT '研发/交付一/交付二/实施',
  plan_start    DATE         NOT NULL,
  plan_end      DATE         NOT NULL,
  actual_end    DATE         NULL COMMENT '实际完成日',
  plan_hours    DECIMAL(10,1) NOT NULL,
  actual_hours  DECIMAL(10,1) NOT NULL,
  KEY idx_project_dept (dept),
  KEY idx_project_planend (plan_end)
) ENGINE=InnoDB COMMENT='项目主表';

-- ------------------------------------------------------------
-- [项目域] 里程碑子表
-- ------------------------------------------------------------
DROP TABLE IF EXISTS project_milestone;
CREATE TABLE project_milestone (
  milestone_id BIGINT PRIMARY KEY AUTO_INCREMENT,
  project_id   VARCHAR(20) NOT NULL,
  m_name       VARCHAR(80) NOT NULL COMMENT '里程碑名',
  planned_date DATE        NOT NULL,
  actual_date  DATE        NULL,
  status       VARCHAR(20) NOT NULL COMMENT '已完成-按期/已完成-延期/进行中-延期/进行中-正常/未开始',
  KEY idx_ms_project (project_id),
  KEY idx_ms_status  (status)
) ENGINE=InnoDB COMMENT='项目里程碑';

-- ------------------------------------------------------------
-- [语义层] 指标定义: 口径/同义词/公式/血缘/权限标签
-- ------------------------------------------------------------
DROP TABLE IF EXISTS metric_definition;
CREATE TABLE metric_definition (
  metric_id      VARCHAR(40)  PRIMARY KEY,
  domain         VARCHAR(20)  NOT NULL COMMENT 'finance/project/maas',
  name           VARCHAR(40)  NOT NULL COMMENT '指标展示名',
  aliases        VARCHAR(200) NOT NULL COMMENT '同义词,逗号分隔',
  formula        VARCHAR(200) NOT NULL COMMENT '口径/计算公式说明',
  agg_sql        VARCHAR(300) NOT NULL COMMENT '聚合表达式, 供SQL生成与血缘',
  unit           VARCHAR(20)  DEFAULT '' COMMENT '单位: 元/万元/个/小时/%',
  grain          VARCHAR(50)  NOT NULL COMMENT '粒度说明',
  base_table     VARCHAR(50)  NOT NULL COMMENT '来源表',
  lineage        VARCHAR(300) DEFAULT '' COMMENT '血缘描述: 指标→字段→表',
  permission_tag VARCHAR(40)  NOT NULL COMMENT '权限标签: finance.*/project.*/project.mask_client/public',
  mask_fields    VARCHAR(200) DEFAULT '' COMMENT '命中该指标需脱敏的字段,逗号分隔',
  is_registered  TINYINT      DEFAULT 1 COMMENT '是否注册(歧义用例需毛利与净利润同时注册)'
) ENGINE=InnoDB COMMENT='语义层指标定义';

-- ------------------------------------------------------------
-- [权限] 系统用户(后端自治, 登录工具校验)
-- ------------------------------------------------------------
DROP TABLE IF EXISTS sys_user;
CREATE TABLE sys_user (
  user_id  VARCHAR(20) PRIMARY KEY,
  name     VARCHAR(50) NOT NULL,
  role     VARCHAR(20) NOT NULL COMMENT 'admin/finance/pm/staff',
  dept     VARCHAR(50) NOT NULL DEFAULT '',
  password VARCHAR(64) NOT NULL COMMENT '演示用明文, 报告注明仅教学'
) ENGINE=InnoDB COMMENT='后端系统用户';

-- ------------------------------------------------------------
-- [审计] 查询日志: draft/final SQL 双记录
-- ------------------------------------------------------------
DROP TABLE IF EXISTS audit_log;
CREATE TABLE audit_log (
  id          BIGINT PRIMARY KEY AUTO_INCREMENT,
  ts          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  user_id     VARCHAR(20) NOT NULL,
  role        VARCHAR(20) NOT NULL,
  question    VARCHAR(500) NOT NULL,
  draft_sql   TEXT COMMENT '模型生成SQL',
  final_sql   TEXT COMMENT '用户确认后的SQL',
  status      VARCHAR(20) NOT NULL COMMENT 'approved/denied/clarified/blocked/error',
  hook_result VARCHAR(300) DEFAULT '' COMMENT 'hook链结果/拦截原因',
  row_count   INT DEFAULT 0,
  elapse_ms   INT DEFAULT 0
) ENGINE=InnoDB COMMENT='查询审计日志';

-- ------------------------------------------------------------
-- [告警] 站内告警(异常洞察/阈值触发)
-- ------------------------------------------------------------
DROP TABLE IF EXISTS alert_message;
CREATE TABLE alert_message (
  id          BIGINT PRIMARY KEY AUTO_INCREMENT,
  ts          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  alert_type  VARCHAR(30) NOT NULL COMMENT '环比暴跌/预算偏差/延期风险/成本突增',
  title       VARCHAR(100) NOT NULL,
  content     VARCHAR(500) NOT NULL,
  level       VARCHAR(10) NOT NULL COMMENT 'high/medium/low',
  dept        VARCHAR(30) DEFAULT '',
  is_read     TINYINT DEFAULT 0
) ENGINE=InnoDB COMMENT='站内告警';

-- ------------------------------------------------------------
-- [MaaS域] LLM调用用量(第三个领域包的数据来源, 由工作台真实写入)
-- ------------------------------------------------------------
DROP TABLE IF EXISTS llm_usage;
CREATE TABLE llm_usage (
  id                 BIGINT PRIMARY KEY AUTO_INCREMENT,
  ts                 DATETIME NOT NULL,
  dept               VARCHAR(30) NOT NULL,
  model              VARCHAR(50) NOT NULL,
  prompt_tokens      INT NOT NULL,
  completion_tokens  INT NOT NULL,
  cost               DECIMAL(10,4) NOT NULL COMMENT '费用(元)',
  success            TINYINT NOT NULL DEFAULT 1,
  blocked            TINYINT NOT NULL DEFAULT 0 COMMENT '是否被hook拦截'
) ENGINE=InnoDB COMMENT='LLM调用用量(领域包三)';

-- ------------------------------------------------------------
-- [导出] 导出日志(受控查询结果/报告的每次导出留痕)
-- ------------------------------------------------------------
DROP TABLE IF EXISTS export_log;
CREATE TABLE export_log (
  id           BIGINT PRIMARY KEY AUTO_INCREMENT,
  ts           DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  user_id      VARCHAR(20) NOT NULL,
  role         VARCHAR(20) NOT NULL,
  export_type  VARCHAR(20) NOT NULL COMMENT 'query/report/csv',
  description  VARCHAR(300) DEFAULT '' COMMENT '导出内容说明',
  row_count    INT DEFAULT 0,
  content_md5  CHAR(32) DEFAULT '' COMMENT '导出内容摘要, 防篡改留痕',
  is_downloaded TINYINT DEFAULT 0
) ENGINE=InnoDB COMMENT='导出日志';

-- ------------------------------------------------------------
-- 只读账号(禁写兜底)
-- ------------------------------------------------------------
CREATE USER IF NOT EXISTS 'bi_ro'@'localhost' IDENTIFIED BY 'bi_ro_pass_2026';
GRANT SELECT ON bi_workbench.* TO 'bi_ro'@'localhost';

-- ------------------------------------------------------------
-- 应用账号: 业务查询仍走只读, 仅允许写 审计/告警/用量/导出日志 四类表
-- ------------------------------------------------------------
CREATE USER IF NOT EXISTS 'bi_app'@'localhost' IDENTIFIED BY 'bi_app_pass_2026';
GRANT SELECT ON bi_workbench.* TO 'bi_app'@'localhost';
GRANT INSERT, UPDATE ON bi_workbench.audit_log TO 'bi_app'@'localhost';
GRANT INSERT, UPDATE ON bi_workbench.alert_message TO 'bi_app'@'localhost';
GRANT INSERT, UPDATE ON bi_workbench.llm_usage TO 'bi_app'@'localhost';
GRANT INSERT, UPDATE ON bi_workbench.export_log TO 'bi_app'@'localhost';
FLUSH PRIVILEGES;
