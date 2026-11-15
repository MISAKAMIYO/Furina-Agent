"""独立测试 npm 发行包内核(dsh-suite/npm-core)的启动与 JSON-RPC 连通性。"""
import asyncio, json, os, sys, traceback, time

NPM_CORE = r"d:\Python\NEW_start\ZHIYAN AGENT WEB\dsh-suite\npm-core"
BIN = os.path.join(NPM_CORE, "node_modules", "@deepseek-ai", "dsh-sdk-jsonrpc-demo", "lib", "bin.js")
CONFIG = os.path.join(NPM_CORE, "zhiyan.cordis.yml")

def env_for():
    e = dict(os.environ)
    e["DEEPSEEK_API_KEY"] = "sk-503b174c1fad4d638166be629fbe1272"
    e["DEEPSEEK_BASE_URL"] = "https://api.deepseek.com"
    return e

async def main():
    print("BIN =", BIN, "exists:", os.path.isfile(BIN))
    print("CONFIG =", CONFIG, "exists:", os.path.isfile(CONFIG))
    proc = await asyncio.create_subprocess_exec(
        "node", BIN, CONFIG,
        cwd=NPM_CORE, env=env_for(),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
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

    stderr_lines = []
    async def pump_stderr():
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                stderr_lines.append(line.decode(errors="replace").rstrip())
        except Exception:
            pass

    stderr_task = asyncio.create_task(pump_stderr())
    wait = asyncio.get_event_loop().time

    async def read_msg(timeout=240):
        """Read one line from stdout (blocking w/ timeout)."""
        line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
        if not line:
            return None
        return json.loads(line.decode(errors="replace"))

    try:
        # 1) initialize
        await send(req("initialize", {"cwd": NPM_CORE, "provider": "deepseek-official",
                                     "model": "deepseek-v4-flash", "maxTokens": 4096}))
        init = await read_msg(120)
        print("[initialize]", "result" in (init or {}), json.dumps(init)[:200] if init else "NONE")
        if not (init and "result" in init):
            print("--- stderr tail ---")
            for l in stderr_lines[-30:]:
                print("STDERR|", l)
            return

        # 2) session/prompt
        sid = "npm-test-" + str(int(time.time()))
        await send(req("session/prompt", {"sessionId": sid,
                                          "contentBlocks": [{"type": "text", "text": "请只回复四个字：连接正常"}],
                                          "agent": "spine"}))
        events = []
        while True:
            msg = await read_msg(240)
            if msg is None:
                print("[EOF] runtime closed")
                break
            m = msg.get("method")
            if m == "session.event":
                ev = msg.get("params", {}).get("event", {})
                events.append(ev)
                t = ev.get("type")
                if t in ("chunk", "done", "error", "tool_call"):
                    txt = str(ev.get("text") or ev.get("content") or "")[:100]
                    print(f"  [event:{t}] {txt}")
            if m == "session.status" and msg.get("params", {}).get("status") in ("idle", "done"):
                break
            if "error" in msg:
                print("  [rpc-error]", json.dumps(msg)[:300])
                break
        # 汇总 assistant/message 的最终回复内容
        final = ""
        for e in events:
            if e.get("type") == "assistant/message":
                msg_ = e.get("data", {}).get("message") or {}
                blocks = msg_.get("content") or []
                final = "".join(str(b.get("text", "")) for b in blocks if isinstance(b, dict) and b.get("type") == "text")
                break
        print("\nFINAL CONTENT:", repr(final[:200]))
        print("events count:", len(events))
    finally:
        try:
            await send(req("shutdown"))
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()
        except Exception:
            proc.kill()
        await asyncio.sleep(0.2)
        print("\n--- stderr tail ---")
        for l in stderr_lines[-20:]:
            print("STDERR|", l)

if __name__ == "__main__":
    asyncio.run(main())
