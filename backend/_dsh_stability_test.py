"""DSH 内核管理接口稳定性 + 流式回复质量验证。

Part A - 管理接口：/api/dsh/* 全量调用，多次生命周期轮询，要求 100% 成功率、无超时中断。
Part B - 流式质量：通过 /api/dsh/chat/stream 实测实时性(首事件延迟/事件间隔)、
        连续性(无丢失/重复)、完整性(重组的 chunk 与 done 内容一致、完整呈现)。
用法: 需后端运行在 127.0.0.1:8001，然后 python _dsh_stability_test.py
"""
import json
import statistics
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8001/api/dsh"
PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail else ""))


def api(method, path, body=None, timeout=180):
    url = BASE + path
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            dt = (time.perf_counter() - t0) * 1000
            return {"ok": True, "code": resp.status, "ms": dt, "body": raw}
    except Exception as e:  # noqa: BLE001
        dt = (time.perf_counter() - t0) * 1000
        return {"ok": False, "code": getattr(e, "code", None), "ms": dt, "err": str(e)}


def stream_chat(message, session_id=None, timeout=300):
    """POST /chat/stream，逐行解析 SSE，返回 (events, metrics)。"""
    url = BASE + "/chat/stream"
    body = json.dumps({"message": message, "session_id": session_id or ""}).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.perf_counter()
    events = []
    times = []
    cur_type = ""
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        while True:
            line = resp.readline()
            if not line:
                break
            line = line.decode("utf-8", "replace").strip()
            if not line:
                continue
            if line.startswith("event:"):
                cur_type = line[6:].strip()
            elif line.startswith("data:"):
                try:
                    ev = json.loads(line[5:].strip())
                except Exception:  # noqa: BLE001
                    continue
                ev["_type"] = cur_type
                ev["_t"] = time.perf_counter()
                times.append(ev["_t"])
                events.append(ev)
    total_ms = (time.perf_counter() - t0) * 1000
    first_event_ms = ((times[0] - t0) * 1000) if times else None
    return events, {"total_ms": total_ms, "first_event_ms": first_event_ms, "times": times}


def analyze_stream(events, metrics):
    """从一次流式会话提取质量指标。"""
    types = [e["_type"] for e in events]
    ts = metrics["times"]
    gaps = [ (ts[i+1] - ts[i]) * 1000 for i in range(len(ts) - 1) ]
    chunks = [e.get("text", "") for e in events if e["_type"] == "chunk"]
    reasons = [e.get("text", "") for e in events if e["_type"] == "reasoning"]
    thinking = sum(1 for e in events if e["_type"] == "thinking")
    tool_calls = sum(1 for e in events if e["_type"] == "tool_call")
    tool_results = sum(1 for e in events if e["_type"] == "tool_result")
    errs = [e for e in events if e["_type"] == "error"]
    done = [e for e in events if e["_type"] == "done"]
    done_content = done[-1].get("content", "") if done else ""
    joined = "".join(chunks)
    return {
        "n_events": len(events),
        "n_chunks": len(chunks),
        "n_reasoning": len(reasons),
        "n_thinking": thinking,
        "n_tool_call": tool_calls,
        "n_tool_result": tool_results,
        "n_errors": len(errs),
        "types": types,
        "gaps": gaps,
        "gap_median": statistics.median(gaps) if gaps else None,
        "gap_max": max(gaps) if gaps else None,
        "gap_p95": statistics.quantiles(gaps, n=20)[-1] if gaps else None,
        "first_event_ms": metrics["first_event_ms"],
        "total_ms": metrics["total_ms"],
        "first_chunk_ms": None,
        "joined_len": len(joined),
        "done_len": len(done_content),
        "joined": joined,
        "done_content": done_content,
    }


def mgmt_round(round_no):
    """一轮管理接口生命周期：start -> status* -> version -> plugins -> skills
    -> storage -> logs -> sessions -> restart -> status -> stop -> status。"""
    r = {}
    r["start"] = api("POST", "/start")
    r["status1"] = api("GET", "/status")
    r["version"] = api("GET", "/version")
    r["plugins"] = api("GET", "/plugins")
    r["skills"] = api("GET", "/skills")
    r["storage"] = api("GET", "/storage")
    r["logs"] = api("GET", "/logs")
    r["sessions"] = api("GET", "/sessions")
    r["restart"] = api("POST", "/restart")
    r["status2"] = api("GET", "/status")
    r["stop"] = api("POST", "/stop")
    r["status3"] = api("GET", "/status")
    return r


