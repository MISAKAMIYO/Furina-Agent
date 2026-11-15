"""
桌面操作工具 - 键盘鼠标模拟、屏幕截图
"""
from __future__ import annotations

import os
import logging
import tempfile
from typing import Dict

logger = logging.getLogger("ZhiYanWeb.Computer.DesktopOps")

# 桌面自动化是否可用
_HAS_PYAUTOGUI = False
_HAS_PIL = False
_HAS_PYWIN32 = False

try:
    import pyautogui
    _HAS_PYAUTOGUI = True
except ImportError:
    pass

try:
    from PIL import Image
    _HAS_PIL = True
except ImportError:
    pass

try:
    import win32api
    import win32con
    _HAS_PYWIN32 = True
except ImportError:
    pass


def take_screenshot() -> Dict:
    """截取屏幕截图"""
    image_path = None

    if _HAS_PYAUTOGUI:
        try:
            screenshot = pyautogui.screenshot()
            # 保存到临时文件
            tmp_dir = os.path.join(tempfile.gettempdir(), "zhiyan_screenshots")
            os.makedirs(tmp_dir, exist_ok=True)
            from datetime import datetime
            filename = f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            image_path = os.path.join(tmp_dir, filename)
            screenshot.save(image_path)
            return {
                "path": image_path,
                "size": os.path.getsize(image_path),
                "width": screenshot.width,
                "height": screenshot.height,
                "message": f"截图已保存: {image_path}",
            }
        except Exception as e:
            logger.error(f"截图失败: {e}")

    # 使用 PowerShell 作为备选
    try:
        import subprocess
        import json
        tmp_dir = os.path.join(tempfile.gettempdir(), "zhiyan_screenshots")
        os.makedirs(tmp_dir, exist_ok=True)
        from datetime import datetime
        filename = f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        image_path = os.path.join(tmp_dir, filename)

        ps_script = f'''
        Add-Type -AssemblyName System.Windows.Forms
        $screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
        $bitmap = New-Object System.Drawing.Bitmap $screen.Width, $screen.Height
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        $graphics.CopyFromScreen($screen.X, $screen.Y, 0, 0, $screen.Size)
        $bitmap.Save("{image_path}", [System.Drawing.Imaging.ImageFormat]::Png)
        $graphics.Dispose()
        $bitmap.Dispose()
        Write-Output "$($screen.Width)x$($screen.Height)"
        '''
        result = subprocess.run(
            ["powershell", "-Command", ps_script],
            capture_output=True, text=True, timeout=30
        )
        if os.path.exists(image_path):
            resolution = result.stdout.strip()
            return {
                "path": image_path,
                "size": os.path.getsize(image_path),
                "resolution": resolution,
                "message": f"截图已保存: {image_path}",
            }
    except Exception as e:
        logger.error(f"PowerShell 截图失败: {e}")

    raise RuntimeError("截图失败: 需要安装 pyautogui (pip install pyautogui)")


def get_mouse_position() -> Dict:
    """获取鼠标当前位置"""
    if _HAS_PYAUTOGUI:
        x, y = pyautogui.position()
        return {"x": x, "y": y}
    elif _HAS_PYWIN32:
        import win32api
        import win32con
        x, y = win32api.GetCursorPos()
        return {"x": x, "y": y}
    raise RuntimeError("需要安装 pyautogui 或 pywin32")


def move_mouse(x: int, y: int) -> Dict:
    """移动鼠标到指定位置"""
    if _HAS_PYAUTOGUI:
        pyautogui.moveTo(x, y, duration=0.2)
        return {"x": x, "y": y, "message": f"鼠标已移动到 ({x}, {y})"}
    elif _HAS_PYWIN32:
        import win32api
        import win32con
        win32api.SetCursorPos((x, y))
        return {"x": x, "y": y, "message": f"鼠标已移动到 ({x}, {y})"}
    raise RuntimeError("需要安装 pyautogui 或 pywin32")


