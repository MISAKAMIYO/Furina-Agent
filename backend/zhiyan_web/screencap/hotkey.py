"""
全局热键管理（Windows RegisterHotKey）
======================================
使用 Win32 RegisterHotKey + GetMessage 消息循环，实现系统级全局热键：
无论 Furina Agent 是否在前台，热键都能触发截图 / 录屏操作。

热键格式: "Ctrl+Shift+A" / "Alt+F1" / "Ctrl+Win+Space" 等。
修饰键: Ctrl / Alt / Shift / Win（Win 即 Windows 徽标键）
"""
from __future__ import annotations

import ctypes
import logging
import sys
import threading
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("ZhiYanWeb.Screencap.Hotkey")

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

# 键名 -> 虚拟键码（A-Z / 0-9 / F1-F12 / 常见特殊键）
_VK = {
    "A": 0x41, "B": 0x42, "C": 0x43, "D": 0x44, "E": 0x45, "F": 0x46,
    "G": 0x47, "H": 0x48, "I": 0x49, "J": 0x4A, "K": 0x4B, "L": 0x4C,
    "M": 0x4D, "N": 0x4E, "O": 0x4F, "P": 0x50, "Q": 0x51, "R": 0x52,
    "S": 0x53, "T": 0x54, "U": 0x55, "V": 0x56, "W": 0x57, "X": 0x58,
    "Y": 0x59, "Z": 0x5A,
    "0": 0x30, "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34,
    "5": 0x35, "6": 0x36, "7": 0x37, "8": 0x38, "9": 0x39,
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73, "F5": 0x74,
    "F6": 0x75, "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79,
    "F11": 0x7A, "F12": 0x7B,
    "SPACE": 0x20, "ENTER": 0x0D, "ESC": 0x1B, "TAB": 0x09,
    "BACKSPACE": 0x08, "DELETE": 0x2E, "INSERT": 0x2D,
    "HOME": 0x24, "END": 0x23, "PAGEUP": 0x21, "PAGEDOWN": 0x22,
    "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28,
    "MINUS": 0xBD, "EQUAL": 0xBB, "COMMA": 0xBC, "PERIOD": 0xBE,
    "SLASH": 0xBF, "BACKSLASH": 0xDC, "SEMICOLON": 0xBA, "QUOTE": 0xDE,
    "LBRACKET": 0xDB, "RBRACKET": 0xDD, "GRAVE": 0xC0,
    "NUMPAD0": 0x60, "NUMPAD1": 0x61, "NUMPAD2": 0x62, "NUMPAD3": 0x63,
    "NUMPAD4": 0x64, "NUMPAD5": 0x65, "NUMPAD6": 0x66, "NUMPAD7": 0x67,
    "NUMPAD8": 0x68, "NUMPAD9": 0x69,
}

_MOD_NAMES = {
    "ctrl": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
}


def parse_combo(combo: str):
    """解析热键字符串，返回 (modifiers, vk) 或 None"""
    if not combo or not isinstance(combo, str):
        return None
    parts = [p.strip() for p in combo.split("+") if p.strip()]
    if not parts:
        return None
    key_part = parts[-1].upper()
    if key_part not in _VK:
        return None
    mods = 0
    for mod_name in parts[:-1]:
        m = _MOD_NAMES.get(mod_name.lower())
        if m is None:
            return None
        mods |= m
    if not mods:
        return None  # 必须带修饰键，避免全局占用普通键
    return mods, _VK[key_part]


class HotkeyManager:
    """全局热键管理器（单例，由 get_hotkey_manager() 获取）"""

    def __init__(self) -> None:
        self._callback: Optional[Callable[[str], None]] = None
        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._current_hotkeys: Dict[str, str] = {}
        self._enabled = True
        self._id_counter = 1

    def set_callback(self, callback: Callable[[str], None]) -> None:
        self._callback = callback

    def start(self, hotkeys: Dict[str, str], enabled: bool = True) -> bool:
        """启动热键线程（需在 Windows 上）"""
        if sys.platform != "win32":
            logger.info("当前系统不支持全局热键（仅 Windows）")
            return False
        self._current_hotkeys = dict(hotkeys or {})
        self._enabled = enabled
        return self._ensure_running()

    def apply(self, hotkeys: Dict[str, str], enabled: bool = True) -> None:
        """重绑热键：停止旧线程并启动新线程"""
        with self._lock:
            self._stop_thread_locked()
            self._current_hotkeys = dict(hotkeys or {})
            self._enabled = enabled
            if enabled and self._current_hotkeys:
                self._ensure_running_locked()

    def stop(self) -> None:
        with self._lock:
            self._stop_thread_locked()

    def _ensure_running(self) -> bool:
        with self._lock:
            return self._ensure_running_locked()

    def _ensure_running_locked(self) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return True
        if not self._enabled or not self._current_hotkeys:
            return False
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="ScreencapHotkey", daemon=True
        )
        self._thread.start()
        self._thread_id = self._thread.ident
        return True

    def _stop_thread_locked(self) -> None:
        if self._thread is None:
            return
        thread_id = self._thread_id
        if thread_id:
            try:
                ctypes.windll.user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
            except Exception:
                pass
        self._stop_event.set()
        self._thread.join(timeout=2)
        self._thread = None
        self._thread_id = None

    # ── 消息循环线程 ──

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        # 防止 64 位下句柄截断
        try:
            user32.GetMessageW.argtypes = [
                ctypes.POINTER(ctypes.wintypes.MSG),
                ctypes.wintypes.HWND,
                ctypes.wintypes.UINT,
                ctypes.wintypes.UINT,
            ]
            user32.GetMessageW.restype = ctypes.wintypes.BOOL
            user32.RegisterHotKey.restype = ctypes.wintypes.BOOL
        except Exception:
            pass

        registered: Dict[int, str] = {}
        for action, combo in self._current_hotkeys.items():
            parsed = parse_combo(combo)
            if not parsed:
                logger.warning(f"热键格式无效，跳过: {action}={combo}")
                continue
            mods, vk = parsed
            hotkey_id = self._next_id()
            # 仅注册非重复触发的热键
            ok = user32.RegisterHotKey(None, hotkey_id, mods | MOD_NOREPEAT, vk)
            if ok:
                registered[hotkey_id] = action
                logger.info(f"已注册全局热键: {action} -> {combo}")
            else:
                logger.warning(f"热键注册失败（可能被其他程序占用）: {action} -> {combo}")

        if not registered:
            logger.info("没有可用的全局热键，热键线程退出")
            return

        msg = ctypes.wintypes.MSG()
        while not self._stop_event.is_set():
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r <= 0:
                break
            if msg.message == WM_HOTKEY:
                action = registered.get(int(msg.wParam))
                if action and self._callback:
                    try:
                        self._callback(action)
                    except Exception as e:
                        logger.error(f"热键回调异常 {action}: {e}")

        # 注销所有热键
        for hotkey_id in registered:
            try:
                user32.UnregisterHotKey(None, hotkey_id)
            except Exception:
                pass
        logger.info("全局热键线程已退出")

    def _next_id(self) -> int:
        hid = self._id_counter
        self._id_counter += 1
        return hid


# 全局单例
_hotkey_manager: Optional[HotkeyManager] = None


def get_hotkey_manager() -> HotkeyManager:
    global _hotkey_manager
    if _hotkey_manager is None:
        _hotkey_manager = HotkeyManager()
    return _hotkey_manager
