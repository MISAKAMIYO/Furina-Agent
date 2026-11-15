"""
Furina Agent Desktop
=====================
使用 pywebview 将 Web 应用封装为桌面应用程序。

启动方式:
    python desktop.py

功能:
    - 自动启动 Python 后端服务器
    - 加载 Web 前端界面
    - 提供 Python-JS 双向通信桥接
    - 自定义标题栏（毛玻璃风格）
    - 系统托盘支持
    - 跨平台兼容
"""

import os
import sys
import json
import time
import threading
import logging
import subprocess
import socket
import webbrowser
import traceback
from pathlib import Path

# 添加 backend 目录到 Python 路径（源码模式；PyInstaller 冻结模式下已内置，无需添加）
BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backend')
if not getattr(sys, 'frozen', False):
    sys.path.insert(0, BACKEND_DIR)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("ZhiYanDesktop")
# 同时输出到文件（exe 排错用），目录随打包模式解析到用户数据根
try:
    from zhiyan_web.config.settings import PROJECT_ROOT as _D_ROOT
    _log_dir = os.path.join(_D_ROOT, 'logs')
    os.makedirs(_log_dir, exist_ok=True)
    _fh = logging.FileHandler(os.path.join(_log_dir, 'desktop.log'), encoding='utf-8')
    _fh.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", "%H:%M:%S"))
    logger.addHandler(_fh)
except Exception:
    pass

# ============= 配置 =============

class Config:
    """桌面应用配置"""
    # 窗口配置
    WINDOW_TITLE = "Furina Agent"
    WINDOW_WIDTH = 1600
    WINDOW_HEIGHT = 1000
    WINDOW_MIN_WIDTH = 1100
    WINDOW_MIN_HEIGHT = 700
    
    # 服务器配置
    SERVER_HOST = "127.0.0.1"
    SERVER_PORT = None  # 自动选择空闲端口
    SERVER_START_TIMEOUT = 60  # 服务器启动超时（秒），插件较多时可能需要更长时间
    
    # 资源路径（冻结感知：源码/打包双模式统一从 zhiyan_web.config 读取）
    try:
        from zhiyan_web.config.settings import PROJECT_ROOT as _WEB_ROOT, FRONTEND_DIR as WEB_FRONTEND_DIR
        PROJECT_ROOT = _WEB_ROOT
        FRONTEND_DIR = WEB_FRONTEND_DIR
    except Exception:
        PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
        FRONTEND_DIR = os.path.join(PROJECT_ROOT, 'frontend')
    # 窗口/任务栏图标：内置生成的多尺寸 zhiyan.ico
    # （已复制到 frontend/static/assets，源码与打包双模式路径一致）
    ICON_PATH = os.path.join(FRONTEND_DIR, 'static', 'assets', 'zhiyan.ico')
    
    # 调试模式
    DEBUG = False
    DEV_TOOLS = False
    
    # 主题颜色
    PRIMARY_COLOR = "#3b82f6"
    PRIMARY_HOVER = "#2563eb"
    DANGER_COLOR = "#ef4444"
    DANGER_HOVER = "#dc2626"
    BG_COLOR = "#f8fafc"
    TEXT_COLOR = "#1e293b"
    TEXT_MUTED = "#64748b"
    BORDER_COLOR = "#e2e8f0"


# ============= 服务器管理 =============

# uvicorn 安全日志配置：不使用 "uvicorn.logging.*" 类名字符串，
# 避免 PyInstaller 冻结环境下 logging.config.dictConfig 解析失败。
_SAFE_LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {"format": "%(levelname)s: %(message)s"},
        "access": {"format": "%(asctime)s - %(levelname)s - %(message)s"},
    },
    "handlers": {
        "default": {
            "class": "logging.StreamHandler",
            "formatter": "default",
            "stream": "ext://sys.stderr",
        },
        "access": {
            "class": "logging.StreamHandler",
            "formatter": "access",
            "stream": "ext://sys.stderr",
        },
    },
    "loggers": {
        "uvicorn.error": {"level": "WARNING", "handlers": ["default"], "propagate": False},
        "uvicorn.access": {"level": "WARNING", "handlers": ["access"], "propagate": False},
    },
}

