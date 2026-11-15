# -*- coding: utf-8 -*-
"""Furina 插件依赖安装器。

用法：直接双击 install_plugin_deps.bat，或在终端运行
    python install_plugin_deps.py
会逐个为三个插件执行 pip install -U -r requirements.txt，
并打印进度；末尾等待回车保持窗口（避免"闪退"看不到日志）。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PLUGINS_DIR = os.path.join(ROOT, "data", "plugins")

PLUGINS = [
    "astrbot_plugin_essential",
    "astrbot_plugin_apis",
    "astrbot_plugin_parser",
]

KNOWN_OPTIONAL_FAIL = {"yt-dlp-ejs"}


def pip(plugin: str, req: str) -> int:
    print("=" * 72)
    print(f"==> 安装 {plugin} 依赖: {os.path.basename(req)}")
    print("=" * 72)
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-r", req],
        cwd=os.path.dirname(req),
    )
    return proc.returncode


def main() -> int:
    print("Furina 插件依赖安装器")
    print(f"插件目录: {PLUGINS_DIR}")
    if not os.path.isdir(PLUGINS_DIR):
        print(f"[错误] 找不到插件目录: {PLUGINS_DIR}")
        return 1

    ok = True
    for name in PLUGINS:
        pdir = os.path.join(PLUGINS_DIR, name)
        req = os.path.join(pdir, "requirements.txt")
        if not os.path.isdir(pdir):
            print(f"\n[跳过] 未找到插件目录: {name}")
            continue
        if not os.path.isfile(req):
            print(f"\n[跳过] {name} 没有 requirements.txt")
            continue
        code = pip(name, req)
        if code != 0:
            ok = False
            print(f"\n[警告] {name} 依赖安装返回码 {code}")
        else:
            print(f"\n[完成] {name} 依赖安装成功")

    print()
    if ok:
        print("全部插件依赖安装完成。请重启 backend/main.py 使插件加载。")
    else:
        print("部分依赖未完全安装，请查看上方日志。若仅 yt-dlp-ejs 失败可忽略。")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        code = main()
    except KeyboardInterrupt:
        code = 130
        print("\n用户中断。")
    except Exception as exc:  # noqa: BLE001 打印异常避免闪退
        code = 1
        import traceback
        traceback.print_exc()
    print("\n按回车键退出...")
    sys.stdout.flush()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace") if hasattr(sys.stdout, "reconfigure") else None
    try:
        if not sys.stdin.isatty():
            import threading
            threading.Event().wait(20)  # 非交互环境等待 20 秒避免立刻退出
        else:
            input()
    except Exception:
        pass
    sys.exit(code)