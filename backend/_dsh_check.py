import sys, asyncio, traceback
sys.path.insert(0, r"d:\Python\NEW_start\ZHIYAN AGENT WEB\backend")

async def main():
    from zhiyan_web.dsh.engine import DshEngine, DSH_CORE, DSH_RUNTIME, DSH_BIN
    print("DSH_CORE=", DSH_CORE)
    eng = DshEngine()
    try:
        await asyncio.wait_for(eng.start(), timeout=200)
        print("STARTED alive=", eng.alive, "model=", eng.model)
        st = eng.status()
        print("status running=", st["running"], "pid=", st["pid"], "last_error=", st["last_error"])
        import time
        sid = "zhiyan-fresh-" + str(int(time.time()))
        print("--- prompt ---")
        async for ev in eng.prompt_stream(sid, "请只回复四个字：连接正常"):
            print("EVENT", ev.get("type"), repr(str(ev.get("text", "") or ev.get("content", ""))[:200]))
        print("--- prompt done ---")
        print("--- runtime stderr tail ---")
        for line in eng.log_tail(120):
            print("STDERR|", line)
    except Exception:
        traceback.print_exc()
    finally:
        try:
            await eng.stop()
        except Exception as e:
            print("stop err", e)

if __name__ == "__main__":
    asyncio.run(main())