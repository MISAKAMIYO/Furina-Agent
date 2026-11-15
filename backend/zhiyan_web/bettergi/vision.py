"""
BetterGI 图像识别兜底与 AI 决策

能力：
- 截取原神窗口画面
- 本地 OCR 识别（优先新版 rapidocr，其次 rapidocr-onnxruntime）
- 按关键词规则点击（开始/确认/继续/领取等）
- 规则未命中时交给多模态大模型决策（失败再退化为纯文本 LLM）
- OCR 引擎缺失时可一键安装（源码运行环境）

说明：
- 打包版应内置 OCR 组件（build_exe.spec 已收集 rapidocr / onnxruntime）
- 所有动作都有次数与关键词限制，不会无限点击
"""
from __future__ import annotations

import base64
import json
import logging

import numpy as np
import io
import os
import shutil
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("ZhiYanWeb.bettergi.vision")

# 默认恢复规则：ocr 命中这些文字就点它（越靠前优先级越高）
DEFAULT_RULES: List[Dict[str, Any]] = [
    {"text": "开始挑战", "action": "click"},
    {"text": "点击继续", "action": "click"},
    {"text": "继续", "action": "click"},
    {"text": "确认", "action": "click"},
    {"text": "确定", "action": "click"},
    {"text": "开始", "action": "click"},
    {"text": "领取", "action": "click"},
    {"text": "传送", "action": "click"},
    {"text": "关闭", "action": "click"},
]

# 游戏窗口标题关键词（国服/国际服/云原神）
WINDOW_KEYWORDS = ("原神", "genshin", "yuanshen", "云·原神")

_ENGINE = None
_ENGINE_KIND = ""


def vendor_dir() -> str:
    """OCR 依赖的独立安装目录（避免污染 Python 环境/权限问题）"""
    try:
        from zhiyan_web.config import DATA_DIR
        return os.path.join(DATA_DIR, "py_deps", "ocr")
    except Exception:
        return os.path.join(os.getcwd(), "data", "py_deps", "ocr")


def _ensure_sys_path() -> None:
    """把独立安装目录加入 sys.path 与 DLL 搜索路径"""
    vendor = vendor_dir()
    if not os.path.isdir(vendor):
        return
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    if os.name == "nt":
        for sub in ("", "onnxruntime", os.path.join("onnxruntime", "capi"), "cv2", "shapely", "shapely.libs"):
            path = os.path.join(vendor, sub)
            if os.path.isdir(path):
                try:
                    os.add_dll_directory(path)
                except Exception:
                    pass


_ensure_sys_path()


# ============= OCR 引擎 =============

def _load_engine() -> Tuple[Any, str]:
    """加载 OCR 引擎，优先新版 rapidocr，其次 rapidocr-onnxruntime"""
    global _ENGINE, _ENGINE_KIND
    if _ENGINE is not None:
        return _ENGINE, _ENGINE_KIND
    try:
        from rapidocr import RapidOCR  # 新版（RapidOCR 3.x）
        _ENGINE = RapidOCR()
        _ENGINE_KIND = "rapidocr"
        logger.info("本地 OCR 引擎已加载: rapidocr")
        return _ENGINE, _ENGINE_KIND
    except Exception as e_new:
        logger.debug(f"新版 rapidocr 不可用: {e_new}")
    try:
        from rapidocr_onnxruntime import RapidOCR  # 旧版（Python < 3.13）
        _ENGINE = RapidOCR()
        _ENGINE_KIND = "rapidocr_onnxruntime"
        logger.info("本地 OCR 引擎已加载: rapidocr_onnxruntime")
        return _ENGINE, _ENGINE_KIND
    except Exception as e_old:
        logger.debug(f"rapidocr_onnxruntime 不可用: {e_old}")
    _ENGINE = None
    _ENGINE_KIND = ""
    return None, ""


def reset_engine() -> None:
    global _ENGINE, _ENGINE_KIND
    _ENGINE = None
    _ENGINE_KIND = ""


def engine_status() -> Dict[str, Any]:
    """OCR 引擎与图案模板可用性（会尝试加载一次）"""
    engine, kind = _load_engine()
    ready = engine is not None
    templates = list_templates()
    return {
        "available": ready,
        "kind": kind,
        "python": "%d.%d.%d" % sys.version_info[:3],
        "frozen": bool(getattr(sys, "frozen", False)),
        "opencv": _load_cv2() is not None,
        "templates": len(templates),
        "templates_dir": templates_dir(),
        "hint": "" if ready else "本地 OCR 未安装，可在页面点击「安装 OCR 引擎」",
    }


