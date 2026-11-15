"""
Playwright 浏览器自动化工具（增强版）

提供完整、安全的浏览器自动化能力：
- 网页导航：打开 / 后退 / 前进 / 刷新 / 多标签页
- 元素定位与交互：元素检索(finder)、单击 / 双击 / 悬停 / 按键 / 选择下拉 / 勾选
- 表单填写：按选择器或 name 批量填充、清空、提交
- 页面截图：整页 / 视口 / 指定元素
- 内容提取：文本 / HTML / 属性 / 结构化提取(extract) / JS 执行
- 多浏览器引擎：Chrome(Chromium) / Firefox / WebKit，可由配置切换
- 安全性：URL scheme 校验、危险协议拦截、JS 执行开关、内容长度限制、敏感参数脱敏
- 性能：实例复用、惰性启动、内容截断
- 资源释放：手动关闭 + 空闲自动回收
- 操作日志：内存环形缓冲 + 可选文件落盘

需要安装 Playwright: pip install playwright && playwright install chromium firefox
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from typing import Dict, Optional, Any, List

logger = logging.getLogger("ZhiYanWeb.Computer.BrowserOps")

# Playwright 是否可用
_HAS_PLAYWRIGHT = False
try:
    from playwright.async_api import async_playwright as _async_playwright
    _HAS_PLAYWRIGHT = True
except ImportError:
    pass

# 全局浏览器实例（单例模式，跨后续工具共享）
_playwright: Optional[Any] = None
_browser: Optional[Any] = None
_browser_context: Optional[Any] = None
_current_page: Optional[Any] = None
_browser_lock = asyncio.Lock()
_last_used_ts: float = 0.0
_idle_task: Optional[asyncio.Task] = None

# ──── 操作日志（内存环形缓冲 + 可选文件） ────
_OP_LOG: List[Dict] = []
_OP_LOG_MAX = 200

# 敏感参数名，记录日志时脱敏，保护用户隐私
_SENSITIVE_KEYS = ("password", "secret", "token", "authorization", "cookie", "api_key", "apikey")

# 操作名统一前缀
_PREFIX = "browser_"


# ════════════════════════════════════════════════════
# 配置读取
# ════════════════════════════════════════════════════

def _get_cfg() -> Dict:
    """读取浏览器配置（来自 settings.json 的 browser 块），带默认值兜底"""
    s = {}
    try:
        from zhiyan_web.config.settings import get_settings, DATA_DIR
        s = get_settings().get("browser", {}) or {}
    except Exception:
        from zhiyan_web.config.settings import DATA_DIR
    log_dir = os.path.join(DATA_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_enabled = bool(s.get("operation_log_enabled", True))
    return {
        "engine": str(s.get("browser_type", "chromium")).lower(),   # chromium / firefox / webkit
        "headless": bool(s.get("headless", True)),
        "use_system_browser": bool(s.get("use_system_browser", True)),
        "executable_path": str(s.get("executable_path", "") or ""),
        "viewport_width": int(s.get("viewport_width", 1280)),
        "viewport_height": int(s.get("viewport_height", 720)),
        "default_timeout": int(s.get("default_timeout", 30)),
        "max_idle_seconds": float(s.get("max_idle_seconds", 600)),
        "max_content_chars": int(s.get("max_content_chars", 5000)),
        "allow_js_eval": bool(s.get("allow_js_eval", True)),
        "log_enabled": log_enabled,
        "log_file": os.path.join(log_dir, "browser_ops.log") if log_enabled else "",
    }


def _extract_exe_from_cmd(cmd: str) -> Optional[str]:
    """从 Windows 关联的命令行提取可执行文件路径（处理引号与参数）"""
    if not cmd:
        return None
    cmd = cmd.strip()
    # 优先取双引号包裹的段
    if '"' in cmd:
        seg = cmd.split('"')
        for i in range(1, len(seg), 2):
            p = seg[i].strip()
            if p and os.path.sep in p and ("." in os.path.basename(p)):
                return p
    # 否则取第一个空格分隔的段
    first = cmd.split()[0].strip('"')
    return first if os.path.sep in first else None


def _win_default_browser() -> Optional[Dict]:
    """探测 Windows 系统默认浏览器，返回 {"engine","executable_path"}，找不到返回 None"""
    try:
        import winreg
    except ImportError:
        return None

    def _exe_for_progid(progid):
        for root in (winreg.HKEY_CLASSES_ROOT, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(root, rf"{progid}\shell\open\command") as k:
                    cmd, _ = winreg.QueryValueEx(k, None)
                exe = _extract_exe_from_cmd(cmd)
                if exe and os.path.isfile(exe):
                    return exe
            except OSError:
                continue
        return None

    for keyname in (
        r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice",
        r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice",
    ):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, keyname) as k:
                progid, _ = winreg.QueryValueEx(k, "Progid")
        except OSError:
            continue
        if not progid:
            continue
        exe = _exe_for_progid(progid)
        if exe and os.path.isfile(exe):
            return _mapping_for_exe(exe)

    # 兜底：遍历已安装浏览器（StartMenuInternet）
    try:
        for hive, root_key in ((winreg.HKEY_CURRENT_USER, "Software\\Clients\\StartMenuInternet"),
                               (winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\Clients\\StartMenuInternet")):
            try:
                with winreg.OpenKey(hive, root_key) as base:
                    i = 0
                    while True:
                        try:
                            name = winreg.EnumKey(base, i)
                        except OSError:
                            break
                        i += 1
                        try:
                            with winreg.OpenKey(base, f"{name}\\shell\\open\\command") as k:
                                cmd, _ = winreg.QueryValueEx(k, None)
                        except OSError:
                            continue
                        exe = _extract_exe_from_cmd(cmd)
                        if exe and os.path.isfile(exe):
                            return _mapping_for_exe(exe)
            except OSError:
                continue
    except Exception:
        pass
    return None


def _mapping_for_exe(exe: str) -> Dict:
    """根据可执行文件名推断合适的 Playwright 引擎与启动参数"""
    base = os.path.basename(exe).lower()
    if "firefox" in base:
        return {"engine": "firefox", "executable_path": exe}
    # chrome/edge/brave/opera/vivaldi 等均为 Chromium 内核，统一用 chromium 引擎驱动
    return {"engine": "chromium", "executable_path": exe}


def _detect_system_browser() -> Dict:
    """探测操作系统默认浏览器，返回可传给 launch 的参数与匹配引擎。

    返回结构: {"engine": "chromium"|"firefox"|..., "executable_path": "..."}
    找不到默认浏览器时退回检测已安装的 Chrome/Edge/Chromium/Firefox。
    """
    import shutil

    # Windows：真实默认浏览器（注册表）
    if os.name == "nt":
        win_res = _win_default_browser()
        if win_res:
            return win_res

    candidates = []  # (engine, exe)

    if os.name == "nt":
        wins = os.environ.get("ProgramFiles", "C:\\Program Files")
        wins_x86 = os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")
        for pth in (
            os.path.join(wins_x86, "Microsoft\\Edge\\Application\\msedge.exe"),
            os.path.join(wins, "Google\\Chrome\\Application\\chrome.exe"),
            os.path.join(wins_x86, "Google\\Chrome\\Application\\chrome.exe"),
            os.path.join(wins, "Mozilla Firefox\\firefox.exe"),
        ):
            if os.path.isfile(pth):
                candidates.append((_mapping_for_exe(pth)["engine"], pth))
    elif sys.platform == "darwin":
        for pth in (
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Firefox.app/Contents/MacOS/Firefox",
        ):
            if os.path.isfile(pth):
                candidates.append((_mapping_for_exe(pth)["engine"], pth))
    else:  # Linux：优先走 xdg 默认浏览器
        try:
            xdg = shutil.which("xdg-settings")
            if xdg:
                out = subprocess.run(
                    [xdg, "get", "default-web-browser"],
                    capture_output=True, text=True, timeout=5,
                ).stdout.strip()
                if out:
                    desktop = os.path.join(os.path.expanduser("~"), ".local", "share", "applications", out)
                    if os.path.isfile(desktop):
                        exec_line = None
                        with open(desktop, encoding="utf-8", errors="replace") as f:
                            for line in f:
                                if line.lower().startswith("exec="):
                                    exec_line = line[5:].strip()
                                    break
                        if exec_line:
                            exe = exec_line.split(" ", 1)[0].strip('"')
                            if exe and os.path.exists(exe):
                                return _mapping_for_exe(exe)
        except Exception:
            pass
        for name in ("google-chrome", "google-chrome-stable", "chromium",
                     "chromium-browser", "microsoft-edge", "firefox"):
            p = shutil.which(name)
            if p:
                candidates.append((_mapping_for_exe(p)["engine"], p))
                break

    for engine, exe in candidates:
        if os.path.isfile(exe):
            return {"engine": engine, "executable_path": exe}
    return {}


def _log(message: str):
    """写操作日志到文件"""
    if not _get_cfg()["log_file"]:
        return
    try:
        with open(_get_cfg()["log_file"], "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat()}] {message}\n")
    except Exception:
        pass


def _record_op(op: str, params: Dict, success: bool, msg: str = "", duration_ms: float = 0.0):
    """记录操作日志（内存 + 文件）"""
    import copy
    p = copy.deepcopy(params or {})
    for k in list(p.keys()):
        try:
            if any(sen in k.lower() for sen in _SENSITIVE_KEYS) or k in ("script", "content"):
                p[k] = "***"
        except Exception:
            pass
    entry = {
        "ts": datetime.now().isoformat(),
        "op": op,
        "params": p,
        "success": success,
        "msg": str(msg),
        "duration_ms": round(duration_ms, 1),
    }
    _OP_LOG.append(entry)
    if len(_OP_LOG) > _OP_LOG_MAX:
        _OP_LOG.pop(0)
    _log(f"{'OK' if success else 'FAIL'} {op} {json.dumps(p, ensure_ascii=False)} ({duration_ms:.0f}ms) {msg}")


# ════════════════════════════════════════════════════
# 安全校验
# ════════════════════════════════════════════════════

_ALLOWED_SCHEMES = ("http", "https", "file")


def _validate_url(url: str) -> str:
    """校验并规范化 URL，拦截危险协议，保护隐私安全"""
    url = (url or "").strip()
    if not url:
        raise ValueError("URL 不能为空")
    if not url.startswith(("http://", "https://", "file://")):
        url = "https://" + url
    scheme = url.split(":", 1)[0].lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise PermissionError(f"不允许访问该协议: {scheme}（仅支持 http/https/file）")
    if "chrome://" in url.lower() or "about:" in url.lower() or "javascript:" in url.lower():
        raise PermissionError("不允许访问内部/危险 URL")
    return url


def _redact_params(params: Dict) -> Dict:
    """脱敏参数（供日志/返回展示），保护隐私"""
    import copy
    p = copy.deepcopy(params or {})
    for k in list(p.keys()):
        try:
            if any(sen in k.lower() for sen in _SENSITIVE_KEYS) or k in ("script", "content", "base64_full"):
                p[k] = "***"
        except Exception:
            pass
    return p


# ════════════════════════════════════════════════════
# 浏览器生命周期
# ════════════════════════════════════════════════════

def _touch():
    """刷新最后活跃时间戳（用于空闲回收判断）"""
    global _last_used_ts
    _last_used_ts = time.time()


async def _ensure_browser() -> None:
    """确保浏览器实例已启动（惰性启动，实例复用）"""
    global _playwright, _browser, _browser_context, _current_page, _idle_task

    if not _HAS_PLAYWRIGHT:
        raise RuntimeError(
            "Playwright 未安装。请执行: pip install playwright && playwright install chromium firefox"
        )
    if _browser is not None:
        return

    async with _browser_lock:
        if _browser is not None:
            return
        cfg = _get_cfg()
        engine = cfg["engine"] if cfg["engine"] in ("chromium", "firefox", "webkit") else "chromium"
        try:
            _playwright = await _async_playwright().start()

            # 启动目标：优先使用系统默认浏览器（use_system_browser=true 或显式 executable_path）
            sys_target = {}   # 传给 launch 的参数
            sys_engine = None # 若探测到默认浏览器，可能覆盖引擎
            if cfg.get("use_system_browser"):
                detected = _detect_system_browser()
                if detected.get("executable_path"):
                    sys_target = {"executable_path": detected["executable_path"]}
                    sys_engine = detected.get("engine")
            if cfg.get("executable_path"):
                sys_target = {"executable_path": cfg["executable_path"]}
                sys_engine = _mapping_for_exe(cfg["executable_path"]).get("engine")
            if sys_engine and sys_engine in ("chromium", "firefox", "webkit"):
                engine = sys_engine

            browser_type = getattr(_playwright, engine)
            launch_kwargs = {
                "headless": cfg["headless"],
                "args": [
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            }
            if sys_target:
                launch_kwargs.update(sys_target)

            _browser = await browser_type.launch(**launch_kwargs)
            _browser_context = await _browser.new_context(
                viewport={"width": cfg["viewport_width"], "height": cfg["viewport_height"]},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            _current_page = await _browser_context.new_page()
            cfg2 = _get_cfg()
            _current_page.set_default_timeout(cfg2["default_timeout"] * 1000)
            _touch()

            # 启动空闲自动回收任务
            _idle_task = asyncio.create_task(_idle_auto_close())
            logger.info(f"Playwright 浏览器已启动 (engine={engine}, headless={cfg['headless']})")
            _log(f"启动浏览器 engine={engine} headless={cfg['headless']}")
        except Exception as e:
            await _close_browser_internal()
            logger.error(f"启动 Playwright 浏览器失败: {e}")
            raise RuntimeError(f"启动浏览器失败: {e}")


async def _close_browser_internal() -> None:
    """内部关闭浏览器（不抛异常）"""
    global _playwright, _browser, _browser_context, _current_page
    try:
        if _current_page is not None:
            try:
                await _current_page.close()
            except Exception:
                pass
        if _browser_context is not None:
            try:
                await _browser_context.close()
            except Exception:
                pass
        if _browser is not None:
            try:
                await _browser.close()
            except Exception:
                pass
        if _playwright is not None:
            try:
                await _playwright.stop()
            except Exception:
                pass
    finally:
        _playwright = None
        _browser = None
        _browser_context = None
        _current_page = None


async def _idle_auto_close() -> None:
    """空闲自动回收：长时间未操作则释放浏览器资源"""
    try:
        while True:
            await asyncio.sleep(30)
            cfg = _get_cfg()
            idle = cfg.get("max_idle_seconds", 600)
            if idle <= 0:
                continue
            if _browser is not None and (time.time() - _last_used_ts) > idle:
                logger.info(f"浏览器空闲超过 {idle:.0f}s，自动关闭释放资源")
                _log("空闲超时，自动关闭浏览器")
                await _close_browser_internal()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.debug(f"空闲回收任务异常: {e}")


async def browser_close() -> Dict:
    """手动关闭浏览器实例，释放资源"""
    global _idle_task
    try:
        if _idle_task is not None:
            _idle_task.cancel()
            _idle_task = None
        await _close_browser_internal()
        logger.info("浏览器已关闭")
        _record_op("browser_close", {}, True, "浏览器已关闭")
        return {"success": True, "message": "浏览器已关闭"}
    except Exception as e:
        _record_op("browser_close", {}, False, str(e))
        logger.error(f"关闭浏览器失败: {e}")
        return {"success": False, "error": str(e)}


# ════════════════════════════════════════════════════
# 导航操作
# ════════════════════════════════════════════════════

async def browser_navigate(url: str, timeout: int = None, wait_until: str = "domcontentloaded") -> Dict:
    """在浏览器中导航到指定 URL"""
    start = time.time()
    try:
        await _ensure_browser()
        global _current_page
        url = _validate_url(url)
        cfg = _get_cfg()
        t = timeout or cfg["default_timeout"]
        if wait_until not in ("load", "domcontentloaded", "networkidle", "commit"):
            wait_until = "domcontentloaded"
        _touch()
        await _current_page.goto(url, timeout=t * 1000, wait_until=wait_until)
        title = await _current_page.title()
        current_url = _current_page.url
        content = await _current_page.content()
        result = {
            "success": True,
            "title": title,
            "url": current_url,
            "content_length": len(content),
        }
        _record_op("browser_navigate", {"url": url, "timeout": t}, True, f"title={title}", (time.time() - start) * 1000)
        return result
    except Exception as e:
        _record_op("browser_navigate", {"url": url}, False, str(e), (time.time() - start) * 1000)
        logger.error(f"浏览器导航失败: {e}")
        return {"success": False, "error": str(e)}


async def browser_back(timeout: int = 30) -> Dict:
    start = time.time()
    try:
        await _ensure_browser()
        await _current_page.go_back(timeout=timeout * 1000)
        _touch()
        _record_op("browser_back", {}, True, f"url={_current_page.url}", (time.time() - start) * 1000)
        return {"success": True, "url": _current_page.url}
    except Exception as e:
        _record_op("browser_back", {}, False, str(e), (time.time() - start) * 1000)
        return {"success": False, "error": str(e)}


async def browser_forward(timeout: int = 30) -> Dict:
    start = time.time()
    try:
        await _ensure_browser()
        await _current_page.go_forward(timeout=timeout * 1000)
        _touch()
        _record_op("browser_forward", {}, True, f"url={_current_page.url}", (time.time() - start) * 1000)
        return {"success": True, "url": _current_page.url}
    except Exception as e:
        _record_op("browser_forward", {}, False, str(e), (time.time() - start) * 1000)
        return {"success": False, "error": str(e)}


async def browser_reload(timeout: int = 30) -> Dict:
    start = time.time()
    try:
        await _ensure_browser()
        await _current_page.reload(timeout=timeout * 1000)
        _touch()
        _record_op("browser_reload", {}, True, f"url={_current_page.url}", (time.time() - start) * 1000)
        return {"success": True, "url": _current_page.url}
    except Exception as e:
        _record_op("browser_reload", {}, False, str(e), (time.time() - start) * 1000)
        return {"success": False, "error": str(e)}


# ════════════════════════════════════════════════════
# 元素定位
# ════════════════════════════════════════════════════

_FIND_JS = """
(max) => {
  const all = Array.from(document.querySelectorAll(
    'a,button,input,select,textarea,[role="button"],[role="link"],[role="checkbox"],' +
    '[role="menuitem"],[onclick],label,li,span[class*="button"]'
  ));
  all.forEach(el => el.removeAttribute('data-zhiyan-i'));
  let n = 0;
  const out = [];
  for (const el of all) {
    if (n >= max) break;
    el.setAttribute('data-zhiyan-i', String(n));
    let text = (el.innerText || el.value || '').trim();
    if (text.length > 60) text = text.slice(0, 60);
    out.push({
      i: n,
      tag: el.tagName.toLowerCase(),
      type: el.type || '',
      name: el.name || '',
      id: el.id || '',
      placeholder: el.placeholder || '',
      text: text,
      title: el.title || '',
      href: (el.tagName === 'A') ? (el.href || el.getAttribute('href') || '') : ''
    });
    n++;
  }
  return { count: n, items: out };
}
"""


async def browser_find(selector: str = "", max_elements: int = 50) -> Dict:
    """检索页面上可交互的元素，返回带可点击 selector 的清单"""
    start = time.time()
    try:
        await _ensure_browser()
        cfg = _get_cfg()
        limit = max(1, min(int(max_elements or 50), 200))
        _touch()
        data = await _current_page.evaluate(_FIND_JS, limit)
        items = []
        for it in data["items"]:
            if selector and (selector.lower() not in (it["id"] + it["name"] + it["tag"] + it["placeholder"] + it["text"] + it["href"]).lower()):
                continue
            it["selector"] = f'[data-zhiyan-i="{it["i"]}"]'
            items.append(it)
        result = {
            "success": True,
            "count": len(items),
            "items": items[:cfg["max_content_chars"] // 300 + 10],
        }
        _record_op("browser_find", {"selector": selector, "max_elements": limit}, True, f"found={data['count']}", (time.time() - start) * 1000)
        return result
    except Exception as e:
        _record_op("browser_find", {"selector": selector}, False, str(e), (time.time() - start) * 1000)
        return {"success": False, "error": str(e)}


async def _locator(selector: str, timeout: int = None):
    """封装常用定位 + 等待逻辑"""
    await _ensure_browser()
    if not selector:
        raise ValueError("selector 不能为空")
    cfg = _get_cfg()
    t = timeout or cfg["default_timeout"]
    _touch()
    await _current_page.wait_for_selector(selector, timeout=t * 1000)
    return _current_page.locator(selector)


# ════════════════════════════════════════════════════
# 元素交互
# ════════════════════════════════════════════════════

async def browser_click(selector: str, timeout: int = 10) -> Dict:
    start = time.time()
    try:
        loc = await _locator(selector, timeout)
        await loc.click()
        await asyncio.sleep(0.3)
        _record_op("browser_click", {"selector": selector}, True, "", (time.time() - start) * 1000)
        return {"success": True, "message": f"已点击元素: {selector}"}
    except Exception as e:
        _record_op("browser_click", {"selector": selector}, False, str(e), (time.time() - start) * 1000)
        return {"success": False, "error": str(e)}


async def browser_dblclick(selector: str, timeout: int = 10) -> Dict:
    start = time.time()
    try:
        loc = await _locator(selector, timeout)
        await loc.dblclick()
        await asyncio.sleep(0.3)
        _record_op("browser_dblclick", {"selector": selector}, True, "", (time.time() - start) * 1000)
        return {"success": True, "message": f"已双击元素: {selector}"}
    except Exception as e:
        _record_op("browser_dblclick", {"selector": selector}, False, str(e), (time.time() - start) * 1000)
        return {"success": False, "error": str(e)}


async def browser_hover(selector: str, timeout: int = 10) -> Dict:
    start = time.time()
    try:
        loc = await _locator(selector, timeout)
        await loc.hover()
        _record_op("browser_hover", {"selector": selector}, True, "", (time.time() - start) * 1000)
        return {"success": True, "message": f"已悬停元素: {selector}"}
    except Exception as e:
        _record_op("browser_hover", {"selector": selector}, False, str(e), (time.time() - start) * 1000)
        return {"success": False, "error": str(e)}


async def browser_type(selector: str, text: str, clear: bool = True, timeout: int = 10) -> Dict:
    """在输入框中输入文字（默认先清空）"""
    start = time.time()
    try:
        loc = await _locator(selector, timeout)
        if clear:
            await loc.fill("")
        await loc.fill(text)
        _record_op("browser_type", {"selector": selector, "text_length": len(text)}, True, "", (time.time() - start) * 1000)
        return {"success": True, "message": f"已在 '{selector}' 输入 {len(text)} 字符"}
    except Exception as e:
        _record_op("browser_type", {"selector": selector}, False, str(e), (time.time() - start) * 1000)
        return {"success": False, "error": str(e)}


async def browser_press_key(key: str) -> Dict:
    """在页面上按下键盘按键（如 Enter, Tab, Escape, ArrowDown）"""
    start = time.time()
    try:
        await _ensure_browser()
        await _current_page.keyboard.press(key)
        _touch()
        _record_op("browser_press_key", {"key": key}, True, "", (time.time() - start) * 1000)
        return {"success": True, "message": f"已按键: {key}"}
    except Exception as e:
        _record_op("browser_press_key", {"key": key}, False, str(e), (time.time() - start) * 1000)
        return {"success": False, "error": str(e)}


async def browser_select_option(selector: str, value: str, timeout: int = 10) -> Dict:
    """在下拉框 / 单选按钮中选择选项"""
    start = time.time()
    try:
        loc = await _locator(selector, timeout)
        await loc.select_option(value)
        _record_op("browser_select_option", {"selector": selector, "value": value}, True, "", (time.time() - start) * 1000)
        return {"success": True, "message": f"已选择: {value}"}
    except Exception as e:
        _record_op("browser_select_option", {"selector": selector, "value": value}, False, str(e), (time.time() - start) * 1000)
        return {"success": False, "error": str(e)}


async def browser_check(selector: str, checked: bool = True, timeout: int = 10) -> Dict:
    """勾选 / 取消勾选复选框"""
    start = time.time()
    try:
        loc = await _locator(selector, timeout)
        if checked:
            await loc.check()
        else:
            await loc.uncheck()
        _record_op("browser_check", {"selector": selector, "checked": checked}, True, "", (time.time() - start) * 1000)
        return {"success": True, "message": f"已{'勾选' if checked else '取消勾选'}: {selector}"}
    except Exception as e:
        _record_op("browser_check", {"selector": selector}, False, str(e), (time.time() - start) * 1000)
        return {"success": False, "error": str(e)}


async def browser_fill_form(fields: List[Dict], timeout: int = 10) -> Dict:
    """批量填写表单。

    fields 为列表，每项可为:
      {"selector": "...", "value": "..."}
      {"name": "username", "value": "admin"}   # 按 name 定位
    """
    start = time.time()
    try:
        await _ensure_browser()
        cfg = _get_cfg()
        t = timeout or cfg["default_timeout"]
        if not fields:
            return {"success": False, "error": "fields 不能为空"}
        filled = []
        for f in fields:
            value = f.get("value", "")
            if f.get("selector"):
                sel = f["selector"]
            elif f.get("name"):
                sel = f"input[name=\"{f['name']}\"], textarea[name=\"{f['name']}\"]"
            else:
                continue
            await _current_page.wait_for_selector(sel, timeout=t * 1000)
            loc = _current_page.locator(sel)
            await loc.fill("")
            await loc.fill(str(value))
            filled.append({"field": sel, "chars": len(str(value))})
        _record_op("browser_fill_form", {"count": len(filled), "fields": _redact_params({str(i + 1): f.get('name') or f.get('selector', '') for i, f in enumerate(fields)})}, True, "", (time.time() - start) * 1000)
        return {"success": True, "filled": filled, "message": f"已填写 {len(filled)} 个字段"}
    except Exception as e:
        _record_op("browser_fill_form", {}, False, str(e), (time.time() - start) * 1000)
        return {"success": False, "error": str(e)}


async def browser_submit(timeout: int = 10) -> Dict:
    """提交当前页面第一个可见表单"""
    start = time.time()
    try:
        await _ensure_browser()
        _touch()
        submitted = await _current_page.evaluate("""
            () => {
                const forms = Array.from(document.querySelectorAll('form'));
                if (!forms.length) return false;
                const btn = forms[0].querySelector('button[type="submit"], input[type="submit"]');
                if (btn) { btn.click(); return true; }
                forms[0].requestSubmit();
                return true;
            }
        """)
        await asyncio.sleep(0.5)
        _record_op("browser_submit", {}, True, "", (time.time() - start) * 1000)
        return {"success": True, "submitted": submitted, "message": "已提交表单"}
    except Exception as e:
        _record_op("browser_submit", {}, False, str(e), (time.time() - start) * 1000)
        return {"success": False, "error": str(e)}


# ════════════════════════════════════════════════════
# 页面滚动
# ════════════════════════════════════════════════════

async def browser_scroll(amount: int = 0, to: str = "down", selector: str = "") -> Dict:
    """滚动页面。

    to: down/up/bottom/top；amount: 像素；selector: 指定滚动容器。
    """
    start = time.time()
    try:
        await _ensure_browser()
        _touch()
        base = f'Array.from(document.querySelectorAll({json.dumps(selector)})).length' if selector else 'true'
        js = f"""
            () => {{
                const root = {base} ? Array.from(document.querySelectorAll({json.dumps(selector)}))[0] || document.scrollingElement
                                   : document.scrollingElement;
                const el = root || document.scrollingElement;
                const to = {json.dumps(to)};
                const amount = {int(amount)};
                if (to === 'bottom') el.scrollTop = el.scrollHeight;
                else if (to === 'top') el.scrollTop = 0;
                else if (to === 'up') el.scrollTop -= (amount || window.innerHeight || 400);
                else el.scrollTop += (amount || window.innerHeight || 400);
                return {{ y: el.scrollTop, height: el.scrollHeight, viewport: window.innerHeight }};
            }}
        """
        res = await _current_page.evaluate(js)
        _record_op("browser_scroll", {"to": to, "amount": amount}, True, f"y={res['y']}", (time.time() - start) * 1000)
        return {"success": True, **res}
    except Exception as e:
        _record_op("browser_scroll", {"to": to}, False, str(e), (time.time() - start) * 1000)
        return {"success": False, "error": str(e)}


# ════════════════════════════════════════════════════
# 内容提取
# ════════════════════════════════════════════════════

async def browser_get_text(selector: str = "body", timeout: int = 10) -> Dict:
    """获取页面元素的文本内容"""
    start = time.time()
    try:
        loc = await _locator(selector, timeout)
        text = (await loc.inner_text()).strip()
        cfg = _get_cfg()
        truncated = text[:cfg["max_content_chars"]]
        _record_op("browser_get_text", {"selector": selector}, True, f"chars={len(text)}", (time.time() - start) * 1000)
        return {"success": True, "text": truncated, "length": len(truncated), "truncated": len(truncated) < len(text)}
    except Exception as e:
        _record_op("browser_get_text", {"selector": selector}, False, str(e), (time.time() - start) * 1000)
        return {"success": False, "error": str(e)}


async def browser_get_html(selector: str = "html", timeout: int = 10) -> Dict:
    """获取页面 HTML 内容"""
    start = time.time()
    try:
        loc = await _locator(selector, timeout)
        html = await loc.inner_html()
        cfg = _get_cfg()
        truncated = html[:cfg["max_content_chars"]]
        _record_op("browser_get_html", {"selector": selector}, True, f"chars={len(html)}", (time.time() - start) * 1000)
        return {"success": True, "html": truncated, "length": len(truncated), "truncated": len(truncated) < len(html)}
    except Exception as e:
        _record_op("browser_get_html", {"selector": selector}, False, str(e), (time.time() - start) * 1000)
        return {"success": False, "error": str(e)}


async def browser_get_attribute(selector: str, attribute: str, timeout: int = 10) -> Dict:
    """获取元素的属性值"""
    start = time.time()
    try:
        loc = await _locator(selector, timeout)
        val = await loc.get_attribute(attribute)
        _record_op("browser_get_attribute", {"selector": selector, "attribute": attribute}, True, "", (time.time() - start) * 1000)
        return {"success": True, "attribute": attribute, "value": val}
    except Exception as e:
        _record_op("browser_get_attribute", {"selector": selector, "attribute": attribute}, False, str(e), (time.time() - start) * 1000)
        return {"success": False, "error": str(e)}


async def browser_extract(include: List[str] = None) -> Dict:
    """结构化提取页面内容：文本、标题、链接、表单字段、表格、图片地址、meta"""
    start = time.time()
    try:
        await _ensure_browser()
        _touch()
        if not include:
            include = ["text", "links", "forms", "meta"]
        cfg = _get_cfg()
        maxc = cfg["max_content_chars"]
        parts = {}
        if "text" in include:
            parts["title"] = await _current_page.title()
            parts["url"] = _current_page.url
            parts["text"] = (await _current_page.locator("body").inner_text())[:maxc]
        if "links" in include:
            links = await _current_page.evaluate(
                "() => Array.from(document.querySelectorAll('a')).slice(0,80).filter(a=>a.href).map((a,i)=>({i, text:(a.innerText||'').trim().slice(0,40), href:a.href}))"
            )
            parts["links"] = links[:80]
        if "forms" in include:
            forms = await _current_page.evaluate("""
                () => Array.from(document.querySelectorAll('form')).map((f, fi) => ({
                    index: fi, action: f.action || '', method: f.method || 'get',
                    fields: Array.from(f.querySelectorAll('input,select,textarea,button')).map((el, i) => ({
                        i, tag: el.tagName.toLowerCase(), type: el.type || '', name: el.name || '', id: el.id || '',
                        placeholder: el.placeholder || '', value: (el.type && ['password','submit','button','hidden','file'].includes(el.type)) ? '' : (el.value || '').slice(0,40)
                    }))
                }))
            """)
            parts["forms"] = forms
        if "tables" in include:
            tables = await _current_page.evaluate("""
                () => Array.from(document.querySelectorAll('table')).slice(0,10).map((tb, ti) => ({
                    index: ti, caption: (tb.caption?.innerText || '').slice(0,60),
                    rows: Array.from(tb.querySelectorAll('tr')).slice(0,30).map(r => Array.from(r.querySelectorAll('th,td')).map(c => (c.innerText||'').trim().slice(0,50)))
                }))
            """)
            parts["tables"] = tables
        if "images" in include:
            imgs = await _current_page.evaluate(
                "() => Array.from(document.querySelectorAll('img[src]')).slice(0,40).map(img => ({alt: (img.alt||'').slice(0,40), src: img.src}))"
            )
            parts["images"] = imgs
        if "meta" in include:
            meta = await _current_page.evaluate(
                "() => Array.from(document.querySelectorAll('meta')).slice(0,40).map(m => ({name: m.getAttribute('name')||m.getAttribute('property')||'', content: (m.content||'').slice(0,120)})).filter(m=>m.name)"
            )
            parts["meta"] = meta
        _record_op("browser_extract", {"include": include}, True, "", (time.time() - start) * 1000)
        return {"success": True, **parts}
    except Exception as e:
        _record_op("browser_extract", {"include": include}, False, str(e), (time.time() - start) * 1000)
        return {"success": False, "error": str(e)}


async def browser_evaluate(script: str, timeout: int = 10) -> Dict:
    """在浏览器中执行 JavaScript（受配置开关限制，脚本不落日志）"""
    start = time.time()
    try:
        await _ensure_browser()
        cfg = _get_cfg()
        if not cfg.get("allow_js_eval", True):
            return {"success": False, "error": "JavaScript 执行已被管理员禁用（allow_js_eval=false）"}
        if not script or not script.strip():
            return {"success": False, "error": "script 不能为空"}
        _touch()
        result = await _current_page.evaluate(script, timeout=timeout * 1000)
        s = json.dumps(result, ensure_ascii=False, default=str)[:cfg["max_content_chars"]] if result is not None else ""
        _record_op("browser_evaluate", {"script": "***"}, True, f"result_type={type(result).__name__}", (time.time() - start) * 1000)
        return {"success": True, "result": s, "result_type": type(result).__name__}
    except Exception as e:
        _record_op("browser_evaluate", {"script": "***"}, False, str(e), (time.time() - start) * 1000)
        return {"success": False, "error": str(e)}


# ════════════════════════════════════════════════════
# 等待 / 标签页
# ════════════════════════════════════════════════════

async def browser_wait(selector: str = "", timeout: int = 10, state: str = "visible") -> Dict:
    """等待元素出现 / 页面加载完成"""
    start = time.time()
    try:
        await _ensure_browser()
        cfg = _get_cfg()
        t = timeout or cfg["default_timeout"]
        _touch()
        if selector:
            await _current_page.wait_for_selector(selector, timeout=t * 1000, state=state)
            msg = f"元素已{state}: {selector}"
        else:
            await _current_page.wait_for_load_state("networkidle", timeout=t * 1000)
            msg = "页面加载完成 (networkidle)"
        _record_op("browser_wait", {"selector": selector, "timeout": t}, True, msg, (time.time() - start) * 1000)
        return {"success": True, "message": msg}
    except Exception as e:
        _record_op("browser_wait", {"selector": selector}, False, str(e), (time.time() - start) * 1000)
        return {"success": False, "error": str(e)}


async def browser_open_tab(url: str = "", timeout: int = 30) -> Dict:
    """打开新标签页（可带 URL）"""
    start = time.time()
    try:
        await _ensure_browser()
        page = await _browser_context.new_page()
        _current_page = page
        cfg = _get_cfg()
        page.set_default_timeout(cfg["default_timeout"] * 1000)
        _touch()
        if url:
            url = _validate_url(url)
            await page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
        _record_op("browser_open_tab", {"url": url}, True, f"tabs={len(_browser_context.pages)}", (time.time() - start) * 1000)
        return {"success": True, "message": f"已打开新标签页，当前共 {len(_browser_context.pages)} 个", "tabs": len(_browser_context.pages), "url": page.url}
    except Exception as e:
        _record_op("browser_open_tab", {"url": url}, False, str(e), (time.time() - start) * 1000)
        return {"success": False, "error": str(e)}


async def browser_list_tabs() -> Dict:
    start = time.time()
    try:
        await _ensure_browser()
        tabs = []
        for i, p in enumerate(_browser_context.pages):
            try:
                title = await p.title()
            except Exception:
                title = ""
            tabs.append({"index": i, "url": p.url, "title": (title or "")[:80], "active": p == _current_page})
        _record_op("browser_list_tabs", {}, True, f"tabs={len(tabs)}", (time.time() - start) * 1000)
        return {"success": True, "tabs": tabs, "count": len(tabs)}
    except Exception as e:
        _record_op("browser_list_tabs", {}, False, str(e), (time.time() - start) * 1000)
        return {"success": False, "error": str(e)}


async def browser_switch_tab(index: int) -> Dict:
    """切换到指定索引的标签页"""
    start = time.time()
    try:
        await _ensure_browser()
        global _current_page
        pages = _browser_context.pages
        if not (0 <= int(index) < len(pages)):
            return {"success": False, "error": f"标签页索引 {index} 无效，当前共 {len(pages)} 个"}
        _current_page = pages[int(index)]
        await _current_page.bring_to_front()
        _touch()
        _record_op("browser_switch_tab", {"index": int(index)}, True, f"url={_current_page.url}", (time.time() - start) * 1000)
        return {"success": True, "url": _current_page.url}
    except Exception as e:
        _record_op("browser_switch_tab", {"index": index}, False, str(e), (time.time() - start) * 1000)
        return {"success": False, "error": str(e)}


async def browser_close_tab(index: int = -1) -> Dict:
    """关闭标签页（默认最后一个 / 当前页）"""
    start = time.time()
    try:
        await _ensure_browser()
        global _current_page
        pages = _browser_context.pages
        if not pages:
            return {"success": False, "error": "没有可关闭的标签页"}
        target = pages[int(index)] if 0 <= int(index) < len(pages) else pages[-1]
        await target.close()
        if pages or _browser_context.pages:
            _current_page = _browser_context.pages[-1]
        else:
            _current_page = await _browser_context.new_page()
        _touch()
        _record_op("browser_close_tab", {"index": int(index)}, True, f"剩余 {len(_browser_context.pages)} 个标签页", (time.time() - start) * 1000)
        return {"success": True, "tabs": len(_browser_context.pages)}
    except Exception as e:
        _record_op("browser_close_tab", {"index": index}, False, str(e), (time.time() - start) * 1000)
        return {"success": False, "error": str(e)}


# ════════════════════════════════════════════════════
# 截图
# ════════════════════════════════════════════════════

async def browser_screenshot(full_page: bool = False, selector: str = "") -> Dict:
    """截取当前浏览器页面截图。

    full_page: 是否整页截图；selector: 只截取指定元素区域。
    返回本地 PNG 路径。
    """
    start = time.time()
    try:
        await _ensure_browser()
        _touch()
        tmp_dir = os.path.join(tempfile.gettempdir(), "zhiyan_browser_shot")
        os.makedirs(tmp_dir, exist_ok=True)
        filename = f"browser_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        image_path = os.path.join(tmp_dir, filename)

        if selector:
            locator = _current_page.locator(selector)
            await locator.wait_for(state="visible", timeout=5000)
            await locator.screenshot(path=image_path)
        else:
            await _current_page.screenshot(path=image_path, full_page=full_page, type="png")

        size = os.path.getsize(image_path)
        viewport = _current_page.viewport_size
        result = {
            "success": True,
            "path": image_path,
            "size": size,
            "width": viewport.get("width", 1280) if viewport else 1280,
            "height": viewport.get("height", 720) if viewport else 720,
            "element": selector or "",
            "message": f"截图已保存: {image_path}",
        }
        _record_op("browser_screenshot", {"full_page": full_page, "selector": selector}, True, image_path, (time.time() - start) * 1000)
        return result
    except Exception as e:
        _record_op("browser_screenshot", {"selector": selector}, False, str(e), (time.time() - start) * 1000)
        logger.error(f"浏览器截图失败: {e}")
        return {"success": False, "error": str(e)}


# ════════════════════════════════════════════════════
# 状态 / 日志
# ════════════════════════════════════════════════════

async def get_browser_status() -> Dict:
    """获取浏览器状态"""
    cfg = _get_cfg()
    return {
        "success": True,
        "available": _HAS_PLAYWRIGHT,
        "running": _browser is not None,
        "has_page": _current_page is not None,
        "pages": len(_browser_context.pages) if _browser_context else 0,
        "current_url": _current_page.url if _current_page else "",
        "engine": cfg["engine"],
        "headless": cfg["headless"],
        "default_timeout": cfg["default_timeout"],
        "max_idle_seconds": cfg["max_idle_seconds"],
    }


async def get_browser_logs(limit: int = 50) -> Dict:
    """获取浏览器操作日志"""
    n = min(max(int(limit or 50), 1), _OP_LOG_MAX)
    return {"success": True, "count": len(_OP_LOG), "logs": list(reversed(_OP_LOG[-n:]))}


# ════════════════════════════════════════════════════
# 统一操作分发
# ════════════════════════════════════════════════════

_BROWSER_OPS = {
    "browser_navigate": browser_navigate,
    "browser_back": browser_back,
    "browser_forward": browser_forward,
    "browser_reload": browser_reload,
    "browser_open_tab": browser_open_tab,
    "browser_list_tabs": browser_list_tabs,
    "browser_switch_tab": browser_switch_tab,
    "browser_close_tab": browser_close_tab,
    "browser_find": browser_find,
    "browser_click": browser_click,
    "browser_dblclick": browser_dblclick,
    "browser_hover": browser_hover,
    "browser_type": browser_type,
    "browser_press_key": browser_press_key,
    "browser_select_option": browser_select_option,
    "browser_check": browser_check,
    "browser_fill_form": browser_fill_form,
    "browser_submit": browser_submit,
    "browser_scroll": browser_scroll,
    "browser_get_text": browser_get_text,
    "browser_get_html": browser_get_html,
    "browser_get_attribute": browser_get_attribute,
    "browser_extract": browser_extract,
    "browser_evaluate": browser_evaluate,
    "browser_wait": browser_wait,
    "browser_screenshot": browser_screenshot,
    "browser_close": browser_close,
}


async def browser_execute(op: str, **kwargs) -> Dict:
    """统一分发入口（供 LLM 工具与 control 面板复用）

    兼容两种传入形式：'navigate' 或 'browser_navigate'。
    同时也支持 status / logs 查询。
    """
    key = op if op.startswith(_PREFIX) else _PREFIX + op
    handler: Optional[Any]
    if key in ("browser_status", "browser_logs") or op in ("status", "logs"):
        handler = get_browser_status if (key or op) in ("browser_status", "status") else get_browser_logs
    else:
        handler = _BROWSER_OPS.get(key)
    if handler is None:
        return {"success": False, "error": f"未知浏览器操作: {op}"}
    return await handler(**kwargs)


def available_operations() -> List[str]:
    """返回所有可用浏览器操作名"""
    return list(_BROWSER_OPS.keys())