class ServerManager:
    """管理后端服务器生命周期"""
    
    def __init__(self):
        self.process = None
        self.host = Config.SERVER_HOST
        self.port = Config.SERVER_PORT or self._find_free_port()
        self._error = None  # 记录服务器启动错误
        self._last_error = None  # 保留最后一条错误信息（供对话框使用）
        self._start_time = 0
    
    def _find_free_port(self):
        """查找空闲端口"""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('', 0))
            return s.getsockname()[1]
    
    def start(self):
        """在后台线程中启动服务器
        
        流程:
        1. 先在主线程导入 app（立即捕获导入错误）
        2. 在后台线程运行 uvicorn
        3. 轮询端口确认服务器就绪
        """
        self._start_time = time.time()
        
        # === 第一步：在主线程导入 app 和 uvicorn，立即捕获导入错误 ===
        try:
            from zhiyan_web.app import app
            import uvicorn
            logger.info("后端模块导入成功")

            # Windows 下使用 SelectorEventLoop 避免 proactor 连接重置错误
            # 必须在导入 zhiyan_web.app 之后设置，覆盖 app.py 中的 ProactorEventLoopPolicy
            if sys.platform == 'win32':
                import asyncio
                asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except Exception as e:
            error_msg = f"导入后端模块失败: {e}"
            logger.error(error_msg)
            logger.error(traceback.format_exc())
            self._error = error_msg
            self._last_error = error_msg
            self._show_error("模块导入失败", error_msg)
            return False
        
        # === 第二步：在后台线程启动 uvicorn ===
        def _run():
            try:
                # 确保线程使用正确的事件循环策略
                if sys.platform == 'win32':
                    import asyncio
                    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
                
                logger.info(f"正在启动后端服务器 http://{self.host}:{self.port} ...")
                # 让后端进程/线程知道实际端口（BetterGI 托管用它同步 Webhook 端点）
                os.environ["ZHIYAN_PORT"] = str(self.port)
                uvicorn.run(
                    app,
                    host=self.host,
                    port=self.port,
                    log_level="info" if Config.DEBUG else "warning",
                    access_log=Config.DEBUG,
                    log_config=_SAFE_LOG_CONFIG,   # 规避冻结环境下 uvicorn 格式化类解析失败
                )
            except Exception as e:
                error_msg = f"服务器运行时异常: {e}"
                logger.error(error_msg)
                logger.error(traceback.format_exc())
                self._error = error_msg
                self._last_error = error_msg
        
        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        
        # === 第三步：等待服务器就绪 ===
        return self._wait_for_server()
    
    def _wait_for_server(self, timeout=None):
        """等待服务器完全就绪，轮询端口可用性"""
        if timeout is None:
            timeout = Config.SERVER_START_TIMEOUT
        
        start = time.time()
        poll_interval = 0.3  # 初始轮询间隔（秒）
        max_poll_interval = 2.0  # 最大轮询间隔
        
        while time.time() - start < timeout:
            # 检查是否有错误
            if self._error:
                error_msg = self._error
                self._last_error = error_msg
                self._error = None
                logger.error(f"服务器启动失败: {error_msg}")
                return False
            
            # 尝试连接端口
            try:
                with socket.create_connection((self.host, self.port), timeout=1):
                    elapsed = time.time() - start
                    logger.info(f"后端服务器就绪 http://{self.host}:{self.port} (耗时 {elapsed:.1f}s)")
                    return True
            except (ConnectionRefusedError, socket.timeout, OSError):
                # 渐进式增加轮询间隔，减少空转消耗
                time.sleep(poll_interval)
                poll_interval = min(poll_interval * 1.2, max_poll_interval)
        
        # 超时
        elapsed = time.time() - start
        logger.error(f"服务器启动超时 (已等待 {elapsed:.1f}s)")
        
        # 如果线程已经结束但端口未就绪，说明有未捕获的错误
        if not self._thread.is_alive():
            if self._error:
                error_msg = self._error
                self._error = None
                logger.error(f"服务器线程已退出: {error_msg}")
            else:
                logger.error("服务器线程已意外退出，请检查日志")
        
        return False
    
    def stop(self):
        """停止服务器"""
        # 线程是 daemon 的，会在主进程退出时自动结束
        logger.info("正在关闭服务器...")
    
    def _show_error(self, title, message):
        """显示错误信息（在主线程中调用）"""
        logger.error(f"{title}: {message}")
        # 错误信息会通过 _show_error_dialog 显示


# ============= 原生 API 桥接 =============

def _default_ball_url() -> str:
    """悬浮球默认 URL（前端会传入 http URL；此处仅兜底为本地文件）"""
    try:
        return os.path.join(Config.FRONTEND_DIR, "float_ball.html")
    except Exception:
        return "about:blank"


