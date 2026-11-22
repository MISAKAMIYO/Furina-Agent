# -*- coding: utf-8 -*-
"""BetterGI 通知中枢 + 更新检查的离线自测（不联网、不碰 QQ）。

运行：python backend/zhiyan_web/bettergi/test_notify_updates.py
也可以交给 pytest：pytest backend/zhiyan_web/bettergi/test_notify_updates.py -q
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from zhiyan_web.bettergi.notify import NotifyHub, _parse_hhmm  # noqa: E402
from zhiyan_web.bettergi.updates import (  # noqa: E402
    UpdateManager,
    parse_version,
    version_str,
)


class FakeSender:
    def __init__(self, fail_times: int = 0):
        self.calls = []
        self.fail_times = fail_times

    async def __call__(self, channel, text):
        self.calls.append((channel, text))
        if self.fail_times > 0:
            self.fail_times -= 1
            return {"ok": False, "error": "boom"}
        return {"ok": True}


def test_parse_hhmm():
    assert _parse_hhmm("23:30") == 23 * 60 + 30
    assert _parse_hhmm("08:00") == 480
    assert _parse_hhmm("") is None
    assert _parse_hhmm("25:00") is None
    print("[ok] _parse_hhmm")


def test_quiet_hours_cross_midnight():
    cfg = {"notify_quiet_start": "23:00", "notify_quiet_end": "08:00", "notify_qq": True}
    hub = NotifyHub(sender=FakeSender(), config_getter=lambda: cfg)
    assert hub._quiet_now(23 * 60 + 30) is True
    assert hub._quiet_now(3 * 60) is True
    assert hub._quiet_now(12 * 60) is False
    print("[ok] quiet hours across midnight")


def test_dedupe_and_levels():
    cfg = {"notify_qq": True, "notify_dedupe_window": 60, "notify_min_level": "warning"}
    sender = FakeSender()
    hub = NotifyHub(sender=sender, config_getter=lambda: cfg)

    async def run():
        r1 = await hub.send("同上", level="warning", event="e1")
        r2 = await hub.send("同上", level="warning", event="e1")
        r3 = await hub.send("低等级", level="info", event="e2")
        r4 = await hub.send("同上", level="warning", event="e1", force=True)
        return r1, r2, r3, r4

    r1, r2, r3, r4 = asyncio.run(run())
    assert r1["sent"] == ["napcat"], r1
    assert r2["skipped"] and r2["skipped"][0]["reason"] == "duplicate", r2
    assert r3["skipped"] and r3["skipped"][0]["reason"] == "below-min-level", r3
    assert r4["sent"] == ["napcat"], r4
    assert len(sender.calls) == 2
    print("[ok] dedupe + min level + force")


def test_retry():
    cfg = {"notify_qq": True, "notify_dedupe_window": 0}
    sender = FakeSender(fail_times=1)
    hub = NotifyHub(sender=sender, config_getter=lambda: cfg)

    async def run():
        return await hub.send("重试一次", level="info", event="e3", retries=1)

    result = asyncio.run(run())
    assert result["sent"] == ["napcat"], result
    assert len(sender.calls) == 2, sender.calls
    items = hub.recent(5)
    assert items[0]["attempts"] == 2 and items[0]["ok"] is True
    print("[ok] retry then success")


def test_version_compare():
    assert parse_version("v0.67.0.0") == (0, 67, 0, 0)
    assert parse_version("BetterGI 0.68") == (0, 68, 0, 0)
    assert version_str(parse_version("0.67")) == "0.67.0.0"
    assert parse_version("0.68.0") > parse_version("0.67.9")
    print("[ok] version parse/compare")


def test_update_manager_with_fake_release():
    tmp = tempfile.mkdtemp()
    cfg = {"update_repo": "fake/repo"}
    release = {
        "ok": True,
        "tag": "v0.68.0",
        "version": "0.68.0.0",
        "name": "0.68.0",
        "url": "https://example.invalid",
        "published": "",
        "notes": "",
        "prerelease": False,
        "source": "fake",
    }
    exe = os.path.join(tmp, "BetterGI.exe")
    with open(exe, "wb") as f:
        f.write(b"MZ")  # 不是真 PE，版本读取会失败，退化为空
    mgr = UpdateManager(
        history_path=os.path.join(tmp, "hist.json"),
        exe_getter=lambda: exe,
        config_getter=lambda: cfg,
        release_getter=lambda repo: release,
    )
    result = mgr.check(record=True)
    assert result["remote"] == "0.68.0.0", result
    assert result["has_update"] is None and result["direction"] == "unknown-local", result
    hist = mgr.history(5)
    assert hist and hist[0]["kind"] == "check"
    print("[ok] update manager with injected release")


if __name__ == "__main__":
    test_parse_hhmm()
    test_quiet_hours_cross_midnight()
    test_dedupe_and_levels()
    test_retry()
    test_version_compare()
    test_update_manager_with_fake_release()
    print("\nall notify/update tests passed")