def _box_center(box: Any) -> Tuple[float, float]:
    """四点框 -> 中心点"""
    try:
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
        return sum(xs) / len(xs), sum(ys) / len(ys)
    except Exception:
        return 0.0, 0.0


def _items_from_result(kind: str, raw: Any) -> List[Dict[str, Any]]:
    """把两种引擎的返回结构统一成 items"""
    items: List[Dict[str, Any]] = []
    if raw is None:
        return items

    # 旧版返回 (result, elapse)，result 为 [[box, text, score], ...]
    if kind == "rapidocr_onnxruntime":
        result = raw[0] if isinstance(raw, tuple) else raw
        for entry in (result or []):
            try:
                box, text, score = entry[0], entry[1], entry[2]
            except Exception:
                continue
            cx, cy = _box_center(box)
            items.append({"text": str(text), "score": float(score or 0), "box": box, "cx": cx, "cy": cy})
        return items

    # 新版返回对象（txts/boxes/scores）
    txts = getattr(raw, "txts", None)
    boxes = getattr(raw, "boxes", None)
    scores = getattr(raw, "scores", None)
    if txts is None and isinstance(raw, dict):
        txts = raw.get("txts")
        boxes = raw.get("boxes")
        scores = raw.get("scores")
    if txts is None and isinstance(raw, tuple):
        return _items_from_result("rapidocr_onnxruntime", raw)
    for i, text in enumerate(txts or []):
        try:
            box = boxes[i] if boxes is not None else []
            score = scores[i] if scores is not None else 0
            cx, cy = _box_center(box)
            items.append({"text": str(text), "score": float(score or 0), "box": box, "cx": cx, "cy": cy})
        except Exception:
            continue
    return items


def _as_pil(image: Any):
    """把 path / bytes / PIL.Image / numpy 统一成 PIL.Image（不落盘）"""
    try:
        from PIL import Image
    except Exception:
        return None
    if image is None:
        return None
    if isinstance(image, Image.Image):
        return image
    if isinstance(image, (bytes, bytearray)):
        try:
            return Image.open(io.BytesIO(image))
        except Exception:
            return None
    if isinstance(image, str):
        try:
            return Image.open(image)
        except Exception:
            return None
    # numpy 数组
    try:
        return Image.fromarray(image)
    except Exception:
        return None


def _as_ndarray(image: Any):
    """把输入统一成 numpy RGB 数组（供 OCR / OpenCV 使用）"""
    pil = _as_pil(image)
    if pil is not None:
        try:
            return np.array(pil.convert("RGB"))
        except Exception:
            return None
    try:
        return image
    except Exception:
        return None


def recognize(image_path: Any) -> Dict[str, Any]:
    """对图片执行 OCR"""
    engine, kind = _load_engine()
    if engine is None:
        return {"success": False, "engine": "", "text": "", "items": [], "message": "本地 OCR 引擎未安装"}
    payload = _as_ndarray(image_path)
    if payload is None:
        return {"success": False, "engine": kind, "text": "", "items": [], "message": "图像为空或无法解析"}
    try:
        raw = engine(payload)
    except Exception as e:
        return {"success": False, "engine": kind, "text": "", "items": [], "message": f"OCR 识别失败: {e}"}
    items = _items_from_result(kind, raw)
    text = "\n".join(i["text"] for i in items)
    return {"success": True, "engine": kind, "text": text, "items": items, "message": ""}


# ============= 窗口与截图 =============

def find_game_window() -> Optional[Dict[str, Any]]:
    """查找原神窗口，返回 {hwnd, title, left, top, right, bottom}"""
    try:
        import win32gui
    except Exception:
        return None

    found: List[Dict[str, Any]] = []

    def _cb(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd) or ""
            lower = title.lower()
            if not any(k in lower for k in WINDOW_KEYWORDS):
                return
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            if right - left < 200 or bottom - top < 200:
                return
            found.append({"hwnd": hwnd, "title": title, "left": left, "top": top, "right": right, "bottom": bottom})
        except Exception:
            return

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:
        return None
    return found[0] if found else None


APP_WINDOW_KEYWORDS = {
    "game": WINDOW_KEYWORDS,
    "bgi": ("bettergi", "better gi", "更好的原神"),
    # BGI 更新时拉起的安装程序（独立窗口）
    "installer": ("安装", "setup", "install", "更好的原神"),
}