class NativeBridge:
    """
    Python-JS 双向通信桥接

    通过 webview.window.js_api 暴露给 JavaScript 调用。
    JavaScript 可通过 pywebview.api.methodName() 调用这些方法。
    """

    def __init__(self):
        self._window = None
        self._ball_window = None  # AI 授课：悬浮球置顶小窗
        self._lyrics_window = None  # 桌面歌词浮窗
        self._mini_window = None  # 独立迷你播放器
        self._music_window = None  # 独立音乐窗口（导航离开也不中断播放）

    def set_window(self, window):
        """设置窗口引用（在窗口创建后调用）"""
        self._window = window

    def _get_window(self):
        """获取窗口实例"""
        if self._window is not None:
            return self._window
        try:
            import webview
            return webview.windows[0]
        except Exception:
            return None

    # ---------- 系统信息 ----------

    def get_system_info(self):
        """获取系统信息"""
        import platform
        return {
            "platform": platform.system(),
            "platform_version": platform.version(),
            "python_version": platform.python_version(),
            "hostname": platform.node(),
        }

    # ---------- 文件操作 ----------

    def open_file_dialog(self, file_types=None):
        """打开文件选择对话框"""
        try:
            win = self._get_window()
            if win is None:
                return None
            file_types = file_types or [("所有文件", "*.*")]
            result = win.create_file_dialog(
                0,  # OPEN_DIALOG
                allow_multiple=False,
                file_types=file_types,
            )
            return result[0] if result else None
        except Exception as e:
            logger.error(f"文件对话框失败: {e}")
            return None

    def save_file_dialog(self, content, default_filename="download", file_types=None):
        """保存文件对话框"""
        try:
            win = self._get_window()
            if win is None:
                return None
            file_types = file_types or [("所有文件", "*.*")]
            result = win.create_file_dialog(
                1,  # SAVE_DIALOG
                save_filename=default_filename,
                file_types=file_types,
            )
            if result:
                with open(result, 'w', encoding='utf-8') as f:
                    f.write(content)
                return result
            return None
        except Exception as e:
            logger.error(f"保存文件失败: {e}")
            return None

    # ---------- 通知 ----------

    def show_notification(self, title, message):
        """显示系统通知"""
        try:
            win = self._get_window()
            if win:
                win.show_notification(title, message)
        except Exception as e:
            logger.error(f"通知失败: {e}")

    # ---------- 窗口控制 ----------

    def minimize_window(self):
        """最小化窗口"""
        try:
            win = self._get_window()
            if win:
                win.minimize()
        except Exception as e:
            logger.debug(f"最小化失败: {e}")

    def maximize_window(self):
        """最大化窗口"""
        try:
            win = self._get_window()
            if win:
                win.maximize()
        except Exception as e:
            logger.debug(f"最大化失败: {e}")

    def restore_window(self):
        """恢复窗口"""
        try:
            win = self._get_window()
            if win:
                win.restore()
        except Exception as e:
            logger.debug(f"恢复失败: {e}")

    def _hwnd(self):
        """取主窗口的 Win32 句柄（窗口装饰用）"""
        win = self._get_window()
        if win is None:
            return 0
        try:
            native = getattr(win, "native", None)
            raw = getattr(native, "Handle", None)
            if raw:
                return int(raw)
        except Exception:
            pass
        try:
            import ctypes
            return int(ctypes.windll.user32.FindWindowW(None, Config.WINDOW_TITLE) or 0)
        except Exception:
            return 0

    def close_window(self):
        """关闭窗口"""
        try:
            win = self._get_window()
            if win:
                win.destroy()
        except Exception as e:
            logger.debug(f"关闭失败: {e}")

    # ---------- 剪贴板 ----------

    def copy_to_clipboard(self, text):
        """复制文本到剪贴板"""
        try:
            win = self._get_window()
            if win:
                win.clipboard = text
                return True
        except Exception:
            pass
        try:
            import pyperclip
            pyperclip.copy(text)
            return True
        except Exception:
            return False

    def paste_from_clipboard(self):
        """从剪贴板粘贴"""
        try:
            win = self._get_window()
            if win:
                return win.clipboard
        except Exception:
            pass
        try:
            import pyperclip
            return pyperclip.paste()
        except Exception:
            return ""

    # ---------- 窗口置顶 ----------

    def toggle_always_on_top(self):
        """切换窗口置顶"""
        try:
            win = self._get_window()
            if win:
                current = getattr(win, '_on_top', False)
                win.on_top = not current
                win._on_top = not current
                return win.on_top
        except Exception:
            pass
        return False

    # ---------- AI 授课：悬浮球置顶小窗（覆盖在 WPS / 希沃白板之上） ----------

    def _ball_win(self):
        return getattr(self, "_ball_window", None)

    def open_float_ball(self, url="", width=0, height=0):
        """打开（或重新显示）悬浮球小窗。

        独立无边框 + 置顶 + 透明窗口，可浮在 WPS / 希沃白板等其它软件之上，
        用于无导航快捷键课件的快捷跳转（配合 OCR 识别当前页）。

        实现要点（依据 pywebview Windows 后端行为）：
        - 不使用 hidden 参数：Windows 后端里 transparent 与 hidden 语义冲突
          （透明窗口在 on_navigation_start 会主动 form.Show()，抵消 hidden），
          因此窗口在首次点击时惰性创建，创建即可见。
        - 非主窗口的创建会被 pywebview 自动调度到 UI 线程，故在 JS 回调线程中调用是安全的。
        """
        import webview

        existing = list(getattr(webview, "windows", None) or [])
        win = self._ball_win()
        if win is not None and any(win is w for w in existing):
            try:
                win.show()
                try:
                    win.on_top = True
                except Exception:
                    pass
                return {"success": True, "message": "悬浮球已显示"}
            except Exception as e:
                logger.warning(f"显示悬浮球失败，将重新创建: {e}")
                self._ball_window = None

        try:
            w = int(width) if width else 84
            h = int(height) if height else 84
            target = url or _default_ball_url()
            ball = webview.create_window(
                "Furina 悬浮球",
                target,
                width=w,
                height=h,
                frameless=True,
                on_top=True,
                transparent=True,
                resizable=False,
                easy_drag=False,
                js_api=self,
            )
            self._ball_window = ball
            logger.info("悬浮球小窗已创建")
            return {"success": True, "message": "悬浮球已打开"}
        except Exception as e:
            logger.error(f"创建悬浮球窗口失败: {e}", exc_info=True)
            return {"success": False, "message": f"创建悬浮球窗口失败: {e}"}

    def open_lyrics_overlay(self, url: str = ""):
        """桌面歌词浮窗：无边框 + 置顶 + 透明背景，只读展示当前歌词"""
        try:
            import webview
        except Exception as e:
            return {"success": False, "message": f"桌面壳不可用: {e}"}

        if self._lyrics_window is not None:
            try:
                self._lyrics_window.show()
                return {"success": True, "message": "桌面歌词已在运行"}
            except Exception:
                self._lyrics_window = None

        target = url or "about:blank"
        try:
            win = webview.create_window(
                "Furina 桌面歌词",
                target,
                width=920,
                height=170,
                frameless=True,
                on_top=True,
                transparent=True,
                resizable=True,
                easy_drag=False,
                js_api=self,
            )
            self._lyrics_window = win
            logger.info("桌面歌词浮窗已创建")
            return {"success": True, "message": "桌面歌词已打开"}
        except Exception as e:
            logger.error(f"创建桌面歌词窗口失败: {e}", exc_info=True)
            return {"success": False, "message": f"创建桌面歌词窗口失败: {e}"}

    def restart_as_admin(self, dry_run: bool = False):
        """以管理员身份重启本程序（桌面壳按钮用）"""
        try:
            from zhiyan_web.system.elevation import restart_as_admin as _restart
        except Exception as e:
            return {"ok": False, "message": "提权模块不可用: %s" % e}
        result = _restart(dry_run=bool(dry_run))
        logger.info("restart_as_admin: %s", (result or {}).get("message"))
        return result

    def open_music_window(self, url: str = ""):
        """独立音乐窗口：主窗口跳去别的页面（如 DSH 内核）时播放不中断"""
        try:
            import webview
        except Exception as e:
            return {"success": False, "message": f"桌面壳不可用: {e}"}

        if self._music_window is not None:
            try:
                self._music_window.show()
                self._music_window.restore()
                return {"success": True, "message": "音乐窗口已在运行"}
            except Exception:
                self._music_window = None

        target = url or "about:blank"
        sep = "&" if "?" in target else "?"
        try:
            win = webview.create_window(
                "Furina 音乐",
                target + sep + "window=1",
                width=1320,
                height=840,
                min_size=(900, 600),
                resizable=True,
                js_api=self,
            )
            self._music_window = win
            logger.info("独立音乐窗口已创建")
            return {"success": True, "message": "音乐窗口已打开"}
        except Exception as e:
            logger.error(f"创建音乐窗口失败: {e}", exc_info=True)
            return {"success": False, "message": f"创建音乐窗口失败: {e}"}

    def close_music_window(self):
        win = getattr(self, "_music_window", None)
        self._music_window = None
        if win is None:
            return {"success": True, "message": "音乐窗口未打开"}
        try:
            win.destroy()
            return {"success": True, "message": "音乐窗口已关闭"}
        except Exception as e:
            return {"success": False, "message": f"关闭失败: {e}"}

    def open_mini_player(self, url: str = ""):
        """独立置顶迷你播放器：小窗 + 置顶 + 无边框，控制命令走后端 /api/music/command"""
        try:
            import webview
        except Exception as e:
            return {"success": False, "message": f"桌面壳不可用: {e}"}

        if self._mini_window is not None:
            try:
                self._mini_window.show()
                return {"success": True, "message": "迷你播放器已在运行"}
            except Exception:
                self._mini_window = None

        target = url or "about:blank"
        try:
            win = webview.create_window(
                "Furina 迷你播放器",
                target,
                width=430,
                height=120,
                frameless=True,
                on_top=True,
                transparent=True,
                resizable=True,
                easy_drag=False,
                js_api=self,
            )
            self._mini_window = win
            logger.info("迷你播放器窗口已创建")
            return {"success": True, "message": "迷你播放器已打开"}
        except Exception as e:
            logger.error(f"创建迷你播放器失败: {e}", exc_info=True)
            return {"success": False, "message": f"创建迷你播放器失败: {e}"}

    def close_mini_player(self):
        win = getattr(self, "_mini_window", None)
        self._mini_window = None
        if win is None:
            return {"success": True, "message": "迷你播放器未打开"}
        try:
            win.destroy()
            return {"success": True, "message": "迷你播放器已关闭"}
        except Exception as e:
            return {"success": False, "message": f"关闭失败: {e}"}

    def close_lyrics_overlay(self):
        """关闭桌面歌词浮窗"""
        win = self._lyrics_window
        self._lyrics_window = None
        if win is None:
            return {"success": True, "message": "没有打开的歌词浮窗"}
        try:
            win.destroy()
            return {"success": True, "message": "桌面歌词已关闭"}
        except Exception as e:
            return {"success": False, "message": f"关闭失败: {e}"}

    def close_float_ball(self):
        """关闭悬浮球小窗（关闭后再次打开会重新创建）"""
        win = self._ball_win()
        self._ball_window = None
        if not win:
            return {"success": True, "message": "悬浮球未打开"}
        try:
            win.destroy()
        except Exception as e:
            logger.debug(f"销毁悬浮球失败: {e}")
        return {"success": True, "message": "悬浮球已关闭"}

    def hide_float_ball(self):
        """隐藏（保留）悬浮球小窗"""
        win = self._ball_win()
        if win:
            try:
                win.hide()
            except Exception:
                pass
        return {"success": True}

    def ball_move(self, x, y):
        """移动悬浮球窗口到屏幕坐标 (x, y)"""
        win = self._ball_win()
        if win:
            try:
                win.move(int(x), int(y))
                return {"success": True, "x": int(x), "y": int(y)}
            except Exception as e:
                return {"success": False, "message": str(e)}
        return {"success": False, "message": "悬浮球未打开"}

    def ball_get_pos(self):
        """获取悬浮球窗口位置"""
        win = self._ball_win()
        if win:
            try:
                return {"success": True, "x": int(getattr(win, "x", 0) or 0), "y": int(getattr(win, "y", 0) or 0)}
            except Exception:
                pass
        return {"success": False, "x": 0, "y": 0}

    def ball_set_size(self, width, height):
        """调整悬浮球窗口尺寸（展开目录面板时用），并保证不超出屏幕"""
        win = self._ball_win()
        if not win:
            return {"success": False, "message": "悬浮球未打开"}
        try:
            w = max(72, int(width))
            h = max(72, int(height))
            win.resize(w, h)
            # 球通常停在屏幕右下角，向右下扩展会跑出屏幕，这里回推位置
            try:
                scr_w, scr_h = self._screen_size()
                x = int(getattr(win, "x", 0) or 0)
                y = int(getattr(win, "y", 0) or 0)
                nx = min(max(0, x), max(0, scr_w - w))
                ny = min(max(0, y), max(0, scr_h - h))
                if (nx, ny) != (x, y):
                    win.move(nx, ny)
            except Exception:
                pass
            return {"success": True}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def ball_screen_size(self):
        """获取主屏可用分辨率（悬浮球边缘吸附用）"""
        try:
            w, h = self._screen_size()
            # 扣除任务栏，取可用区域更贴近视觉贴边效果
            try:
                import ctypes
                user32 = ctypes.windll.user32
                avail_w = int(user32.GetSystemMetrics(0))
                avail_h = int(user32.GetSystemMetrics(1))
                if avail_w > 0 and avail_h > 0:
                    return {"success": True, "width": avail_w, "height": avail_h}
            except Exception:
                pass
            return {"success": True, "width": w, "height": h}
        except Exception as e:
            return {"success": False, "width": 1920, "height": 1080, "message": str(e)}

    @staticmethod
    def _screen_size():
        """获取主屏分辨率（用于窗口边界修正）"""
        try:
            import pyautogui
            size = pyautogui.size()
            return int(size.width), int(size.height)
        except Exception:
            pass
        try:
            import webview
            screens = getattr(webview, "screens", None) or []
            if screens:
                return int(screens[0].width), int(screens[0].height)
        except Exception:
            pass
        return 1920, 1080

    def open_external(self, url):
        """用系统默认浏览器打开 URL（HTML 幻灯片全屏放映 / 导出文件）"""
        try:
            webbrowser.open(url)
            return {"success": True}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def toggle_main_top(self):
        """切换主窗口置顶"""
        return {"success": True, "on_top": bool(self.toggle_always_on_top())}