def click_mouse(button: str = "left") -> Dict:
    """点击鼠标

    Args:
        button: left / right / middle
    """
    if _HAS_PYAUTOGUI:
        pyautogui.click(button=button)
        return {"button": button, "message": f"鼠标已点击 ({button})"}
    elif _HAS_PYWIN32:
        import win32api
        import win32con
        btn_map = {
            "left": (win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP),
            "right": (win32con.MOUSEEVENTF_RIGHTDOWN, win32con.MOUSEEVENTF_RIGHTUP),
            "middle": (win32con.MOUSEEVENTF_MIDDLEDOWN, win32con.MOUSEEVENTF_MIDDLEUP),
        }
        if button not in btn_map:
            raise ValueError(f"不支持的按钮: {button}")
        down, up = btn_map[button]
        win32api.mouse_event(down, 0, 0, 0, 0)
        win32api.mouse_event(up, 0, 0, 0, 0)
        return {"button": button, "message": f"鼠标已点击 ({button})"}
    raise RuntimeError("需要安装 pyautogui 或 pywin32")


def type_text(text: str) -> Dict:
    """输入文本"""
    if _HAS_PYAUTOGUI:
        pyautogui.write(text, interval=0.01)
        return {"text": text, "length": len(text), "message": f"已输入文本 ({len(text)} 字符)"}
    raise RuntimeError("需要安装 pyautogui (pip install pyautogui)")


def press_key(key: str) -> Dict:
    """按下按键

    Args:
        key: 按键名称，如 enter, tab, escape, f5, ctrl+c 等
    """
    if _HAS_PYAUTOGUI:
        if "+" in key:
            # 组合键 Ctrl+C 等
            parts = key.lower().split("+")
            modifiers = [p.strip() for p in parts[:-1]]
            main_key = parts[-1].strip()
            pyautogui.hotkey(*modifiers, main_key)
        else:
            pyautogui.press(key.lower())
        return {"key": key, "message": f"已按下按键: {key}"}
    raise RuntimeError("需要安装 pyautogui (pip install pyautogui)")


def get_screen_size() -> Dict:
    """获取屏幕分辨率"""
    if _HAS_PYAUTOGUI:
        width, height = pyautogui.size()
        return {"width": width, "height": height}
    elif _HAS_PYWIN32:
        import win32api
        import win32con
        width = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
        height = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)
        return {"width": width, "height": height}
    # 使用 PowerShell 作为备选
    try:
        import subprocess
        result = subprocess.run(
            ["powershell", "-Command", "(Get-WmiObject -Class Win32_VideoController).CurrentHorizontalResolution.ToString() + 'x' + (Get-WmiObject -Class Win32_VideoController).CurrentVerticalResolution.ToString()"],
            capture_output=True, text=True, timeout=10
        )
        resolution = result.stdout.strip()
        if "x" in resolution:
            parts = resolution.split("x")
            return {"width": int(parts[0]), "height": int(parts[1])}
    except Exception:
        pass
    return {"width": 1920, "height": 1080, "_note": "估算值"}


def scroll_mouse(clicks: int = 1) -> Dict:
    """滚动鼠标滚轮

    Args:
        clicks: 滚动次数，正数向上，负数向下
    """
    if _HAS_PYAUTOGUI:
        pyautogui.scroll(clicks)
        return {"clicks": clicks, "message": f"鼠标滚轮滚动: {clicks} 格"}
    raise RuntimeError("需要安装 pyautogui (pip install pyautogui)")


def double_click() -> Dict:
    """双击鼠标左键"""
    if _HAS_PYAUTOGUI:
        pyautogui.doubleClick()
        return {"message": "鼠标已双击"}
    elif _HAS_PYWIN32:
        import win32api
        import win32con
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        return {"message": "鼠标已双击"}
    raise RuntimeError("需要安装 pyautogui 或 pywin32")


def drag_mouse(x: int, y: int) -> Dict:
    """拖拽鼠标到指定位置

    Args:
        x: 目标 X 坐标
        y: 目标 Y 坐标
    """
    if _HAS_PYAUTOGUI:
        pyautogui.drag(x, y, duration=0.3)
        return {"x": x, "y": y, "message": f"鼠标已拖拽到 ({x}, {y})"}
    raise RuntimeError("需要安装 pyautogui (pip install pyautogui)")