PROCESS_WINDOW_NAMES = {
    "bgi": ("bettergi.exe",),
    "game": ("yuanshen.exe", "genshinimpact.exe", "yuanshen", "genshinimpact"),
}


def _pid_of_process(names) -> List[int]:
    out: List[int] = []
    try:
        import psutil
    except Exception:
        return out
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if any(name == n or name.startswith(n.rsplit(".", 1)[0]) for n in names):
                out.append(int(proc.info.get("pid")))
        except Exception:
            continue
    return out


def _windows_of_pids(pids, need_visible: bool = False) -> List[Dict[str, Any]]:
    """枚举指定进程的所有顶层窗口（默认也包含最小化的）"""
    found: List[Dict[str, Any]] = []
    try:
        import win32gui
        import win32process
    except Exception:
        return found

    def _cb(hwnd, _):
        try:
            if need_visible and not win32gui.IsWindowVisible(hwnd):
                return
            _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid not in pids:
                return
            title = win32gui.GetWindowText(hwnd) or ""
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            if right - left < 200 or bottom - top < 200:
                return
            found.append({"hwnd": hwnd, "title": title, "left": left, "top": top,
                          "right": right, "bottom": bottom,
                          "visible": bool(win32gui.IsWindowVisible(hwnd))})
        except Exception:
            return

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:
        return found
    return found


def find_app_window(app: str = "game") -> Optional[Dict[str, Any]]:
    """按应用名查找可见窗口（game=原神，bgi=BetterGI）"""
    try:
        import win32gui
    except Exception:
        return None

    keywords = APP_WINDOW_KEYWORDS.get((app or "game").lower(), WINDOW_KEYWORDS)
    found: List[Dict[str, Any]] = []

    def _cb(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd) or ""
            lower = title.lower()
            if not any(k in lower for k in keywords):
                return
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            if right - left < 200 or bottom - top < 200:
                return
            found.append({"hwnd": hwnd, "title": title, "left": left, "top": top,
                          "right": right, "bottom": bottom})
        except Exception:
            return

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:
        return None
    if not found:
        names = PROCESS_WINDOW_NAMES.get((app or "game").lower())
        if names:
            pids = _pid_of_process(names)
            if pids:
                found = _windows_of_pids(set(pids))
    if not found:
        return None
    # 优先取面积最大的窗口（主窗口），避免抓到小提示框
    found.sort(key=lambda w: (w["right"] - w["left"]) * (w["bottom"] - w["top"]), reverse=True)
    return found[0]


def focus_app_window(app: str = "game") -> bool:
    """把目标应用窗口调到前台，保证后续屏幕坐标点击落在正确窗口上"""
    window = find_app_window(app)
    if not window:
        return False
    try:
        import win32gui
        hwnd = window["hwnd"]
        try:
            if not window.get("visible", True):
                win32gui.ShowWindow(hwnd, 9)      # SW_RESTORE 还原最小化
                time.sleep(0.6)
            win32gui.ShowWindow(hwnd, 9)
        except Exception:
            pass
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.35)
        return True
    except Exception as e:
        logger.debug(f"聚焦窗口失败({app}): {e}")
        return False


