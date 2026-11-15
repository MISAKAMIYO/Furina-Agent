"""DSH 内核全面测试：启动稳定性、功能完整性、访问通畅性。"""
import sys, os, asyncio, traceback, time
sys.path.insert(0, r"d:\Python\NEW_start\ZHIYAN AGENT WEB\backend")

PASS = []
FAIL = []

def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))

async def main():
    from zhiyan_web.dsh.engine import DshEngine, DSH_CORE, DSH_RUNTIME, DSH_BIN
    check("内核定位(DSH_CORE)", os.path.isdir(DSH_CORE) and os.path.isfile(os.path.join(DSH_CORE, "package.json")), DSH_CORE)
    check("jsonrpc bin 存在", os.path.isfile(DSH_BIN), DSH_BIN)

    eng = DshEngine()
    try:
        # 1) 冷启动
        t0 = time.time()
        await asyncio.wait_for(eng.start(), timeout=200)
        dt = time.time() - t0
        check("冷启动成功", eng.alive, f"耗时 {dt:.1f}s model={eng.model}")
        st = eng.status()
        check("状态 running=True", st["running"] is True, f"pid={st['pid']}")
        check("last_error 为空", not st["last_error"], st["last_error"])

        # 2) 同一会话多轮对话（功能完整性 + 会话连续性）
        sid = "zhiyan-full-" + str(int(time.time()))
        async def prompt(text):
            return await prompt_sid(sid, text)
        async def prompt_sid(sid_, text):
            out = []
            async for ev in eng.prompt_stream(sid_, text):
                if ev.get("type") in ("chunk", "done", "error"):
                    out.append(ev)
            content = ""
            for e in out:
                if e.get("type") == "done":
                    content = e.get("content", "")
                if e.get("type") == "error":
                    content = "[ERR] " + str(e.get("message", ""))
            return content, out
        c1, _ = await prompt("请只回复四个字：连接正常")
        check("第一轮对话返回", len(c1) > 0, c1[:60])
        c2, _ = await prompt("请只回复四个字：测试通过")
        check("第二轮对话返回", len(c2) > 0, c2[:60])

        # 3) 工具调用功能（bash 工具）
        c3, _ = await prompt("请用 bash 工具执行命令输出当前工作目录，只回复目录路径。")
        check("工具调用(bash)返回", len(c3) > 0, c3[:80])

        # 4) 会话持久化文件
        sess = eng.list_sessions()
        check("会话持久化文件已生成", any(s["name"].startswith(sid) for s in sess), f"{len(sess)} 个会话")

        # 5) 重启稳定性
        await eng.restart()
        check("重启后存活", eng.alive, f"pid={eng.status()['pid']}")
        sid2 = "zhiyan-restart-" + str(int(time.time()))
        c4, ev4 = await prompt_sid(sid2, "请只回复四个字：重启正常")
        check("重启后对话正常", len(c4) > 0, f"{c4[:60]} | events={[e.get('type') for e in ev4]}")

        # 6) 幂等启动（重复 start 不应报错）
        try:
            await eng.start()
            check("幂等启动", True)
        except Exception as e:
            check("幂等启动", False, str(e))

    except Exception:
        traceback.print_exc()
        check("整体流程无异常", False, "出现未捕获异常")
    finally:
        try:
            await eng.stop()
            check("停止后进程回收", eng.proc is None or eng.proc.returncode is not None)
        except Exception as e:
            check("停止后进程回收", False, str(e))

    print("\n===== 汇总 =====")
    print(f"PASS {len(PASS)}  FAIL {len(FAIL)}")
    for f in FAIL:
        print("  FAILED:", f)

if __name__ == "__main__":
    import os
    asyncio.run(main())
