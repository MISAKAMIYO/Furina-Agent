"""诊断: 启动 npm 内核并把 stderr 实时落盘, 发送 initialize + session/prompt, 观察事件与日志。"""
import asyncio, json, os, sys, time

NPM_CORE = r"d:\Python\NEW_start\ZHIYAN AGENT WEB\dsh-suite\npm-core"
BIN = os.path.join(NPM_CORE, "node_modules", "@deepseek-ai", "dsh-sdk-jsonrpc-demo", "lib", "bin.js")
CONFIG = os.path.join(NPM_CORE, "zhiyan.cordis.yml")
ERR_LOG = os.path.join(NPM_CORE, "_dsh_diag.stderr.log")

def env_for():
    e = dict(os.environ)
    e["DEEPSEEK_API_KEY"] = "sk-503b174c1fad4d638166be629fbe1272"
    e["DEEPSEEK_BASE_URL"] = "https://api.deepseek.com"
    e["PYTHONUNBUFFERED"] = "1"
    return e

async def main():
    print("start runtime...", flush=True)
    proc = await asyncio.create_subprocess_exec(
        "node", BIN, CONFIG, cwd=NPM_CORE, env=env_for(),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    rid = 0
    def req(method, params=None):
        nonlocal rid
        rid += 1
        return {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
    async def send(obj):
        proc.stdin.write((json.dumps(obj) + "\n").encode())
        await proc.stdin.drain()
    errf = open(ERR_LOG, "w", encoding="utf-8")
    async def pump_stderr():
        while True:
            line = await proc.stderr.readline()
            if not line: break
            errf.write(line.decode(errors="replace")); errf.flush()
    asyncio.create_task(pump_stderr())
    async def read_msg(timeout=120):
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
        except asyncio.TimeoutError:
            return {"__timeout__": True}
        if not line: return None
        return json.loads(line.decode(errors="replace"))
    await send(req("initialize", {"cwd": NPM_CORE, "provider": "deepseek-official",
                                  "model": "deepseek-v4-flash", "maxTokens": 4096}))
    init = await read_msg()
    print("[initialize]", init, flush=True)
    sid = "diag-" + str(int(time.time()))
    await send(req("session/prompt", {"sessionId": sid,
                                      "contentBlocks": [{"type": "text", "text": "hi"}], "agent": "spine"}))
    print("prompt sent, waiting events...", flush=True)
    t0 = time.time()
    while time.time() - t0 < 150:
        msg = await read_msg(20)
        if msg is None:
            print("[EOF]", flush=True); break
        if msg.get("__timeout__"):
            print(f"  ...({int(time.time()-t0)}s no msg)", flush=True)
            continue
        m = msg.get("method")
        if m == "session.event":
            ev = msg.get("params", {}).get("event", {})
            print(f"  [ev:{ev.get('type')}] {json.dumps(ev.get('data'))[:160]}", flush=True)
        else:
            print(f"  [{m}] {json.dumps(msg)[:200]}", flush=True)
        if m == "session.status" and msg.get("params", {}).get("status") == "idle":
            break
    errf.close()
    try:
        await send(req("shutdown"))
        await asyncio.wait_for(proc.wait(), timeout=5)
    except Exception:
        proc.kill()
    print("stderr log:", ERR_LOG, flush=True)

if __name__ == "__main__":
    asyncio.run(main())