def capture_window(save_dir: str = "", region: Any = None, app: str = "game",
                   save: bool = False) -> Dict[str, Any]:
    """截取原神窗口（找不到则退回前台窗口/全屏）

    region：相对截取区域的四元组比例 (x1, y1, x2, y2)，取值 0~1；
            留空表示整窗。返回的 offset 已是「该区域左上角的屏幕坐标」，
            所以 items 的坐标可以直接 offset + 局部坐标。
    """
    if app not in ("game", ""):
        try:
            focus_app_window(app)
        except Exception:
            pass
    window = find_app_window(app)
    bbox = None
    if window:
        bbox = (window["left"], window["top"], window["right"], window["bottom"])
    elif app not in ("game", ""):
        # 不是原神窗口时（bgi / installer）不要退化到前台窗口，否则会截到别的程序
        return {"success": False, "message": "未找到 %s 窗口" % app}
    else:
        try:
            from zhiyan_web.screencap.service import _get_foreground_window_rect
            bbox = _get_foreground_window_rect()
        except Exception:
            bbox = None

    try:
        from PIL import ImageGrab
    except Exception as e:
        return {"success": False, "message": f"缺少 Pillow，无法截图: {e}"}

    path = ""
    if save and save_dir:
        os.makedirs(save_dir, exist_ok=True)
        path = os.path.join(save_dir, "bgi_vision_%d.png" % int(time.time() * 1000))

    region_rect = None
    grab_bbox = tuple(int(x) for x in bbox) if bbox else None
    if grab_bbox and region:
        try:
            x1, y1, x2, y2 = [float(v) for v in region]
            bw = grab_bbox[2] - grab_bbox[0]
            bh = grab_bbox[3] - grab_bbox[1]
            if max(x1, y1, x2, y2) <= 1.0001:
                x1, y1, x2, y2 = x1 * bw, y1 * bh, x2 * bw, y2 * bh
            rect = (
                grab_bbox[0] + int(round(x1)),
                grab_bbox[1] + int(round(y1)),
                grab_bbox[0] + int(round(x2)),
                grab_bbox[1] + int(round(y2)),
            )
            if rect[2] - rect[0] >= 16 and rect[3] - rect[1] >= 16:
                grab_bbox = rect
                region_rect = rect
        except Exception:
            region_rect = None

    try:
        if grab_bbox:
            img = ImageGrab.grab(bbox=grab_bbox)
            offset = (int(grab_bbox[0]), int(grab_bbox[1]))
        else:
            img = ImageGrab.grab(all_screens=True)
            offset = (0, 0)
        if path:
            img.save(path)
    except Exception as e:
        return {"success": False, "message": f"截图失败: {e}"}

    return {
        "success": True,
        "path": path,
        "image": img,
        "width": img.width,
        "height": img.height,
        "offset": offset,
        "region": list(region_rect) if region_rect else None,
        "window": window,
        "message": "",
    }


def click_at(x: float, y: float, offset: Tuple[int, int] = (0, 0)) -> bool:
    """在屏幕坐标点击"""
    try:
        import pyautogui
        pyautogui.click(int(offset[0] + x), int(offset[1] + y))
        return True
    except Exception as e:
        logger.warning(f"点击失败: {e}")
        return False


def press_key(key: str) -> bool:
    """按下允许的按键"""
    key = (key or "").strip().lower()
    allowed = {"escape", "esc", "space", "enter", "return", "f", "e", "tab"}
    if key not in allowed:
        return False
    try:
        import pyautogui
        pyautogui.press("esc" if key == "escape" else key)
        return True
    except Exception:
        return False


def scroll(times: int = -5) -> bool:
    """鼠标滚轮滚动（负数向下），用于更新页「划动页面」这类步骤"""
    try:
        import pyautogui
        pyautogui.scroll(int(times))
        return True
    except Exception:
        return False


# ============= 图案模板匹配 =============

_TEMPLATE_EXTS = (".png", ".jpg", ".jpeg", ".bmp")


def templates_dir() -> str:
    """图案模板目录（与截图目录分开，模板属于用户资产）"""
    try:
        from zhiyan_web.config import DATA_DIR
        return os.path.join(DATA_DIR, "bettergi", "templates")
    except Exception:
        return os.path.join(os.getcwd(), "data", "bettergi", "templates")


def list_templates() -> List[str]:
    """列出可用模板文件名"""
    d = templates_dir()
    if not os.path.isdir(d):
        return []
    try:
        return sorted(
            f for f in os.listdir(d)
            if f.lower().endswith(_TEMPLATE_EXTS) and os.path.isfile(os.path.join(d, f))
        )
    except Exception:
        return []


def _load_cv2():
    """加载 OpenCV（随 OCR 依赖一起安装，缺失时返回 None）"""
    try:
        import cv2
        return cv2
    except Exception:
        pass
    try:
        _ensure_sys_path()
        import cv2
        return cv2
    except Exception:
        return None


def _resolve_template(name: str) -> List[str]:
    """把规则里的模板名解析成实际文件（支持省略扩展名与多倍图）"""
    if not name:
        return []
    if os.path.isabs(name) and os.path.isfile(name):
        return [name]
    stem = os.path.splitext(os.path.basename(name))[0]
    d = templates_dir()
    hits: List[str] = []
    for fname in list_templates():
        fstem = os.path.splitext(fname)[0]
        # foo.png / foo@125.png / foo_2x.png 都算同一个模板
        if fstem == stem or fstem.startswith(stem + "@") or fstem.startswith(stem + "_"):
            hits.append(os.path.join(d, fname))
    return hits