# ============= 自定义标题栏注入脚本 =============

TITLEBAR_INJECT_JS = """
// 窗体样式与交互逻辑在前端 static/js/desktop-chrome.js，这里只做兜底触发。
if (window.mountDesktopChrome) { window.mountDesktopChrome(); }
"""


# ============= 加载动画 HTML =============

LOADING_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Microsoft YaHei', 'Segoe UI', sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            display: flex;
            align-items: center;
            justify-content: center;
            height: 100vh;
            overflow: hidden;
        }
        
        .loading-container {
            text-align: center;
            color: white;
        }
        
        .logo {
            width: 80px;
            height: 80px;
            margin: 0 auto 30px;
            background: rgba(255, 255, 255, 0.2);
            backdrop-filter: blur(10px);
            border-radius: 20px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 36px;
            font-weight: bold;
            animation: float 3s ease-in-out infinite;
        }
        
        @keyframes float {
            0%, 100% { transform: translateY(0px); }
            50% { transform: translateY(-10px); }
        }
        
        .title {
            font-size: 28px;
            font-weight: 600;
            margin-bottom: 10px;
            letter-spacing: 1px;
        }
        
        .subtitle {
            font-size: 14px;
            opacity: 0.9;
            margin-bottom: 40px;
        }
        
        .spinner {
            width: 50px;
            height: 50px;
            margin: 0 auto;
            border: 3px solid rgba(255, 255, 255, 0.3);
            border-top-color: white;
            border-radius: 50%;
            animation: spin 1s linear infinite;
        }
        
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        
        .status {
            margin-top: 30px;
            font-size: 13px;
            opacity: 0.8;
        }
    </style>
