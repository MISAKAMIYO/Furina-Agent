"""引擎级诊断: 用 DshEngine.start() 启动 npm 内核, 实时打印 stderr 与超时期间的耗时。"""
import sys, os, asyncio, time
sys.path.insert(0, r"d:\Python\NEW_start\ZHIYAN AGENT WEB\backend")

async def main():
    from zhiyan_web.dsh.engine import DshEngine, DSH_CORE, DSH_RUNTIME, DSH_GENERATED_CONFIG
    print("DSH_CORE     =", DSH_CORE)
    print("DSH_RUNTIME  =", DSH_RUNTIME)
    print("GEN CONFIG   =", DSH_GENERATED_CONFIG)
    print("cfg exists   =", os.path.isfile(DSH_GENERATED_CONFIG))

    eng = DshEngine()
    t0 = time.time()
    try:
        await asyncio.wait_for(eng.start(), timeout=260)
        print(f"[OK] start 完成, 耗时 {time.time()-t0:.1f}s, model={eng.model}")
    except Exception as e:
        print(f"[FAIL] start 异常: {type(e).__name__}: {e} (耗时 {time.time()-t0:.1f}s)")
    print("---- stderr tail ----")
    for line in eng.stderr_log[-60:]:
        print(" |", line[:200])
    await eng.stop()

if __name__ == "__main__":
    asyncio.run(main())
