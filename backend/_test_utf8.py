# -*- coding: utf-8 -*-
"""用 UTF-8 正确发送测试命令到运行中的后端"""
import json
import urllib.request

BASE = "http://127.0.0.1:8001/api/chat/send"

def send(msg):
    data = json.dumps({"content": msg, "role": "user"}).encode("utf-8")
    req = urllib.request.Request(BASE, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8")
            r = json.loads(body)
            return r.get("response", "")[:150]
    except Exception as e:
        return f"ERROR: {e}"

if __name__ == "__main__":
    for msg in ["/盒 1668255883", "/盒", "/开盒 123", "/plugin_list", "/hello"]:
        print(f"[{msg}] => {send(msg)}")