</head>
<body>
    <div class="loading-container">
        <div class="logo">M</div>
        <div class="title">Furina Agent</div>
        <div class="subtitle">正在启动服务...</div>
        <div class="spinner"></div>
        <div class="status" id="status">初始化中</div>
    </div>
    
    <script>
        const statusEl = document.getElementById('status');
        const messages = ['初始化中', '加载配置', '启动服务', '准备就绪'];
        let index = 0;
        
        setInterval(() => {
            index = (index + 1) % messages.length;
            statusEl.textContent = messages[index];
        }, 1500);
    </script>
</body>
</html>
"""


# ============= 桌面应用 =============

def _is_win7():
    """判断当前是否为 Windows 7（Win7 需额外依赖 WebView2 运行时）"""
    try:
        v = sys.getwindowsversion()
        return (v.major, v.minor) == (6, 1)
    except Exception:
        return False


def _is_webview2_installed():
    """检测 Microsoft Edge WebView2 运行时是否已安装（pywebview 的 edgechromium 后端依赖它）"""
    if sys.platform != "win32":
        return True
    try:
        import winreg
    except Exception:
        return True
    key_path = r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ | view) as k:
                    pv, _ = winreg.QueryValueEx(k, "pv")
                    if pv and pv != "0.0.0.0":
                        return True
            except OSError:
                continue
    return False


class ZhiYanDesktopApp:
    """Furina Agent 桌面应用主类"""
    
    def __init__(self):
        self.server = None
        self.bridge = NativeBridge()
        self._tray_icon = None
    
    def run(self):
        """启动桌面应用"""
        # 1. 启动后端服务器
        self.server = ServerManager()
        if not self.server.start():
            error_detail = self.server._last_error or "无法启动后端服务器，请检查日志。"
            self._show_error_dialog("服务器启动失败", error_detail)
            return
        
        # 2. 构建 URL
        base_url = f"http://{self.server.host}:{self.server.port}"
        logger.info(f"打开桌面窗口: {base_url}")
        
        # 3. 创建并启动 webview 窗口
        self._create_window(base_url)
    
    def _apply_window_icon(self, window):
        """为桌面窗口设置封面图标（pywebview 5.1 不支持 create_window 的 icon 参数，
        故通过 Win32 WM_SETICON 在窗口创建后设置任务栏/标题栏图标）。"""
        if not os.path.exists(Config.ICON_PATH):
            logger.warning(f"窗口图标不存在，跳过: {Config.ICON_PATH}")
            return
        try:
            import ctypes
            from ctypes import wintypes
            WM_SETICON = 0x0080
            ICON_SMALL, ICON_BIG = 0, 1
            IMAGE_ICON = 1
            LR_LOADFROMFILE = 0x00000010
            # 通过 pywebview 的原生窗口句柄（Windows: winforms/WPF Form.Handle / cef 窗口句柄）
            hwnd = None
            native = getattr(window, "native", None)
            try:
                raw = getattr(native, "Handle", None)
                hwnd = int(raw) if raw else None
            except Exception:
                hwnd = None
            if not hwnd:
                # 回退1：找到前台窗口（页面加载完成时窗口在前台）
                hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                # 回退2：按窗口标题查找
                hwnd = ctypes.windll.user32.FindWindowW(None, Config.WINDOW_TITLE)
            if not hwnd:
                logger.warning("窗口句柄获取失败，无法设置图标")
                return
            hicon_small = ctypes.windll.user32.LoadImageW(None, Config.ICON_PATH, IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
            hicon_big = ctypes.windll.user32.LoadImageW(None, Config.ICON_PATH, IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
            if hicon_small:
                ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon_small)
            if hicon_big:
                ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon_big)
            logger.info("已应用封面窗口图标")
        except Exception as e:
            logger.warning(f"设置窗口图标失败: {e}")

    def _apply_window_frame(self, window):
        """去掉系统绘制的外框线，并在 Win11 上启用圆角（Win10 自动忽略）。"""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            hwnd = self.bridge._hwnd()
            if not hwnd:
                return
            dwm = ctypes.windll.dwmapi
            # DWMWA_WINDOW_CORNER_PREFERENCE = 33, DWMWCP_ROUND = 2
            pref = ctypes.c_int(2)
            dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
            # DWMWA_BORDER_COLOR = 34, DWMWA_COLOR_NONE = 0xFFFFFFFE
            color = ctypes.c_uint(0xFFFFFFFE)
            dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(color), ctypes.sizeof(color))
            logger.info("已应用无边框圆角窗口装饰")
        except Exception as e:
            logger.debug(f"窗口装饰应用失败: {e}")

    def _create_window(self, base_url):
        """创建 webview 窗口"""
        import webview

        if _is_win7() and not _is_webview2_installed():
            logger.warning(
                "Windows 7 未检测到 WebView2 运行时，桌面窗口可能无法创建；"
                "请安装最后支持 Win7 的 WebView2 109.x 版本。"
            )
        # 窗口配置
        window_settings = {
            "title": Config.WINDOW_TITLE,
            "url": base_url,
            "width": Config.WINDOW_WIDTH,
            "height": Config.WINDOW_HEIGHT,
            "resizable": True,
            "fullscreen": False,
            "min_size": (Config.WINDOW_MIN_WIDTH, Config.WINDOW_MIN_HEIGHT),
            "js_api": self.bridge,
            "text_select": True,
            "frameless": True,
            # 关闭 pywebview 的"整窗可拖"：默认 easy_drag=True 会在 window 上注册
            # 全局 mousedown，导致拖动画布里的挂件也会把整个窗口带走。
            # 改为只由自定义标题栏调用原生拖动。
            "easy_drag": False,
            "background_color": "#F2F6FC",
        }

        # 调试模式
        if Config.DEV_TOOLS:
            window_settings["debug"] = True

        try:
            window = webview.create_window(**window_settings)

            # 设置窗口引用到 bridge
            self.bridge.set_window(window)

            # 窗口关闭事件 - 清理资源
            def on_closing():
                logger.info("窗口正在关闭...")
                self._cleanup()

            window.events.closing += on_closing

            # 加载完成事件 - 注入自定义标题栏
            def on_loaded():
                logger.info("页面加载完成")
                # 设置窗口图标（封面）与无边框圆角装饰
                self._apply_window_icon(window)
                self._apply_window_frame(window)
                try:
                    window.evaluate_js(TITLEBAR_INJECT_JS)
                    _mounted = window.evaluate_js("!!document.getElementById('custom-titlebar')")
                    logger.info(f"桌面窗体挂载: {_mounted}")
                except Exception as e:
                    logger.warning(f"注入标题栏失败: {e}")

            window.events.loaded += on_loaded

            webview.start(
                private_mode=False,
                storage_path=os.path.join(Config.PROJECT_ROOT, '.webview_cache'),
                gui='cef' if Config.DEV_TOOLS else None,
            )
        except Exception as e:
            logger.error(f"窗口创建失败: {e}")
            message = str(e)
            if _is_win7() and not _is_webview2_installed():
                message = (
                    "Windows 7 缺少 Microsoft Edge WebView2 运行时，无法创建桌面窗口。\n"
                    "请安装最后支持 Win7 的 WebView2 109.x 版本后重试：\n"
                    "https://developer.microsoft.com/zh-cn/microsoft-edge/webview2/\n\n"
                    f"原始错误：{e}"
                )
            self._show_error_dialog("窗口创建失败", message)
            self._cleanup()
    
    def _cleanup(self):
        """清理资源"""
        if self.server:
            self.server.stop()
        logger.info("桌面应用已退出")
    
    def _show_error_dialog(self, title, message):
        """显示错误对话框"""
        error_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                * {{
                    margin: 0;
                    padding: 0;
                    box-sizing: border-box;
                }}
                
                body {{
                    font-family: 'Microsoft YaHei', 'Segoe UI', sans-serif;
                    background: linear-gradient(135deg, #fee2e2 0%, #fecaca 100%);
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    height: 100vh;
                    overflow: hidden;
                }}
                
                .error-container {{
                    background: white;
                    border-radius: 16px;
                    padding: 40px;
                    box-shadow: 0 20px 60px rgba(0, 0, 0, 0.15);
                    text-align: center;
                    max-width: 400px;
                }}
                
                .error-icon {{
                    width: 64px;
                    height: 64px;
                    margin: 0 auto 20px;
                    background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%);
                    border-radius: 50%;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    font-size: 32px;
                }}
                
                .error-title {{
                    font-size: 20px;
                    font-weight: 600;
                    color: #1e293b;
                    margin-bottom: 12px;
                }}
                
                .error-message {{
                    font-size: 14px;
                    color: #64748b;
                    line-height: 1.6;
                    margin-bottom: 24px;
                }}
                
                .error-hint {{
                    font-size: 12px;
                    color: #94a3b8;
                    padding: 12px;
                    background: #f8fafc;
                    border-radius: 8px;
                }}
            </style>
        </head>
        <body>
            <div class="error-container">
                <div class="error-icon">⚠️</div>
                <div class="error-title">{title}</div>
                <div class="error-message">{message}</div>
                <div class="error-hint">请查看控制台日志获取详细信息</div>
            </div>
        </body>
        </html>
        """
        
        try:
            import webview
            webview.create_window(
                title,
                html=error_html,
                width=500,
                height=350,
                resizable=False,
            )
            webview.start()
        except Exception:
            # 最后回退到终端输出；Windows 下再弹原生对话框（WebView2 缺失时也能看到错误）
            print(f"错误: {title}\n{message}")
            if sys.platform == "win32":
                try:
                    import ctypes
                    ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)
                except Exception:
                    pass


# ============= 入口 =============

def main():
    """主入口"""
    # 解析命令行参数
    if "--debug" in sys.argv:
        Config.DEBUG = True
        Config.DEV_TOOLS = True
        logger.setLevel(logging.DEBUG)
    
    if "--port" in sys.argv:
        idx = sys.argv.index("--port")
        if idx + 1 < len(sys.argv):
            Config.SERVER_PORT = int(sys.argv[idx + 1])
    
    if "--help" in sys.argv:
        print("""Furina Agent Desktop - 使用说明
用法:
    python desktop.py [选项]

选项:
    --debug         启用调试模式（打开开发者工具）
    --port PORT     指定后端服务器端口（默认自动选择）
    --help          显示此帮助信息
        """)
        return
    
    # 启动桌面应用
    app = ZhiYanDesktopApp()
    try:
        app.run()
    except KeyboardInterrupt:
        logger.info("用户中断")
    except Exception as e:
        logger.error(f"应用异常: {e}", exc_info=Config.DEBUG)


if __name__ == "__main__":
    main()
