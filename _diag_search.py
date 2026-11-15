# -*- coding: utf-8 -*-
"""诊断 web_search 工具"""
import sys, asyncio, json

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

async def main():
    import requests
    q = "Python 3.13 新特性"
    url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(q)}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        print("HTTP 状态:", resp.status_code)
        print("响应头:", dict(resp.headers).get("Content-Type"), "| 长度:", len(resp.text))
        text = resp.text[:1500]
        print("--- 响应前 1500 字符 ---")
        print(text)
    except Exception as e:
        print("请求异常:", repr(e))

asyncio.run(main())
