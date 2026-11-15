"""
屏幕红框叠加层（调试用）

用途：把 OCR 识别到的文本区域，用红框实时画在屏幕对应位置，方便核对识别是否准确。
约束：
- 无边框、置顶、点击穿透（Windows 下通过 WS_EX_TRANSPARENT），不抢占焦点
- 默认不启动，仅在 overlay_enabled 打开时由兜底流程调用
- 单例工作线程 + 队列渲染，避免多次创建 Tk 实例
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("ZhiYanWeb.bettergi.overlay")

_KEY_COLOR = "#010203"  # 透明色键，与被绘制内容不冲突
_BOX_COLOR = "#ff3b30"
_TEXT_COLOR = "#ffffff"

# Win32 常量
_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79
_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_NOACTIVATE = 0x08000000
_LOGPIXELSX = 88


def _virtual_screen() -> tuple:
    """返回 (x, y, width, height)，覆盖所有显示器"""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        return (
            int(user32.GetSystemMetrics(_SM_XVIRTUALSCREEN)),
            int(user32.GetSystemMetrics(_SM_YVIRTUALSCREEN)),
            int(user32.GetSystemMetrics(_SM_CXVIRTUALSCREEN)),
            int(user32.GetSystemMetrics(_SM_CYVIRTUALSCREEN)),
        )
    except Exception:
        return (0, 0, 1920, 1080)


def _auto_scale() -> float:
    """截图是物理像素；若进程未声明 DPI 感知，Tk 坐标是逻辑像素，需要换算"""
    if os.name != "nt":
        return 1.0
    try:
        import ctypes

        aware = ctypes.c_int(0)
        ctypes.windll.shcore.GetProcessDpiAwareness(None, ctypes.byref(aware))
        if aware.value != 0:
            return 1.0
        dc = ctypes.windll.user32.GetDC(0)
        try:
            dpi = int(ctypes.windll.gdi32.GetDeviceCaps(dc, _LOGPIXELSX))
        finally:
            ctypes.windll.user32.ReleaseDC(0, dc)
        return max(1.0, dpi / 96.0)
    except Exception:
        return 1.0


class _OverlayWorker:
    """在独立线程里持有一个 Tk 窗口，通过队列接收绘制任务"""

    def __init__(self):
        self._queue: "queue.Queue" = queue.Queue(maxsize=16)
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._root = None
        self._canvas = None
        self._hide_job = None
        self._error = ""
        self._scale = 1.0
        self._last: Dict[str, Any] = {}
        self._stop = False

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def status(self) -> Dict[str, Any]:
        return {
            "available": self.running and not self._error,
            "running": self.running,
            "error": self._error,
            "scale": round(self._scale, 3),
            "last": self._last,
        }

    def start(self) -> bool:
        if self.running:
            return True
        self._stop = False
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="bgi-overlay", daemon=True)
        self._thread.start()
        return self._ready.wait(5.0)

    def show(self, boxes: List[Dict[str, Any]], duration_ms: int = 3000, scale: float = 0.0) -> Dict[str, Any]:
        if not self.start():
            return {"ok": False, "message": self._error or "叠加层启动失败"}
        payload = {"boxes": boxes or [], "duration": max(500, int(duration_ms)), "scale": float(scale or 0)}
        try:
            self._queue.put_nowait(payload)
        except queue.Full:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(payload)
            except Exception:
                return {"ok": False, "message": "叠加层繁忙，已丢弃本次绘制"}
        return {"ok": True, "message": "已提交 %d 个框" % len(boxes or [])}

    def stop(self) -> None:
        self._stop = True
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass

    # ---------- 工作线程 ----------

    def _run(self) -> None:
        try:
            import tkinter as tk
        except Exception as e:
            self._error = "缺少 tkinter: %s" % e
            logger.warning(self._error)
            self._ready.set()
            return

        self._scale = _auto_scale()
        try:
            root = tk.Tk()
            root.withdraw()
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            x, y, w, h = _virtual_screen()
            root.geometry("%dx%d%+d%+d" % (w, h, x, y))
            try:
                root.configure(bg=_KEY_COLOR)
                root.attributes("-transparentcolor", _KEY_COLOR)
            except Exception:
                # 透明色键不可用时退化为半透明
                root.attributes("-alpha", 0.35)
                root.configure(bg="#111111")
            canvas = tk.Canvas(root, bg=root.cget("bg"), highlightthickness=0, bd=0)
            canvas.pack(fill="both", expand=True)
            self._root = root
            self._canvas = canvas
            self._make_click_through(root)
            self._ready.set()
            root.after(40, self._poll)
            root.mainloop()
        except Exception as e:
            self._error = "叠加层异常: %s" % e
            logger.warning(self._error)
            self._ready.set()

    @staticmethod
    def _make_click_through(root) -> None:
        if os.name != "nt":
            return
        try:
            import ctypes

            user32 = ctypes.windll.user32
            root.update_idletasks()
            hwnd = user32.GetParent(root.winfo_id()) or root.winfo_id()
            style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
            user32.SetWindowLongW(
                hwnd,
                _GWL_EXSTYLE,
                style | _WS_EX_LAYERED | _WS_EX_TRANSPARENT | _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE,
            )
        except Exception as e:
            logger.warning(f"设置点击穿透失败: {e}")

    def _poll(self) -> None:
        if self._stop:
            try:
                self._root.destroy()
            except Exception:
                pass
            return
        try:
            while True:
                payload = self._queue.get_nowait()
                if payload is None:
                    continue
                self._render(payload)
        except queue.Empty:
            pass
        except Exception as e:
            self._error = str(e)
        try:
            self._root.after(40, self._poll)
        except Exception:
            pass

    def _render(self, payload: Dict[str, Any]) -> None:
        root, canvas = self._root, self._canvas
        if root is None or canvas is None:
            return
        if self._hide_job:
            try:
                root.after_cancel(self._hide_job)
            except Exception:
                pass
            self._hide_job = None
        canvas.delete("all")

        boxes = payload.get("boxes") or []
        scale = float(payload.get("scale") or 0) or self._scale
        offset_x, offset_y, _, _ = _virtual_screen()
        drawn = 0
        for box in boxes:
            try:
                left = float(box.get("left", 0)) / scale - offset_x
                top = float(box.get("top", 0)) / scale - offset_y
                right = float(box.get("right", 0)) / scale - offset_x
                bottom = float(box.get("bottom", 0)) / scale - offset_y
            except Exception:
                continue
            if right <= left or bottom <= top:
                continue
            canvas.create_rectangle(left, top, right, bottom, outline=_BOX_COLOR, width=3)
            label = str(box.get("text") or "")[:24]
            if label:
                ty = top - 18 if top > 24 else bottom + 4
                canvas.create_text(
                    left + 2,
                    ty,
                    anchor="nw",
                    text=label,
                    fill=_TEXT_COLOR,
                    font=("Microsoft YaHei", 10),
                )
            drawn += 1

        self._last = {
            "ts": time.time(),
            "boxes": drawn,
            "scale": round(scale, 3),
        }
        if not drawn:
            try:
                root.withdraw()
            except Exception:
                pass
            return

        try:
            root.deiconify()
            root.attributes("-topmost", True)
            root.lift()
        except Exception:
            pass
        self._hide_job = root.after(int(payload.get("duration") or 3000), self._hide)

    def _hide(self) -> None:
        self._hide_job = None
        try:
            if self._canvas is not None:
                self._canvas.delete("all")
            if self._root is not None:
                self._root.withdraw()
        except Exception:
            pass


_worker: Optional[_OverlayWorker] = None
_lock = threading.Lock()


def _get_worker() -> _OverlayWorker:
    global _worker
    with _lock:
        if _worker is None:
            _worker = _OverlayWorker()
        return _worker


def show_boxes(boxes: List[Dict[str, Any]], duration_ms: int = 3000, scale: float = 0.0) -> Dict[str, Any]:
    """在屏幕上绘制识别框（屏幕绝对坐标，物理像素）"""
    return _get_worker().show(boxes, duration_ms=duration_ms, scale=scale)


def overlay_status() -> Dict[str, Any]:
    return _get_worker().status()


def reset_overlay() -> Dict[str, Any]:
    """关闭叠加层窗口（下次绘制会重新创建）"""
    global _worker
    with _lock:
        worker, _worker = _worker, None
    if worker:
        worker.stop()
    return {"ok": True, "message": "叠加层已关闭"}


def sample_boxes() -> List[Dict[str, Any]]:
    """用于连通性测试的三个框"""
    x, y, _, _ = _virtual_screen()
    return [
        {"left": x + 120, "top": y + 120, "right": x + 420, "bottom": y + 180, "text": "测试红框 1"},
        {"left": x + 120, "top": y + 220, "right": x + 520, "bottom": y + 280, "text": "测试红框 2"},
        {"left": x + 120, "top": y + 320, "right": x + 360, "bottom": y + 380, "text": "测试红框 3"},
    ]
