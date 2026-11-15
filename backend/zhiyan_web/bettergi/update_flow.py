"""
BetterGI / 原神 更新流程步骤机

设计：
- 每一步是「观察 → 判定 → 动作」的声明式描述，坐标不写死，全部来自识别结果
- 观察统一走 vision.recognize_scene（OCR + 图案模板）
- 动作只允许：launch / wait / click_text / click_template / key / scroll / sleep /
  finish_if / wait_version_change
- 步骤顺序严格对应手写稿 R5 的两条流程，不自行增删

步骤字段：
- name：步骤名（事件流与 UI 展示）
- action：动作，见上
- expect：任一关键词出现即视为到达该状态
- template：图案模板名（与 expect 二选一或并用）
- target：action=launch 时指定 game / bgi
- key / times / seconds：按键、滚动格数、等待秒数
- region：观察区域（0~1 比例）
- timeout / interval：超时与轮询间隔（秒）
- on_fail：abort（默认）/ continue
- optional：失败也只记录，不中断
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional

from . import vision

logger = logging.getLogger("ZhiYanWeb.bettergi.update_flow")

_VERSION_RE = re.compile(r"\d+\.\d+(?:\.\d+)?")

# 原神更新流程（R5.4：接入按钮模板，OCR 作为兜底）
# 模板来自用户截图，位于 data/bettergi/templates/，可用 tools/gen_bgi_templates.py 重新生成。
# 区域参数用 0~1 比例限制在启动器右下角，避免桌面其它黄色元素误命中。
GAME_FLOW: List[Dict[str, Any]] = [
    {"name": "打开米哈游启动器", "action": "launch", "target": "game", "timeout": 30},
    {
        "name": "等待启动器就绪",
        "action": "wait",
        "expect": ["米哈游", "启动器", "原神"],
        "timeout": 180,
        "interval": 3,
    },
    {
        "name": "已暂停则点「恢复下载」",
        "action": "click_text",
        "template": "game_btn_resume",
        "expect": ["恢复下载"],
        "region": [0.45, 0.55, 1.0, 1.0],
        "timeout": 45,
        "interval": 3,
        "on_fail": "continue",
        "optional": True,
    },
    {
        "name": "点击「更新游戏」",
        "action": "click_text",
        "template": "game_btn_update",
        "expect": ["更新游戏"],
        "region": [0.45, 0.55, 1.0, 1.0],
        "timeout": 240,
        "interval": 3,
        "skip_if": ["开始游戏", "启动游戏"],
        "on_fail": "continue",
    },
    {
        "name": "确认下载 / 校验已开始",
        "action": "wait",
        "expect": ["下载中", "正在下载", "校验", "修复", "暂停"],
        "timeout": 180,
        "interval": 4,
        "on_fail": "continue",
    },
    {
        "name": "等待出现「开始游戏」",
        "action": "wait",
        "template": "game_btn_start",
        "expect": ["开始游戏", "启动游戏"],
        "region": [0.45, 0.55, 1.0, 1.0],
        "timeout": 3600,
        "interval": 6,
    },
]

# BetterGI 更新流程（R5.6：下载 → 安装程序 → 重启 → 版本变化）
# 说明：BGI 的更新是「下载安装包 → 拉起安装程序 → 装完重启」，
# 安装程序是独立窗口（标题含 安装/Setup/Install），必须单独观察，
# 且下载/安装耗时可能几分钟，所以 interval 放到 4~5 秒、超时放宽、失败不中断。
BGI_FLOW: List[Dict[str, Any]] = [
    {"name": "打开 BetterGI", "action": "launch", "target": "bgi", "timeout": 30},
    {
        "name": "等待主界面就绪",
        "action": "wait",
        "app": "bgi",
        "expect": ["启动", "实时触发", "一条龙", "全自动", "设置"],
        "timeout": 120,
        "interval": 3,
    },
    {
        "name": "点击「立即更新」",
        "action": "click_text",
        "app": "bgi",
        "template": "bgi_btn_update",
        "expect": ["立即更新", "马上更新"],
        "timeout": 90,
        "interval": 3,
        "on_fail": "continue",
    },
    {
        "name": "等待下载进度（BGI 窗口内）",
        "action": "wait",
        "app": "bgi",
        "expect": ["下载中", "正在下载", "解压", "校验", "更新中", "正在更新"],
        "timeout": 600,
        "interval": 5,
        "on_fail": "continue",
    },
    {
        "name": "等待下载 / 安装程序出现",
        "action": "wait",
        "app": "installer",
        "expect": ["安装", "下一步", "解压", "正在下载", "下载中", "校验"],
        "timeout": 600,
        "interval": 5,
        "on_fail": "continue",
    },
    {
        "name": "点击「安装 / 下一步」",
        "action": "click_text",
        "app": "installer",
        "expect": ["立即安装", "安装", "下一步", "开始安装"],
        "timeout": 180,
        "interval": 4,
        "on_fail": "continue",
    },
    {
        "name": "等待安装完成",
        "action": "wait",
        "app": "installer",
        "expect": ["安装完成", "完成", "立即体验", "关闭", "下一步"],
        "timeout": 900,
        "interval": 5,
        "on_fail": "continue",
    },
    {
        "name": "点击「完成 / 关闭」",
        "action": "click_text",
        "app": "installer",
        "expect": ["立即体验", "完成", "关闭", "下一步"],
        "timeout": 180,
        "interval": 4,
        "on_fail": "continue",
    },
    {
        "name": "等待 BetterGI 重新启动",
        "action": "wait",
        "app": "bgi",
        "expect": ["启动", "实时触发", "设置"],
        "timeout": 300,
        "interval": 5,
        "on_fail": "continue",
    },
    {
        "name": "等待版本号变化",
        "action": "wait_version_change",
        "app": "bgi",
        "region": [0.0, 0.0, 0.40, 0.12],
        "timeout": 900,
        "interval": 5,
        "on_fail": "continue",
    },
    # 已是最新就直接收尾，避免无更新时空等十几分钟
    {
        "name": "已是最新则结束",
        "action": "finish_if",
        "app": "bgi",
        "expect": ["已是最新版本", "当前已是最新", "已是最新"],
        "timeout": 20,
        "interval": 3,
    },
]

FLOWS = {"game": GAME_FLOW, "bgi": BGI_FLOW}

FLOW_NAMES = {"game": "原神更新", "bgi": "BetterGI 更新"}


def flow_definitions() -> Dict[str, Any]:
    """供 UI/状态展示的步骤清单"""
    return {
        key: {"name": FLOW_NAMES.get(key, key), "steps": [dict(s) for s in steps]}
        for key, steps in FLOWS.items()
    }


def load_flow(target: str, custom: Any = None) -> List[Dict[str, Any]]:
    """载入步骤定义：用户自定义优先，非法则回退内置"""
    data = custom
    if isinstance(data, str) and data.strip():
        try:
            data = json.loads(data)
        except Exception:
            data = None
    if isinstance(data, list) and data and all(isinstance(x, dict) for x in data):
        return [dict(x) for x in data]
    return [dict(x) for x in FLOWS.get(target) or []]


def _find_text(items: List[Dict[str, Any]], keywords: List[str]) -> Dict[str, Any]:
    for keyword in keywords or []:
        if not keyword:
            continue
        for item in items:
            if keyword in (item.get("text") or ""):
                return item
    return {}


def _pick_version(text: str) -> str:
    found = _VERSION_RE.findall(text or "")
    return found[0] if found else ""


async def _observe(step: Dict[str, Any], save_dir: str, app: str = "game") -> Dict[str, Any]:
    """按步骤的区域与模板做一次观察"""
    region = step.get("region")
    shot = await asyncio.to_thread(vision.capture_window, save_dir, region, app)
    if not shot.get("success"):
        return {"ok": False, "message": shot.get("message") or "截图失败"}

    rules: List[Dict[str, Any]] = []
    if step.get("template"):
        rules.append(
            {
                "template": step.get("template"),
                "action": "wait",
                "threshold": step.get("threshold") or 0.85,
            }
        )
    image = shot.get("image")
    try:
        scene = await asyncio.to_thread(vision.recognize_scene, image, rules, None)
    finally:
        # 识别完立刻释放内存图像（不落盘）
        try:
            if image is not None:
                image.close()
        except Exception:
            pass
    items = scene.get("items") or []

    item: Dict[str, Any] = {}
    if step.get("template"):
        for candidate in items:
            if candidate.get("kind") == "template":
                item = candidate
                break
    if not item:
        item = _find_text(items, list(step.get("expect") or []))

    return {
        "ok": True,
        "shot": {k: v for k, v in shot.items() if k != "image"},
        "scene": scene,
        "items": items,
        "item": item,
        "text": scene.get("text") or "",
    }


async def _run_step(step: Dict[str, Any], save_dir: str, ctx: Dict[str, Any]) -> Dict[str, Any]:
    """执行单步；返回 ok / finish / skipped / matched"""
    name = step.get("name") or step.get("action") or "步骤"
    action = str(step.get("action") or "wait").lower()
    app = str(step.get("app") or ctx.get("app") or "game")
    timeout = max(1.0, float(step.get("timeout") or 60))
    interval = max(0.5, float(step.get("interval") or 2))
    deadline = time.time() + timeout

    if action == "launch":
        launcher = ctx.get("launcher")
        if not callable(launcher):
            return {"ok": False, "name": name, "message": "未提供启动器"}
        result = launcher(str(step.get("target") or ""))
        if asyncio.iscoroutine(result):
            result = await result
        return {
            "ok": bool(result.get("ok")) if isinstance(result, dict) else bool(result),
            "name": name,
            "message": (result or {}).get("message", "") if isinstance(result, dict) else "",
        }

    if action == "sleep":
        await asyncio.sleep(max(0.0, float(step.get("seconds") or 3)))
        return {"ok": True, "name": name, "message": "已等待"}

    if action == "scroll":
        done = await asyncio.to_thread(vision.scroll, int(step.get("times") or -5))
        await asyncio.sleep(1.0)
        return {"ok": done, "name": name, "message": "已滚动页面" if done else "滚动失败"}

    last = ""
    while time.time() < deadline:
        if ctx.get("stop"):
            return {"ok": False, "name": name, "message": "已取消"}
        obs = await _observe(step, save_dir, app)
        if not obs.get("ok"):
            last = obs.get("message") or "截图失败"
            await asyncio.sleep(interval)
            continue

        # 跳过条件：已经处于终态（例如原神已更新完，按钮已是「开始游戏」）
        skip_hit = _find_text(obs["items"], list(step.get("skip_if") or []))
        if skip_hit:
            return {"ok": True, "skipped": True, "name": name, "message": "已处于终态，跳过"}

        if action == "finish_if":
            hit = _find_text(obs["items"], list(step.get("expect") or []))
            if hit:
                return {"ok": True, "finish": True, "name": name, "matched": hit.get("text")}
            await asyncio.sleep(interval)
            continue

        if action == "wait_version_change":
            version = _pick_version(obs.get("text") or "")
            before = ctx.get("version_before")
            if not before and version:
                ctx["version_before"] = version
                last = "已记录初始版本号 %s" % version
            elif version and before and version != before:
                return {"ok": True, "name": name, "matched": version, "message": "版本号 %s -> %s" % (before, version)}
            await asyncio.sleep(interval)
            continue

        if action == "wait":
            if obs.get("item"):
                return {"ok": True, "name": name, "matched": obs["item"].get("text")}
            last = "未出现: " + "、".join(step.get("expect") or []) if step.get("expect") else "未匹配到目标"
            await asyncio.sleep(interval)
            continue

        if action in ("click_text", "click_template", "click"):
            if obs.get("item"):
                await asyncio.to_thread(vision.focus_app_window, app)
                offset = tuple(obs["shot"].get("offset") or (0, 0))
                ok = await asyncio.to_thread(
                    vision.click_at,
                    obs["item"].get("cx") or 0,
                    obs["item"].get("cy") or 0,
                    offset,
                )
                return {
                    "ok": bool(ok),
                    "name": name,
                    "matched": obs["item"].get("text"),
                    "message": "已点击 %s" % (obs["item"].get("text") or ""),
                }
            last = "未找到可点击目标"
            await asyncio.sleep(interval)
            continue

        if action == "key":
            ok = await asyncio.to_thread(vision.press_key, str(step.get("key") or "Escape"))
            await asyncio.sleep(0.5)
            return {"ok": ok, "name": name, "message": "已按键 %s" % (step.get("key") or "")}

        last = "未知动作: %s" % action
        break

    return {"ok": False, "name": name, "message": last or "超时"}


async def run_flow(
    target: str,
    save_dir: str,
    launcher: Optional[Callable[..., Any]] = None,
    custom: Any = None,
    on_event: Optional[Callable[[str, str], None]] = None,
    stop_flag: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """执行一条更新流程；任一步失败中止（除非 on_fail=continue）"""
    steps = load_flow(target, custom)
    if not steps:
        return {"success": False, "target": target, "message": "步骤定义为空", "steps": []}

    ctx: Dict[str, Any] = {"launcher": launcher, "version_before": "", "stop": stop_flag, "app": target}
    records: List[Dict[str, Any]] = []

    def emit(message: str, level: str = "info") -> None:
        if callable(on_event):
            try:
                on_event(level, message)
            except Exception:
                pass
        logger.info(message)

    emit("[更新流程:%s] 开始，共 %d 步" % (target, len(steps)))
    for index, step in enumerate(steps, 1):
        if stop_flag and stop_flag():
            return {"success": False, "target": target, "message": "已取消", "steps": records}
        name = step.get("name") or step.get("action") or ("步骤 %d" % index)
        emit("[更新流程:%s] 第 %d/%d 步：%s" % (target, index, len(steps), name))
        record = await _run_step(step, save_dir, ctx)
        record["index"] = index
        records.append(record)

        if record.get("finish"):
            emit("[更新流程:%s] 已完成（提前结束）：%s" % (target, name))
            return {"success": True, "target": target, "message": "流程完成", "steps": records, "finished_early": True}
        if not record.get("ok"):
            on_fail = str(step.get("on_fail") or "abort").lower()
            if on_fail == "continue" or step.get("optional"):
                emit("[更新流程:%s] 第 %d 步失败但继续：%s" % (target, index, record.get("message") or ""), "warning")
                continue
            emit("[更新流程:%s] 第 %d 步失败，中止：%s" % (target, index, record.get("message") or ""), "error")
            return {"success": False, "target": target, "message": record.get("message") or "流程中断", "steps": records}

    emit("[更新流程:%s] 全部步骤完成" % target)
    return {"success": True, "target": target, "message": "流程完成", "steps": records}
