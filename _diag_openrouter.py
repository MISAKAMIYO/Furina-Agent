# -*- coding: utf-8 -*-
"""拉取 OpenRouter 当前免费模型列表"""
import sys, json

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import requests
url = "https://openrouter.ai/api/v1/models"
try:
    resp = requests.get(url, timeout=20)
    print("HTTP:", resp.status_code)
    data = resp.json()
    models = data.get("data", [])
    free = [m for m in models if m.get("id", "").endswith(":free")]
    print("免费模型数量:", len(free))
    for m in sorted(free, key=lambda x: x["id"]):
        name = m.get("name", "")
        ctx = m.get("context_length", "")
        pricing = m.get("pricing", {})
        print(f"{m['id']} | {name} | ctx={ctx} | prompt=${pricing.get('prompt')} completion=${pricing.get('completion')}")
except Exception as e:
    print("请求异常:", repr(e))
