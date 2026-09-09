# -*- coding: utf-8 -*-
"""bi_workbench MCP Server 启动入口(stdio)。
用于: python run_server.py (被 MCP 客户端以子进程方式拉起, 如 dsh)。
"""
from backend import server

if __name__ == "__main__":
    server.mcp.run(transport="stdio")
