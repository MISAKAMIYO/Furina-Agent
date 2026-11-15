"""
DSH 安全删除模块
================
针对 dsh 引擎的额外安全管控：确保所有通过 dsh 执行的“删除文件/目录”操作都
先进入回收站，而不是被物理永久删除。

实现策略:
- Windows: 使用 ctypes 调用 SHFileOperation 把文件/目录移入系统回收站
  （FOF_ALLOWUNDO），无需额外第三方依赖（send2trash 未安装也 OK）。
- 非 Windows 或系统回收站不可用时: 降级为“托管回收箱”（data/recycle_bin），
  即移动而非物理删除，任何情况下都不丢数据。

用法（模块亦可作为 CLI 使用）:
    python trash.py recycle <path> [path ...]
    python trash.py restore <path>
    python trash.py list
"""
from __future__ import annotations

import datetime
import os
import shutil
import sys
from typing import Dict, List, Tuple

# 确保作为 CLI 独立运行时（cwd 任意）也能 import zhiyan_web.config
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

logger = None  # 延迟初始化

def _log(msg: str) -> None:
    global logger
    if logger is None:
        import logging
        logger = logging.getLogger("ZhiYanWeb.Trash")
    logger.info("[回收站] " + msg)


def _data_dir() -> str:
    from zhiyan_web.config import DATA_DIR
    return DATA_DIR


def _win_recycle(paths: List[str]) -> Tuple[bool, str]:
    """使用 Windows SHFileOperation 将给定路径移入系统回收站。"""
    import ctypes
    from ctypes import wintypes

    FO_DELETE = 3
    FOF_ALLOWUNDO = 0x40      # 关键：允许撤消，即进回收站
    FOF_NOCONFIRMATION = 0x10
    FOF_SILENT = 0x04
    FOF_NOERRORUI = 0x0400

    class SHFILEOPSTRUCT(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", ctypes.c_uint),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_ushort),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", wintypes.LPVOID),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    # SHFileOperation 要求 pFrom 以双 NUL 结尾
    src = "\0".join(paths) + "\0\0"
    op = SHFILEOPSTRUCT(
        hwnd=0,
        wFunc=FO_DELETE,
        pFrom=src,
        pTo=None,
        fFlags=FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI,
        fAnyOperationsAborted=False,
        hNameMappings=None,
        lpszProgressTitle=None,
    )
    SHFileOperationW = ctypes.windll.shell32.SHFileOperationW
    result = SHFileOperationW(ctypes.byref(op))
    if result != 0:
        return False, f"SHFileOperation 返回错误码 {result}"
    if op.fAnyOperationsAborted:
        return False, "操作被系统中断（可能部分文件未进回收站）"
    return True, "recycle_bin"


def _managed_recycle(paths: List[str]) -> Tuple[bool, str]:
    """托管回收箱：把文件移动到 data/recycle_bin 下（物理不删除）。"""
    import time
    root = os.path.join(_data_dir(), "recycle_bin")
    os.makedirs(root, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    for i, p in enumerate(paths):
        base = os.path.basename(os.path.normpath(p)) or ("item_" + str(i))
        dest = os.path.join(root, f"{stamp}_{i}_{base}")
        if os.path.isdir(p) and not os.path.islink(p):
            shutil.move(p, dest)
        else:
            shutil.move(p, dest)
    return True, "managed_box"


def recycle(path: str) -> Dict[str, object]:
    """把单个文件/目录安全移入回收站。成功返回 {'ok':True,...}，绝不物理删除。"""
    path = os.path.normpath(path)
    if not os.path.exists(path):
        return {"ok": True, "method": "noop", "path": path,
                "message": "路径不存在，无需删除"}
    _log(f"安全删除: {path}")
    if sys.platform == "win32":
        try:
            ok, method = _win_recycle([path])
            if ok:
                return {"ok": True, "method": method, "path": path,
                        "message": "已移入 Windows 系统回收站"}
        except Exception as exc:  # 回收站不可用时降级到托管回收箱
            _log(f"系统回收站失败({exc})，降级到托管回收箱")
    else:
        _log("非 Windows 环境，使用托管回收箱")
    try:
        ok, method = _managed_recycle([path])
        return {"ok": ok, "method": method, "path": path,
                "message": "已移入托管回收箱（data/recycle_bin）"}
    except Exception as exc:
        return {"ok": False, "path": path, "error": str(exc),
                "message": f"安全删除失败: {exc}"}


def restore(path_hint: str) -> Dict[str, object]:
    """（预留）从托管回收箱按名字查找并恢复。"""
    root = os.path.join(_data_dir(), "recycle_bin")
    name = os.path.basename(os.path.normpath(path_hint))
    if not os.path.isdir(root):
        return {"ok": False, "message": "托管回收箱为空"}
    for fn in os.listdir(root):
        if fn.endswith("_" + name) or name in fn:
            return {"ok": True, "path": os.path.join(root, fn)}
    return {"ok": False, "message": "未在回收箱中找到匹配项"}


def list_managed() -> List[Dict[str, object]]:
    root = os.path.join(_data_dir(), "recycle_bin")
    out: List[Dict[str, object]] = []
    if os.path.isdir(root):
        for fn in sorted(os.listdir(root)):
            p = os.path.join(root, fn)
            try:
                st = os.stat(p)
            except OSError:
                st = None
            out.append({"name": fn, "path": p,
                        "time": datetime.datetime.fromtimestamp(
                            st.st_mtime).strftime("%Y-%m-%d %H:%M:%S") if st else "",
                        "size": st.st_size if st else 0,
                        "is_dir": os.path.isdir(p)})
    return out


def cli() -> int:
    args = sys.argv[1:]
    if not args:
        print("用法: trash.py recycle <path...> | list | restore <name>")
        return 1
    cmd = args[0]
    if cmd == "recycle":
        ok_all = True
        for p in args[1:]:
            r = recycle(p)
            print(json_dump(r))
            if not r.get("ok"):
                ok_all = False
        return 0 if ok_all else 2
    if cmd == "list":
        print(json_dump(list_managed()))
        return 0
    if cmd == "restore":
        print(json_dump(restore(args[1] if len(args) > 1 else "")))
        return 0
    print("未知子命令: " + cmd)
    return 1


def json_dump(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


if __name__ == "__main__":
    sys.exit(cli())