def get_clipboard() -> Dict:
    """获取剪贴板内容"""
    try:
        import pyperclip
        text = pyperclip.paste()
        return {"text": text, "length": len(text), "message": "已读取剪贴板"}
    except ImportError:
        pass
    try:
        import subprocess
        cmd = "powershell -Command \"Get-Clipboard\""
        result = subprocess.run(
            ["powershell", "-Command", "Get-Clipboard"],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip():
            return {"text": result.stdout.strip(), "length": len(result.stdout.strip()), "method": "powershell"}
    except Exception:
        pass
    return {"error": "需要安装 pyperclip (pip install pyperclip)"}


def set_clipboard(text: str) -> Dict:
    """设置剪贴板内容"""
    try:
        import pyperclip
        pyperclip.copy(text)
        return {"length": len(text), "message": f"已写入剪贴板 ({len(text)} 字符)"}
    except ImportError:
        pass
    try:
        import subprocess
        # 使用 PowerShell 写入剪贴板
        escaped = text.replace('"', '\\"')
        cmd = f'powershell -Command "Set-Clipboard -Value \\"{escaped}\\""'
        subprocess.run(cmd, capture_output=True, timeout=5)
        return {"length": len(text), "method": "powershell", "message": "已写入剪贴板"}
    except Exception:
        pass
    return {"error": "需要安装 pyperclip (pip install pyperclip)"}


def list_windows() -> Dict:
    """列出所有窗口"""
    windows = []
    try:
        import win32gui
        def enum_callback(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title:
                    rect = win32gui.GetWindowRect(hwnd)
                    windows.append({
                        "hwnd": hwnd,
                        "title": title,
                        "rect": {"left": rect[0], "top": rect[1], "right": rect[2], "bottom": rect[3]},
                    })
        win32gui.EnumWindows(enum_callback, None)
        return {"count": len(windows), "windows": windows[:30]}  # 最多返回30个
    except ImportError:
        pass
    # 使用 PowerShell 作为备选
    try:
        import subprocess
        result = subprocess.run(
            ["powershell", "-Command", "Get-Process | Where-Object {$_.MainWindowTitle} | Select-Object -First 30 Name,MainWindowTitle | ConvertTo-Json"],
            capture_output=True, text=True, timeout=10
        )
        if result.stdout.strip():
            import json as _json
            data = _json.loads(result.stdout)
            if isinstance(data, dict):
                data = [data]
            return {"count": len(data), "windows": data}
    except Exception:
        pass
    return {"windows": [], "count": 0, "note": "需要安装 pywin32"}


def activate_window(title: str) -> Dict:
    """激活指定窗口"""
    try:
        import win32gui
        import win32con
        def enum_callback(hwnd, _):
            if win32gui.IsWindowVisible(hwnd) and title.lower() in win32gui.GetWindowText(hwnd).lower():
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(hwnd)
                return False
            return True
        win32gui.EnumWindows(enum_callback, None)
        return {"title": title, "message": f"已激活窗口: {title}"}
    except ImportError:
        return {"error": "需要安装 pywin32", "title": title}


def minimize_window(title: str) -> Dict:
    """最小化窗口"""
    try:
        import win32gui
        import win32con
        def enum_callback(hwnd, _):
            if win32gui.IsWindowVisible(hwnd) and title.lower() in win32gui.GetWindowText(hwnd).lower():
                win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
                return False
            return True
        win32gui.EnumWindows(enum_callback, None)
        return {"title": title, "message": f"已最小化窗口: {title}"}
    except ImportError:
        return {"error": "需要安装 pywin32", "title": title}


def close_window(title: str) -> Dict:
    """关闭窗口"""
    try:
        import win32gui
        import win32con
        def enum_callback(hwnd, _):
            if win32gui.IsWindowVisible(hwnd) and title.lower() in win32gui.GetWindowText(hwnd).lower():
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                return False
            return True
        win32gui.EnumWindows(enum_callback, None)
        return {"title": title, "message": f"已发送关闭请求: {title}"}
    except ImportError:
        return {"error": "需要安装 pywin32", "title": title}