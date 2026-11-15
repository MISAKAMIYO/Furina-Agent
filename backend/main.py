"""
Furina Agent Web - 后端服务器启动入口

运行方式:
    python backend/main.py

或使用 uvicorn:
    uvicorn zhiyan_web.app:app --host 0.0.0.0 --port 8000
"""

import os
import sys

# 添加 backend 目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    import uvicorn
    
    from zhiyan_web.app import app
    
    # 端口通过环境变量 ZHIYAN_PORT 下传（Rust 壳分配），默认 8001 兼容桌面壳旧路径
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("ZHIYAN_PORT", "8001")),
        log_level="info",
    )