def _region_rect(region: Any, width: int, height: int) -> Tuple[int, int, int, int]:
    """把 0~1 比例或像素坐标统一成图片内的像素矩形"""
    if not region:
        return 0, 0, int(width), int(height)
    try:
        x1, y1, x2, y2 = [float(v) for v in region]
    except Exception:
        return 0, 0, int(width), int(height)
    if max(x1, y1, x2, y2) <= 1.0001:
        x1, y1, x2, y2 = x1 * width, y1 * height, x2 * width, y2 * height
    x1 = max(0, min(int(width) - 1, int(round(x1))))
    y1 = max(0, min(int(height) - 1, int(round(y1))))
    x2 = max(0, min(int(width), int(round(x2))))
    y2 = max(0, min(int(height), int(round(y2))))
    if x2 - x1 < 8 or y2 - y1 < 8:
        return 0, 0, int(width), int(height)
    return x1, y1, x2, y2


def match_template(
    image_path: str,
    template_name: str,
    threshold: float = 0.85,
    region: Any = None,
) -> Dict[str, Any]:
    """在截图中查找图案模板（灰度匹配 + 多尺度尝试）

    返回的 box 是四点框（图片局部坐标），cx / cy 为匹配中心，
    score 为最高相似度；低于阈值时 ok=False，但 score 仍会返回，便于调阈值。
    """
    cv2 = _load_cv2()
    if cv2 is None:
        return {"ok": False, "score": 0.0, "message": "缺少 opencv-python，模板匹配不可用"}
    paths = _resolve_template(template_name)
    if not paths:
        return {"ok": False, "score": 0.0, "message": "模板不存在: %s" % template_name}

    if isinstance(image_path, str):
        img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    else:
        arr = _as_ndarray(image_path)
        img = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY) if arr is not None else None
    if img is None:
        return {"ok": False, "score": 0.0, "message": "读取截图失败"}

    height, width = img.shape[:2]
    rx1, ry1, rx2, ry2 = _region_rect(region, width, height)
    crop = img[ry1:ry2, rx1:rx2]

    best: Optional[Dict[str, Any]] = None
    for path in paths:
        tpl = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if tpl is None:
            continue
        for scale in (1.0, 0.8, 1.25):
            cur = tpl
            if scale != 1.0:
                tw = max(1, int(tpl.shape[1] * scale))
                th = max(1, int(tpl.shape[0] * scale))
                cur = cv2.resize(
                    tpl, (tw, th),
                    interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR,
                )
            if cur.shape[0] > crop.shape[0] or cur.shape[1] > crop.shape[1]:
                continue
            try:
                result = cv2.matchTemplate(crop, cur, cv2.TM_CCOEFF_NORMED)
            except Exception:
                continue
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            if best is None or max_val > best["score"]:
                x = int(max_loc[0]) + rx1
                y = int(max_loc[1]) + ry1
                w = int(cur.shape[1])
                h = int(cur.shape[0])
                best = {
                    "score": float(max_val),
                    "box": [[x, y], [x + w, y], [x + w, y + h], [x, y + h]],
                    "cx": x + w / 2.0,
                    "cy": y + h / 2.0,
                    "width": w,
                    "height": h,
                    "template": os.path.basename(path),
                    "scale": scale,
                }

    if best is None:
        return {"ok": False, "score": 0.0, "message": "模板尺寸大于识别区域", "template": template_name}

    best["threshold"] = float(threshold)
    best["ok"] = best["score"] >= float(threshold)
    best["message"] = "" if best["ok"] else "相似度 %.3f 低于阈值 %.2f" % (best["score"], float(threshold))
    return best


# ============= 规则匹配 =============