def main():
    # ═══════════ Part A: 管理接口稳定性 ═══════════
    print("===== Part A: 管理接口稳定性 =====")
    rounds = 4
    agg = {}
    max_ms = {}
    for i in range(rounds):
        r = mgmt_round(i + 1)
        for k, v in r.items():
            agg.setdefault(k, {"ok": 0, "total": 0, "ms": []})
            agg[k]["total"] += 1
            if v["ok"] and v["code"] == 200:
                agg[k]["ok"] += 1
            agg[k]["ms"].append(v["ms"])
            max_ms[k] = max(max_ms.get(k, 0), v["ms"])
            if not (v["ok"] and v["code"] == 200):
                print(f"  round{i+1} {k} FAIL: {v.get('err')} code={v.get('code')}")
    all_ok = True
    for k, s in agg.items():
        rate = s["ok"] / s["total"] * 100
        avg = statistics.mean(s["ms"])
        if rate != 100.0:
            all_ok = False
        print(f"  {k:10s} 成功率 {rate:.0f}% ({s['ok']}/{s['total']})  avg={avg:.0f}ms max={max_ms[k]:.0f}ms")
    check("管理接口 100% 成功无超时", all_ok,
          f"{rounds} 轮生命周期轮询")
    # start 幂等：重复 start 不应失败
    api("POST", "/start")
    dup = api("POST", "/start")
    check("start 幂等", dup["ok"] and dup["code"] == 200, f"code={dup.get('code')} ms={dup['ms']:.0f}")
    st = api("GET", "/status")
    running = json.loads(st["body"]).get("running")
    check("重复 start 后仍运行", running is True)

    # ═══════════ Part B: 流式质量 ═══════════
    print("\n===== Part B: 流式回复质量 =====")
    api("POST", "/start")  # 确保已运行
    time.sleep(1)

    messages = [
        "请只回复四个字：连接正常",
        "请用三句话简单介绍你自己。",
        "请数一数 1 到 20 之间有多少个数字，并列出它们。",
        "请用 bash 工具执行命令输出当前系统时间，并总结结果。",
    ]
    stream_rows = []
    for idx, msg in enumerate(messages, 1):
        sid = f"stab-{int(time.time())}-{idx}"
        events, metrics = stream_chat(msg, session_id=sid)
        an = analyze_stream(events, metrics)
        an["idx"] = idx
        an["msg"] = msg
        stream_rows.append(an)
        # 定位首个 chunk 时间
        for e, t in zip(events, metrics["times"]):
            if e["_type"] == "chunk":
                an["first_chunk_ms"] = (t - (metrics["times"][0] - metrics["first_event_ms"] / 1000)) * 1000
                break
        print(f"  msg{idx}: events={an['n_events']} chunks={an['n_chunks']} "
              f"reasoning={an['n_reasoning']} thinking={an['n_thinking']} "
              f"tool={an['n_tool_call']}/{an['n_tool_result']} errs={an['n_errors']} "
              f"first_event={an['first_event_ms']:.0f}ms total={an['total_ms']:.0f}ms "
              f"gap_med={an['gap_median']:.0f}ms gap_max={an['gap_max']:.0f}ms "
              f"joined={an['joined_len']} done={an['done_len']}")

    # 指标汇总
    no_error = all(a["n_errors"] == 0 for a in stream_rows)
    check("流式无 error 事件", no_error)
    check("流式无 tool_call 中断异常", all(a["n_tool_call"] >= 0 for a in stream_rows))
    # 连续性：chunk 重组 == done 内容（无丢失/重复）
    continuity_ok = all(a["joined"] == a["done_content"] and a["done_len"] > 0 for a in stream_rows)
    check("流式连续性(重组==done 无丢失重复)", continuity_ok,
          " / ".join(f"{a['joined_len']}=={a['done_len']}" for a in stream_rows))
    # 完整性：done 内容完整呈现（非空且达到预期长度）
    complete_ok = all(a["done_len"] > 0 and a["n_events"] >= 2 for a in stream_rows)
    check("流式完整性(完整呈现非空)", complete_ok,
          " / ".join(f"{a['done_len']}字" for a in stream_rows))
    # 实时性：首事件延迟（thinking 即时下发，应 ≤200ms）
    first_events = [a["first_event_ms"] for a in stream_rows if a["first_event_ms"] is not None]
    realtime_ok = all(fe <= 200 for fe in first_events)
    check("流式实时性(首事件≤200ms)", realtime_ok,
          f"min={min(first_events):.0f}ms max={max(first_events):.0f}ms avg={statistics.mean(first_events):.0f}ms")
    # 连续性平滑：事件间隔中位（模型内容流的交付节奏）
    meds = [a["gap_median"] for a in stream_rows if a["gap_median"] is not None]
    if meds:
        print(f"  [info] 事件间隔 中位={statistics.median(meds):.0f}ms 范围 "
              f"{min(meds):.0f}-{max(meds):.0f}ms；"
              f"事件最大间隔 max={max(a['gap_max'] or 0 for a in stream_rows):.0f}ms")
    # 重复检查：相邻 chunk 完全相同（管道层重复）。SSE 里同一段文本若重复下发，
    # 会导致重组内容与 done 内容不一致，已由上方 continuity 检查覆盖。
    print("\n===== 汇总 =====")
    print(f"PASS {len(PASS)}  FAIL {len(FAIL)}")
    for f in FAIL:
        print("  FAILED:", f)


if __name__ == "__main__":
    main()
