"""
截屏与录屏服务
================
提供屏幕截图（全屏 / 活动窗口 / 区域裁剪）、录屏（FFmpeg gdigrab）、
历史记录管理与全局热键事件处理。

依赖:
- Pillow（截图）
- FFmpeg（录屏，优先读取 settings.json 的 ffmpeg_path，其次 data/ffmpeg.exe）
- pywin32（可选，用于活动窗口截图与虚拟屏幕范围）
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from zhiyan_web.config import DATA_DIR
from zhiyan_web.config.settings import get_settings, update_settings

logger = logging.getLogger("ZhiYanWeb.Screencap")

# 默认截屏录屏配置（存储于 settings.json 的 screencap 键）
DEFAULT_SCREENCAP_SETTINGS: Dict[str, Any] = {
    "output_dir": "",                 # 自定义输出目录，留空 = DATA_DIR/captures
    "screenshot_format": "png",       # png / jpg
    "record_fps": 20,                 # 录屏帧率
    "record_quality": "veryfast",     # x264 preset: ultrafast/superfast/veryfast/faster/fast/medium
    "record_audio": False,            # 录屏是否同时录制系统声音（需虚拟音频设备）
    "hotkeys_enabled": True,          # 是否启用全局热键
    "hotkeys": {
        "screenshot": "Ctrl+Shift+A",       # 全屏截图
        "screenshot_window": "Ctrl+Shift+W",# 活动窗口截图
        "recording": "Ctrl+Alt+R",          # 开始/停止录屏
    },
}

_HAS_PIL = False
try:
    from PIL import Image, ImageGrab
    _HAS_PIL = True
except ImportError:
    logger.warning("Pillow 不可用，截图功能将回退到 pyautogui / PowerShell")

_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# ────────── 路径与目录 ──────────

def get_capture_root() -> str:
    """获取截图/录屏输出根目录"""
    cfg = get_screencap_settings()
    out = (cfg.get("output_dir") or "").strip()
    root = os.path.abspath(out) if out else os.path.join(DATA_DIR, "captures")
    os.makedirs(root, exist_ok=True)
    return root


def get_screenshots_dir() -> str:
    d = os.path.join(get_capture_root(), "screenshots")
    os.makedirs(d, exist_ok=True)
    return d


def get_videos_dir() -> str:
    d = os.path.join(get_capture_root(), "videos")
    os.makedirs(d, exist_ok=True)
    return d


def _ensure_dirs() -> None:
    get_screenshots_dir()
    get_videos_dir()


def _new_filename(prefix: str, ext: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{ts}.{ext}"


# ────────── 屏幕信息 ──────────

def _get_virtual_screen() -> Dict[str, int]:
    """获取虚拟屏幕范围 {x, y, width, height}（多显示器合并范围）"""
    try:
        import win32api
        import win32con
        return {
            "x": win32api.GetSystemMetrics(win32con.SM_XVIRTUALSCREEN),
            "y": win32api.GetSystemMetrics(win32con.SM_YVIRTUALSCREEN),
            "width": win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN),
            "height": win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN),
        }
    except Exception:
        pass
    try:
        img = ImageGrab.grab()
        return {"x": 0, "y": 0, "width": img.width, "height": img.height}
    except Exception:
        return {"x": 0, "y": 0, "width": 1920, "height": 1080}


def get_screen_size() -> Dict[str, int]:
    """获取主屏幕分辨率"""
    try:
        import pyautogui
        w, h = pyautogui.size()
        return {"width": int(w), "height": int(h)}
    except Exception:
        pass
    vs = _get_virtual_screen()
    return {"width": vs["width"], "height": vs["height"]}


def _get_foreground_window_rect(window_title: Optional[str] = None) -> Optional[tuple]:
    """获取窗口在屏幕上的矩形区域 (left, top, right, bottom)"""
    try:
        import win32gui
        import win32con
        if window_title:
            hwnd = win32gui.FindWindow(None, window_title)
        else:
            hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.2)
        rect = win32gui.GetWindowRect(hwnd)
        left, top, right, bottom = rect
        if right - left <= 0 or bottom - top <= 0:
            return None
        return (left, top, right, bottom)
    except Exception:
        return None


# ────────── 截图 ──────────

def take_screenshot(
    mode: str = "full",
    region: Optional[Dict[str, int]] = None,
    window_title: Optional[str] = None,
) -> Dict[str, Any]:
    """截取屏幕

    Args:
        mode: full 全屏 / window 活动窗口 / region 指定区域
        region: {"left": x, "top": y, "width": w, "height": h}（mode=region 时）
        window_title: mode=window 时指定的窗口标题（留空取前台窗口）

    Returns:
        Dict: {success, path, url, width, height, size, message}
    """
    _ensure_dirs()
    cfg = get_screencap_settings()
    fmt = (cfg.get("screenshot_format") or "png").lower()
    if fmt not in ("png", "jpg", "jpeg"):
        fmt = "png"
    ext = "png" if fmt == "png" else "jpg"
    save_path = os.path.join(get_screenshots_dir(), _new_filename("shot", ext))

    image = None
    if _HAS_PIL:
        try:
            if mode == "window":
                bbox = _get_foreground_window_rect(window_title)
                if bbox:
                    image = ImageGrab.grab(bbox=bbox)
                else:
                    image = ImageGrab.grab(all_screens=True)
            elif mode == "region" and region:
                r = region
                left = int(r.get("left", 0))
                top = int(r.get("top", 0))
                w = max(1, int(r.get("width", 0)))
                h = max(1, int(r.get("height", 0)))
                image = ImageGrab.grab(bbox=(left, top, left + w, top + h))
            else:
                image = ImageGrab.grab(all_screens=True)
        except Exception as e:
            logger.error(f"PIL 截图失败: {e}")
            image = None

    if image is not None:
        image.save(save_path)
    else:
        # 回退 1: pyautogui
        try:
            import pyautogui
            pyautogui.screenshot().save(save_path)
        except Exception:
            # 回退 2: PowerShell（仅全屏）
            if not _ps_screenshot(save_path):
                return {"success": False, "error": "截图失败：Pillow / pyautogui / PowerShell 均不可用"}

    if not os.path.exists(save_path):
        return {"success": False, "error": "截图失败：未生成文件"}

    size = os.path.getsize(save_path)
    width = height = None
    try:
        with Image.open(save_path) as im:
            width, height = im.size
    except Exception:
        pass

    url = f"/api/screencap/file/screenshots/{os.path.basename(save_path)}"
    result = {
        "success": True,
        "mode": mode,
        "path": save_path,
        "url": url,
        "size": size,
        "width": width,
        "height": height,
        "message": f"截图已保存: {save_path}",
    }
    # 自动打开所在文件夹（由前端控制，不强制弹出系统窗口）
    logger.info(f"截图完成: {save_path}")
    return result


def _ps_screenshot(save_path: str) -> bool:
    """使用 PowerShell 截取全屏（备用方案）"""
    try:
        ps_script = f'''
        Add-Type -AssemblyName System.Windows.Forms
        $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
        $bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.CopyFromScreen($b.X, $b.Y, 0, 0, $b.Size)
        $bmp.Save("{save_path}", [System.Drawing.Imaging.ImageFormat]::Png)
        $g.Dispose(); $bmp.Dispose()
        '''
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, timeout=30,
            creationflags=_CREATE_NO_WINDOW,
        )
        return os.path.exists(save_path)
    except Exception as e:
        logger.warning(f"PowerShell 截图失败: {e}")
        return False


def crop_capture(source_path: str, region: Dict[str, int]) -> Dict[str, Any]:
    """对已截取的图片进行区域裁剪并另存为新文件

    Args:
        source_path: 源截图路径
        region: {"left", "top", "width", "height"}（相对源图）

    Returns:
        Dict: {success, path, url, width, height, size}
    """
    if not _HAS_PIL:
        return {"success": False, "error": "裁剪需要 Pillow 支持"}
    if not os.path.exists(source_path):
        return {"success": False, "error": f"源文件不存在: {source_path}"}

    _ensure_dirs()
    cfg = get_screencap_settings()
    fmt = (cfg.get("screenshot_format") or "png").lower()
    ext = "png" if fmt == "png" else "jpg"

    try:
        with Image.open(source_path) as img:
            src_w, src_h = img.size
            left = max(0, int(region.get("left", 0)))
            top = max(0, int(region.get("top", 0)))
            w = min(int(region.get("width", src_w)), src_w - left)
            h = min(int(region.get("height", src_h)), src_h - top)
            if w <= 0 or h <= 0:
                return {"success": False, "error": "裁剪区域无效"}
            box = (left, top, left + w, top + h)
            cropped = img.crop(box)

            save_path = os.path.join(get_screenshots_dir(), _new_filename("crop", ext))
            if ext == "jpg":
                cropped = cropped.convert("RGB")
            cropped.save(save_path)
    except Exception as e:
        logger.error(f"裁剪失败: {e}")
        return {"success": False, "error": f"裁剪失败: {e}"}

    size = os.path.getsize(save_path)
    with Image.open(save_path) as im:
        width, height = im.size
    return {
        "success": True,
        "path": save_path,
        "url": f"/api/screencap/file/screenshots/{os.path.basename(save_path)}",
        "width": width,
        "height": height,
        "size": size,
        "message": f"裁剪已保存: {save_path}",
    }


# ────────── 录屏（FFmpeg gdigrab） ──────────

_rec_state: Dict[str, Any] = {
    "running": False,
    "process": None,
    "started_at": None,
    "output_path": None,
    "fps": 20,
    "audio": False,
    "quality": "veryfast",
}
_rec_lock = threading.Lock()


def _get_ffmpeg_path() -> str:
    """获取 ffmpeg 可执行文件路径"""
    try:
        custom = get_settings().get("ffmpeg_path", "")
        if custom and os.path.exists(custom):
            return custom
    except Exception:
        pass
    default_path = os.path.join(DATA_DIR, "ffmpeg.exe")
    if os.path.exists(default_path):
        return default_path
    return "ffmpeg"


def ffmpeg_available() -> bool:
    """检查 ffmpeg 是否可用"""
    ffmpeg_path = _get_ffmpeg_path()
    if os.path.isabs(ffmpeg_path) and not os.path.exists(ffmpeg_path):
        return False
    try:
        result = subprocess.run(
            [ffmpeg_path, "-version"],
            capture_output=True, timeout=5,
            creationflags=_CREATE_NO_WINDOW,
        )
        return result.returncode == 0
    except Exception:
        return False


def _build_ffmpeg_cmd(fps: int, quality: str, audio: bool, output_path: str) -> List[str]:
    vs = _get_virtual_screen()
    cmd = [
        _get_ffmpeg_path(), "-y",
        "-f", "gdigrab",
        "-framerate", str(fps),
        "-offset_x", str(vs["x"]),
        "-offset_y", str(vs["y"]),
        "-video_size", f"{vs['width']}x{vs['height']}",
        "-i", "desktop",
    ]
    if audio:
        # 尝试录制系统声音（虚拟音频设备），失败会由调用方回退为纯视频
        cmd += ["-f", "dshow", "-i", 'audio="virtual-audio-capturer"']
    cmd += [
        "-c:v", "libx264",
        "-preset", quality,
        "-pix_fmt", "yuv420p",
        "-crf", "23",
    ]
    if audio:
        cmd += ["-c:a", "aac", "-b:a", "128k", "-shortest"]
    cmd.append(output_path)
    return cmd


def start_recording(
    fps: Optional[int] = None,
    audio: Optional[bool] = None,
    quality: Optional[str] = None,
) -> Dict[str, Any]:
    """开始录屏（FFmpeg gdigrab 全屏录制）"""
    with _rec_lock:
        if _rec_state["running"]:
            return {
                "success": True,
                "already": True,
                "running": True,
                "output_path": _rec_state["output_path"],
                "message": "已在录制中",
            }

        if not ffmpeg_available():
            return {
                "success": False,
                "error": "FFmpeg 不可用，无法录屏。请将 ffmpeg.exe 放到 data 目录或在设置中配置 ffmpeg_path。",
            }

        cfg = get_screencap_settings()
        fps = int(fps if fps is not None else cfg.get("record_fps", 20) or 20)
        audio = bool(audio if audio is not None else cfg.get("record_audio", False))
        quality = (quality or cfg.get("record_quality", "veryfast") or "veryfast").strip().lower()

        _ensure_dirs()
        output_path = os.path.join(get_videos_dir(), _new_filename("rec", "mp4"))

        cmd = _build_ffmpeg_cmd(fps, quality, audio, output_path)
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_CREATE_NO_WINDOW,
            )
        except Exception as e:
            return {"success": False, "error": f"启动 FFmpeg 失败: {e}"}

        # 若启用音频但启动后快速退出（设备不存在等），自动回退为纯视频录制
        if audio and proc.poll() is not None:
            logger.warning("音频设备不可用，回退为纯视频录制")
            cmd2 = _build_ffmpeg_cmd(fps, quality, False, output_path)
            try:
                proc = subprocess.Popen(
                    cmd2,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=_CREATE_NO_WINDOW,
                )
                audio = False
            except Exception as e:
                return {"success": False, "error": f"启动 FFmpeg 失败: {e}"}

        _rec_state.update({
            "running": True,
            "process": proc,
            "started_at": time.time(),
            "output_path": output_path,
            "fps": fps,
            "audio": audio,
            "quality": quality,
        })
        logger.info(f"开始录屏: {output_path} ({fps}fps, audio={audio})")
        return {
            "success": True,
            "running": True,
            "output_path": output_path,
            "fps": fps,
            "audio": audio,
            "started_at": _rec_state["started_at"],
            "message": f"开始录屏: {os.path.basename(output_path)}",
        }


def stop_recording() -> Dict[str, Any]:
    """停止录屏并保存文件"""
    with _rec_lock:
        proc = _rec_state.get("process")
        started_at = _rec_state.get("started_at")
        output_path = _rec_state.get("output_path")
        fps = _rec_state.get("fps")
        audio = _rec_state.get("audio")

        if not _rec_state["running"] or proc is None or not output_path:
            return {"success": True, "running": False, "message": "当前未在录制"}

        _rec_state["running"] = False
        _rec_state["process"] = None
        _rec_state["started_at"] = None
        _rec_state["output_path"] = None

        # 向 ffmpeg 发送 'q' 优雅结束
        try:
            if proc.stdin:
                try:
                    proc.stdin.write(b"q")
                    proc.stdin.flush()
                except Exception:
                    pass
        except Exception:
            pass
        try:
            proc.wait(timeout=15)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

        duration = max(0, time.time() - started_at) if started_at else 0
        exists = os.path.exists(output_path)
        size = os.path.getsize(output_path) if exists else 0
        result = {
            "success": exists,
            "running": False,
            "path": output_path if exists else None,
            "url": f"/api/screencap/file/videos/{os.path.basename(output_path)}" if exists else None,
            "size": size,
            "duration": round(duration, 1),
            "fps": fps,
            "audio": audio,
            "message": f"录屏已保存: {output_path}" if exists else "录屏失败：未生成视频文件",
        }
        if exists:
            _make_video_thumb(output_path)
        logger.info(f"录屏结束: {output_path} 时长 {duration:.1f}s")
        return result


def get_recording_status() -> Dict[str, Any]:
    """获取当前录屏状态"""
    with _rec_lock:
        if not _rec_state["running"]:
            return {
                "success": True,
                "running": False,
                "ffmpeg_available": ffmpeg_available(),
                "elapsed": 0,
                "message": "未在录制",
            }
        elapsed = round(time.time() - _rec_state["started_at"], 1) if _rec_state["started_at"] else 0
        return {
            "success": True,
            "running": True,
            "ffmpeg_available": True,
            "elapsed": elapsed,
            "started_at": _rec_state["started_at"],
            "output_path": _rec_state["output_path"],
            "fps": _rec_state["fps"],
            "audio": _rec_state["audio"],
            "quality": _rec_state["quality"],
            "message": "录制中",
        }


def _make_video_thumb(video_path: str) -> Optional[str]:
    """为视频生成缩略图（ffmpeg 抽取首帧）"""
    if not ffmpeg_available():
        return None
    thumb_path = video_path + ".thumb.jpg"
    try:
        cmd = [
            _get_ffmpeg_path(), "-y",
            "-ss", "0.5", "-i", video_path,
            "-frames:v", "1",
            "-vf", "scale=320:-1",
            thumb_path,
        ]
        subprocess.run(
            cmd, capture_output=True, timeout=20,
            creationflags=_CREATE_NO_WINDOW,
        )
        return thumb_path if os.path.exists(thumb_path) else None
    except Exception:
        return None


# ────────── 历史记录 ──────────

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
_VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".gif"}


def _scan_dir(directory: str) -> List[Dict[str, Any]]:
    items = []
    if not os.path.isdir(directory):
        return items
    for name in os.listdir(directory):
        # 跳过缩略图与隐藏文件
        if name.startswith(".") or name.endswith(".thumb.jpg"):
            continue
        full = os.path.join(directory, name)
        if not os.path.isfile(full):
            continue
        ext = os.path.splitext(name)[1].lower()
        sub = os.path.basename(directory)  # screenshots / videos
        kind = "video" if sub == "videos" else "screenshot"
        if ext not in (_IMAGE_EXTS if kind == "screenshot" else _VIDEO_EXTS):
            continue
        try:
            mtime = os.path.getmtime(full)
            size = os.path.getsize(full)
        except OSError:
            continue
        thumb_url = None
        if kind == "video":
            thumb = full + ".thumb.jpg"
            if os.path.exists(thumb):
                thumb_url = f"/api/screencap/file/videos/{name}.thumb.jpg"
        items.append({
            "name": name,
            "path": full,
            "kind": kind,
            "size": size,
            "mtime": mtime,
            "time": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "url": f"/api/screencap/file/{sub}/{name}",
            "thumb_url": thumb_url,
        })
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def list_history(
    kind: Optional[str] = None,
    page: int = 1,
    limit: int = 24,
) -> Dict[str, Any]:
    """列出截图/录屏历史记录（按修改时间倒序，支持分页）"""
    _ensure_dirs()
    items: List[Dict[str, Any]] = []
    if kind in (None, "", "screenshot"):
        items.extend(_scan_dir(get_screenshots_dir()))
    if kind in (None, "", "video"):
        items.extend(_scan_dir(get_videos_dir()))
    items.sort(key=lambda x: x["mtime"], reverse=True)

    total = len(items)
    page = max(1, int(page))
    limit = max(1, min(200, int(limit)))
    start = (page - 1) * limit
    paged = items[start:start + limit]
    return {
        "success": True,
        "items": paged,
        "total": total,
        "page": page,
        "limit": limit,
        "root": get_capture_root(),
        "screenshots_dir": get_screenshots_dir(),
        "videos_dir": get_videos_dir(),
    }


def delete_capture(path: str) -> Dict[str, Any]:
    """删除截图/录屏文件（连同缩略图）"""
    path = os.path.abspath(path)
    root = os.path.abspath(get_capture_root())
    try:
        common = os.path.commonpath([root, path])
    except ValueError:
        common = ""
    if common != root or not os.path.isfile(path):
        return {"success": False, "error": "非法路径，拒绝删除"}

    # 阻止删除正在录制的文件
    if _rec_state["running"] and _rec_state.get("output_path") == path:
        return {"success": False, "error": "该文件正在录制中，请先停止录屏"}

    try:
        os.remove(path)
    except Exception as e:
        return {"success": False, "error": f"删除失败: {e}"}
    # 删除伴随缩略图
    if path.endswith(".mp4"):
        thumb = path + ".thumb.jpg"
        if os.path.exists(thumb):
            try:
                os.remove(thumb)
            except Exception:
                pass
    return {"success": True, "message": "已删除", "path": path}


def open_capture(path: str) -> Dict[str, Any]:
    """打开截图/录屏文件所在位置（文件夹并选中）"""
    if not os.path.exists(path):
        return {"success": False, "error": "文件不存在"}
    try:
        if sys.platform == "win32":
            subprocess.Popen(
                ["explorer", "/select,", os.path.normpath(path)],
                creationflags=_CREATE_NO_WINDOW,
            )
        else:
            os.startfile(os.path.dirname(path))
        return {"success": True, "message": "已打开所在文件夹"}
    except Exception as e:
        return {"success": False, "error": f"打开失败: {e}"}


def open_capture_direct(path: str) -> Dict[str, Any]:
    """直接打开截图/录屏文件"""
    if not os.path.exists(path):
        return {"success": False, "error": "文件不存在"}
    try:
        if sys.platform == "win32":
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", path])
        return {"success": True, "message": "已打开文件"}
    except Exception as e:
        return {"success": False, "error": f"打开失败: {e}"}


def copy_to_clipboard(path: str) -> Dict[str, Any]:
    """将截图复制到剪贴板（PowerShell STA）"""
    if not os.path.exists(path):
        return {"success": False, "error": "文件不存在"}
    ps_script = f'''
    Add-Type -AssemblyName System.Windows.Forms
    $img = [System.Drawing.Image]::FromFile("{path}")
    [System.Windows.Forms.Clipboard]::SetImage($img)
    $img.Dispose()
    '''
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", ps_script],
            capture_output=True, timeout=15,
            creationflags=_CREATE_NO_WINDOW,
        )
        return {"success": True, "message": "已复制到剪贴板"}
    except Exception as e:
        return {"success": False, "error": f"复制失败: {e}"}


# ────────── 配置 ──────────

def get_screencap_settings() -> Dict[str, Any]:
    """获取截屏录屏配置（合并默认值）"""
    cfg = get_settings().get("screencap") or {}
    merged = copy.deepcopy(DEFAULT_SCREENCAP_SETTINGS)
    merged.update(cfg)
    merged["hotkeys"] = {
        **DEFAULT_SCREENCAP_SETTINGS["hotkeys"],
        **(cfg.get("hotkeys") or {}),
    }
    return merged


def update_screencap_settings(partial: Dict[str, Any], rebind: bool = False) -> Dict[str, Any]:
    """更新截屏录屏配置（可即时重绑热键）"""
    new = get_screencap_settings()
    for k, v in partial.items():
        if k == "hotkeys" and isinstance(v, dict):
            new["hotkeys"].update({str(x): str(y) for x, y in v.items()})
        else:
            new[k] = v
    update_settings({"screencap": new})

    if rebind:
        from .hotkey import get_hotkey_manager
        hm = get_hotkey_manager()
        if hm is not None:
            try:
                hm.apply(new.get("hotkeys", {}), enabled=bool(new.get("hotkeys_enabled", True)))
            except Exception as e:
                logger.warning(f"热键重绑失败: {e}")
    return new


def get_hotkey_config() -> Dict[str, Any]:
    """获取热键配置（供 app.py 启动时注册）"""
    cfg = get_screencap_settings()
    return {
        "enabled": bool(cfg.get("hotkeys_enabled", True)),
        "hotkeys": cfg.get("hotkeys", {}),
    }


# ────────── WebSocket 事件广播 ──────────

_ws_clients: set = set()
_loop: Optional[asyncio.AbstractEventLoop] = None


def set_loop(loop: Optional[asyncio.AbstractEventLoop]) -> None:
    global _loop
    _loop = loop


def add_client(ws) -> None:
    _ws_clients.add(ws)


def remove_client(ws) -> None:
    _ws_clients.discard(ws)


def broadcast(event: Dict[str, Any]) -> None:
    """向所有已连接的前端推送事件（可从任意线程调用）"""
    loop = _loop
    if loop is None or loop.is_closed():
        return
    try:
        loop.call_soon_threadsafe(_send_all, event)
    except Exception:
        pass


async def _send_all(event: Dict[str, Any]) -> None:
    text = json.dumps(event, ensure_ascii=False)
    for ws in list(_ws_clients):
        try:
            await ws.send_text(text)
        except Exception:
            _ws_clients.discard(ws)


# ────────── 热键动作分发 ──────────

def handle_hotkey_action(action: str) -> None:
    """全局热键触发入口（运行在热键线程，勿做阻塞 UI 的操作）"""
    try:
        if action == "screenshot":
            result = take_screenshot(mode="full")
            broadcast({"type": "captured", "mode": "full", **result})
        elif action == "screenshot_window":
            result = take_screenshot(mode="window")
            broadcast({"type": "captured", "mode": "window", **result})
        elif action == "recording":
            status = get_recording_status()
            if status.get("running"):
                result = stop_recording()
                broadcast({"type": "recording_stopped", **result})
            else:
                result = start_recording()
                broadcast({"type": "recording_started", **result})
    except Exception as e:
        logger.error(f"热键动作处理失败 {action}: {e}")
        broadcast({"type": "error", "action": action, "error": str(e)})