def load_rules(raw: Any) -> List[Dict[str, Any]]:
    """解析配置里的规则（JSON 字符串或列表），失败回退默认规则"""
    if isinstance(raw, list):
        data = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
        except Exception:
            data = []
    else:
        data = []
    rules: List[Dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if not (item.get("text") or item.get("key") or item.get("template")):
            continue
        rule: Dict[str, Any] = {
            "text": str(item.get("text") or ""),
            "action": str(item.get("action") or "click"),
            "key": str(item.get("key") or ""),
        }
        if item.get("template"):
            rule["template"] = str(item.get("template"))
        if item.get("region"):
            rule["region"] = item.get("region")
        if item.get("threshold") is not None:
            try:
                rule["threshold"] = float(item.get("threshold"))
            except Exception:
                pass
        rules.append(rule)
    return rules or list(DEFAULT_RULES)


def _in_region(item: Dict[str, Any], region: Any, size: Optional[Tuple[int, int]]) -> bool:
    """判断识别结果中心是否落在规则指定区域内（区域支持 0~1 比例）"""
    if not region or not size:
        return True
    try:
        x1, y1, x2, y2 = _region_rect(region, size[0], size[1])
        cx = float(item.get("cx") or 0)
        cy = float(item.get("cy") or 0)
    except Exception:
        return True
    return x1 <= cx <= x2 and y1 <= cy <= y2


def _same_template(a: Any, b: Any) -> bool:
    """模板名比对（忽略扩展名与多倍图后缀）"""
    if not a or not b:
        return False
    sa = os.path.splitext(os.path.basename(str(a)))[0]
    sb = os.path.splitext(os.path.basename(str(b)))[0]
    sa = sa.split("@")[0]
    sb = sb.split("@")[0]
    return sa == sb or sa.startswith(sb) or sb.startswith(sa)


def match_rule(
    items: List[Dict[str, Any]],
    rules: List[Dict[str, Any]],
    image_size: Optional[Tuple[int, int]] = None,
) -> Optional[Dict[str, Any]]:
    """按规则优先级查找要执行的规则（文本规则匹配 text，图案规则匹配 template）"""
    for rule in rules:
        keyword = rule.get("text") or ""
        action = (rule.get("action") or "click").lower()
        template = rule.get("template") or ""
        region = rule.get("region")

        # 图案规则：按模板名匹配，模板缺失时自然没有对应 item，直接跳过
        if template:
            for item in items:
                if item.get("kind") != "template":
                    continue
                if not _same_template(item.get("template"), template):
                    continue
                if not _in_region(item, region, image_size):
                    continue
                return {"rule": rule, "item": item, "keyword": keyword or template}

        # 无文字的按键规则（如 Escape）直接命中
        if not keyword and action == "key":
            return {"rule": rule, "item": None, "keyword": ""}
        if not keyword:
            continue
        for item in items:
            text = item.get("text") or ""
            if keyword in text and _in_region(item, region, image_size):
                return {"rule": rule, "item": item, "keyword": keyword}
    return None


def recognize_scene(
    image_path: Any,
    rules: Optional[List[Dict[str, Any]]] = None,
    region: Any = None,
) -> Dict[str, Any]:
    """一次识别：OCR 文本 + 图案模板，统一成 items

    - 文本项带 kind=text，图案项带 kind=template 与 template 名
    - 模板缺文件或缺少 OpenCV 时自动跳过，不影响 OCR
    """
    rules = rules or []
    ocr = recognize(image_path)
    items: List[Dict[str, Any]] = []
    if ocr.get("success"):
        for it in ocr.get("items") or []:
            it["kind"] = "text"
        items.extend(ocr.get("items") or [])

    template_rules = [r for r in rules if r.get("template")]
    matched_templates: List[str] = []
    for rule in template_rules:
        name = rule.get("template")
        result = match_template(
            image_path,
            name,
            float(rule.get("threshold") or 0.85),
            rule.get("region") or region,
        )
        if not result.get("ok"):
            continue
        matched_templates.append(result.get("template") or str(name))
        items.append(
            {
                "text": rule.get("text") or result.get("template") or str(name),
                "kind": "template",
                "score": result.get("score") or 0,
                "box": result.get("box"),
                "cx": result.get("cx") or 0,
                "cy": result.get("cy") or 0,
                "template": result.get("template") or str(name),
            }
        )

    text = "\n".join(i.get("text") or "" for i in items)
    success = bool(items)
    message = ""
    if not success:
        message = ocr.get("message") or ("未识别到文本或图案")
    return {
        "success": success,
        "engine": ocr.get("engine") or "",
        "text": text,
        "items": items,
        "template_rules": len(template_rules),
        "templates_matched": matched_templates,
        "message": message,
    }


# ============= AI 决策 =============

_AI_SYSTEM_PROMPT = """你是原神自动化恢复助手。根据截图和 OCR 文本，判断当前卡在什么界面，并给出一个最合理的下一步操作。
只输出严格 JSON，不要解释、不要代码块：
{"action":"click","target_text":"要点击的界面文字","reason":"简短原因"}
或 {"action":"key","key":"Escape","reason":"简短原因"}
或 {"action":"wait","reason":"短暂等待即可"}
或 {"action":"give_up","reason":"无法安全判断"}
限制：target_text 必须来自给定 OCR 文本；key 只能是 Escape / Space / Enter。"""


async def ai_decide(image_path: Any, ocr_text: str, items: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """让多模态模型或文本模型给出下一步动作"""
    from zhiyan_web.edu.llm_utils import parse_object, vision_chat, one_shot

    # 1) 有视觉模型时直接看图
    try:
        data_url = _to_data_url(image_path)
        reply = await vision_chat(
            _AI_SYSTEM_PROMPT,
            "OCR 文本如下：\n" + (ocr_text or "（无文字）") + "\n请输出下一步动作 JSON。",
            image_data_url=data_url,
            temperature=0.1,
            max_tokens=300,
        )
    except Exception as e:
        logger.debug(f"视觉模型调用失败: {e}")
        reply = None

    # 2) 退化为纯文本模型
    if not reply:
        reply = await one_shot(
            _AI_SYSTEM_PROMPT,
            "OCR 文本如下：\n" + (ocr_text or "（无文字）") + "\n请输出下一步动作 JSON。",
            temperature=0.1,
            max_tokens=300,
        )
    if not reply:
        return None

    action = parse_object(reply)
    if not isinstance(action, dict):
        return None
    return action


def _to_data_url(image: Any) -> str:
    """把图像（路径或内存图像）转成 data URL，不落盘"""
    if isinstance(image, str) and os.path.isfile(image):
        ext = os.path.splitext(image)[1].lower().lstrip(".") or "png"
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "bmp": "bmp", "webp": "webp"}.get(ext, "png")
        with open(image, "rb") as f:
            return "data:image/%s;base64,%s" % (mime, base64.b64encode(f.read()).decode("ascii"))
    pil = _as_pil(image)
    if pil is None:
        return ""
    buf = io.BytesIO()
    pil.save(buf, format="JPEG", quality=80)
    return "data:image/jpeg;base64,%s" % base64.b64encode(buf.getvalue()).decode("ascii")


def _screen_boxes(
    items: List[Dict[str, Any]],
    offset: Tuple[int, int] = (0, 0),
    limit: int = 40,
) -> List[Dict[str, Any]]:
    """把 OCR items 转成屏幕绝对坐标的矩形，供叠加层绘制"""
    ox, oy = int(offset[0]), int(offset[1])
    boxes: List[Dict[str, Any]] = []
    for item in items or []:
        # box 可能是 numpy 数组，不能用 `or []` 判真值
        box = item.get("box")
        if box is None:
            continue
        try:
            xs = [float(p[0]) for p in box]
            ys = [float(p[1]) for p in box]
        except Exception:
            continue
        if not xs or not ys:
            continue
        boxes.append(
            {
                "left": min(xs) + ox,
                "top": min(ys) + oy,
                "right": max(xs) + ox,
                "bottom": max(ys) + oy,
                "text": str(item.get("text") or ""),
            }
        )
        if len(boxes) >= limit:
            break
    return boxes


# ============= 恢复流程 =============

async def recover_once(
    save_dir: str,
    rules: List[Dict[str, Any]],
    ai_enabled: bool = True,
    region: Any = None,
) -> Dict[str, Any]:
    """执行一次「截图 → OCR/模板识别 → 规则点击 / AI 决策」"""
    shot = capture_window("", region=region)   # 不落盘，内存图像
    if not shot.get("success"):
        return {"success": False, "stage": "capture", "message": shot.get("message") or "截图失败"}

    scene = recognize_scene(shot.get("image"), rules, region=None)
    if not scene.get("success"):
        return {
            "success": False,
            "stage": "ocr",
            "message": scene.get("message") or "识别失败",
            "shot": "",
        }

    items = scene.get("items") or []
    ocr_text = scene.get("text") or ""
    image_size = (int(shot.get("width") or 0), int(shot.get("height") or 0))
    boxes = _screen_boxes(items, tuple(shot.get("offset") or (0, 0)))

    matched = match_rule(items, rules, image_size)
    if matched:
        rule = matched["rule"]
        item = matched.get("item")
        action = (rule.get("action") or "click").lower()
        if action == "key":
            key = rule.get("key") or "Escape"
            pressed = press_key(key)
            return {
                "success": pressed,
                "stage": "rule",
                "engine": scene.get("engine"),
                "matched": matched["keyword"],
                "shot": "",
                "boxes": boxes,
                "message": "规则命中并按键: %s" % key,
            }
        if action == "wait":
            return {
                "success": True,
                "stage": "rule",
                "engine": scene.get("engine"),
                "matched": matched["keyword"],
                "shot": "",
                "boxes": boxes,
                "message": "规则命中，按规则等待",
            }
        if item:
            clicked = click_at(item.get("cx") or 0, item.get("cy") or 0, tuple(shot.get("offset") or (0, 0)))
            return {
                "success": clicked,
                "stage": "rule",
                "engine": scene.get("engine"),
                "matched": matched["keyword"],
                "text": item.get("text"),
                "kind": item.get("kind"),
                "shot": "",
                "boxes": boxes,
                "message": "规则命中并点击: %s" % matched["keyword"],
            }

    if not ai_enabled:
        return {
            "success": False,
            "stage": "ai",
            "engine": scene.get("engine"),
            "message": "规则未命中且 AI 决策已关闭",
            "text": ocr_text,
            "shot": "",
            "boxes": boxes,
        }

    decision = await ai_decide(shot.get("image"), ocr_text, items)
    if not decision:
        return {
            "success": False,
            "stage": "ai",
            "engine": scene.get("engine"),
            "message": "AI 未返回可用动作",
            "text": ocr_text,
            "shot": "",
            "boxes": boxes,
        }

    action = str(decision.get("action") or "").lower()
    if action == "click":
        target = str(decision.get("target_text") or "")
        item = None
        for candidate in items:
            if target and target in (candidate.get("text") or ""):
                item = candidate
                break
        if item:
            clicked = click_at(item.get("cx") or 0, item.get("cy") or 0, tuple(shot.get("offset") or (0, 0)))
            return {
                "success": clicked,
                "stage": "ai",
                "engine": ocr.get("engine"),
                "matched": target,
                "text": item.get("text"),
                "reason": decision.get("reason") or "",
                "shot": "",
                "boxes": boxes,
                "message": "AI 决策点击: %s" % target,
            }
        return {
            "success": False,
            "stage": "ai",
            "engine": ocr.get("engine"),
            "message": "AI 指定的文字未出现在 OCR 结果中: %s" % target,
            "shot": "",
            "boxes": boxes,
        }

    if action == "key":
        pressed = press_key(str(decision.get("key") or ""))
        return {
            "success": pressed,
            "stage": "ai",
            "engine": ocr.get("engine"),
            "reason": decision.get("reason") or "",
            "shot": "",
            "boxes": boxes,
            "message": "AI 决策按键: %s" % decision.get("key"),
        }

    return {
        "success": False,
        "stage": "ai",
        "engine": ocr.get("engine"),
        "reason": decision.get("reason") or "",
        "shot": "",
        "boxes": boxes,
        "message": "AI 决策: %s" % action,
    }


# ============= 引擎安装 =============

def ensure_engine() -> Dict[str, Any]:
    """安装本地 OCR 引擎到 data/py_deps/ocr（源码环境与带 Python 的机器均可）"""
    _ensure_sys_path()
    engine, kind = _load_engine()
    if engine is not None:
        return {"ok": True, "message": "OCR 引擎已就绪: %s" % kind}

    if sys.version_info >= (3, 13):
        spec = "rapidocr[onnxruntime]"
    else:
        spec = "rapidocr-onnxruntime==1.4.4"

    python_exe = sys.executable or ""
    if getattr(sys, "frozen", False):
        python_exe = shutil.which("python") or shutil.which("python3") or ""
    if not python_exe:
        return {
            "ok": False,
            "message": "当前环境没有可用的 pip（打包版请使用内置 OCR 的安装包，或先安装 Python）。",
        }

    vendor = vendor_dir()
    os.makedirs(vendor, exist_ok=True)
    import subprocess
    cmd = [
        python_exe, "-m", "pip", "install",
        "--target", vendor, "--upgrade", "--no-warn-script-location",
        "--disable-pip-version-check", "--no-input", spec,
    ]
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, creationflags=creationflags)
    except Exception as e:
        return {"ok": False, "message": "安装失败: %s" % e}

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-600:]
        return {"ok": False, "message": "pip 安装失败: %s" % tail}

    _ensure_sys_path()
    reset_engine()
    engine, kind = _load_engine()
    if engine is None:
        return {"ok": False, "message": "安装完成但仍无法加载 OCR 引擎，请查看后端日志"}
    return {"ok": True, "message": "OCR 引擎安装成功: %s（目录 %s）" % (kind, vendor)}
