# -*- coding: utf-8 -*-
"""
增量补丁(幂等): 为已初始化的 bi_workbench 库补齐 export_log 表与 bi_app 写入授权。
不触碰任何业务数据; 全部 IF NOT EXISTS / 重复授权无害。
用法: python scripts/patch_db.py --password <root密码>
"""
import argparse

import pymysql

DDL = [
    # 1) 导出日志表
    """CREATE TABLE IF NOT EXISTS export_log (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        ts DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        user_id VARCHAR(20) NOT NULL,
        role VARCHAR(20) NOT NULL,
        export_type VARCHAR(20) NOT NULL COMMENT 'query/report/csv',
        description VARCHAR(300) DEFAULT '' COMMENT '导出内容说明',
        row_count INT DEFAULT 0,
        content_md5 CHAR(32) DEFAULT '' COMMENT '导出内容摘要, 防篡改留痕',
        is_downloaded TINYINT DEFAULT 0
      ) ENGINE=InnoDB COMMENT='导出日志'""",
    # 2) bi_app 应用账号仅追加导出日志写权限(最小权限)
    "GRANT INSERT, UPDATE ON bi_workbench.export_log TO 'bi_app'@'localhost'",
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=3306)
    ap.add_argument("--user", default="root")
    ap.add_argument("--password", required=True)
    args = ap.parse_args()
    c = pymysql.connect(host=args.host, port=args.port, user=args.user,
                        password=args.password, database="bi_workbench",
                        charset="utf8mb4", autocommit=True)
    try:
        with c.cursor() as cur:
            for ddl in DDL:
                cur.execute(ddl)
            cur.execute("FLUSH PRIVILEGES")
        print("patch OK: export_log + bi_app grant 已就绪")
    finally:
        c.close()


if __name__ == "__main__":
    main()
