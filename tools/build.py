# -*- coding: utf-8 -*-
"""FURINA AGENT WEB —— 自动化打包脚本（PyInstaller onedir）

用法（在项目根目录）：
    python tools/build.py              # 清理并打包，产物 dist/ZhiYanAgent/
    python tools/build.py --zip        # 打包后再压缩成 dist/ZhiYanAgent.zip
    python tools/build.py --no-clean   # 增量打包（不清 build/dist，速度快但可能残留旧文件）
    python tools/build.py --check      # 只检查环境，不打包
    python tools/build.py --no-pause   # 结束/失败时不等待按键（CI/命令行用）

也可以直接双击 build.bat。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import io
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(ROOT, "build_exe.spec")
DIST = os.path.join(ROOT, "dist")
TEMPLATES = os.path.join(ROOT, "data", "bettergi", "templates")


def spec_app_name(spec_path: str = SPEC) -> str:
    """从 spec 里读出产物名（COLLECT 的 name），默认 ZhiYanAgent"""
    try:
        text = io.open(spec_path, encoding="utf-8").read()
    except Exception:
        return "ZhiYanAgent"
    names = re.findall(r'name="([^"]+)"', text)
    return names[-1] if names else "ZhiYanAgent"


def _init_console() -> None:
    """把控制台切成 UTF-8（完整输出里有大量中英文混排）"""
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def log(msg: str = "") -> None:
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("ascii", "replace").decode("ascii"), flush=True)


def human(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}GB"


def dir_size(path: str) -> int:
    total = 0
    for base, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(base, f))
            except OSError:
                pass
    return total


def running_app(exe_name: str) -> bool:
    """目标 exe 是否正在运行（会锁文件导致打包失败）"""
    names = [exe_name]
    # 兼容历史命名（用户可能把产物改名成 furina.exe / FurinaAgent）
    for legacy in ("ZhiYanAgent.exe", "furina.exe", "FurinaAgent.exe"):
        if legacy not in names:
            names.append(legacy)
    try:
        out = subprocess.run(["tasklist", "/NH"], capture_output=True, text=True, errors="ignore").stdout or ""
    except Exception:
        return False
    low = out.lower()
    return any(n.lower() in low for n in names)


def exe_candidates(app_dir: str, name: str):
    """产物 exe 的候选路径（优先 spec 名字，其次目录里唯一的 exe）"""
    cands = [os.path.join(app_dir, name + ".exe")]
    try:
        for f in os.listdir(app_dir):
            if f.lower().endswith(".exe"):
                p = os.path.join(app_dir, f)
                if p not in cands:
                    cands.append(p)
    except OSError:
        pass
    return cands


def check_env(name: str = "") -> bool:
    ok = True
    log(f"Python      : {sys.version.split()[0]} ({sys.executable})")
    if sys.version_info < (3, 10):
        log("  [X] Python 版本过低，需要 3.10+")
        ok = False

    try:
        import PyInstaller  # noqa: F401
        log(f"PyInstaller : {PyInstaller.__version__}")
    except Exception:
        log("  [X] 未安装 PyInstaller，请先执行: pip install pyinstaller")
        ok = False

    if not os.path.isfile(SPEC):
        log(f"  [X] 找不到打包配置: {SPEC}")
        ok = False
    else:
        log(f"spec        : {os.path.relpath(SPEC, ROOT)}")

    icon = "zhiyan.ico"
    try:
        m = re.search(r'icon=os\.path\.join\(ROOT,\s*"([^"]+)"\)', io.open(SPEC, encoding="utf-8").read())
        if m:
            icon = m.group(1)
    except Exception:
        pass
    if os.path.isfile(os.path.join(ROOT, icon)):
        log(f"图标        : {icon} [OK]")
    else:
        log(f"图标        : 缺少 {icon}（打包会失败）")
        ok = False

    if os.path.isdir(TEMPLATES):
        n = len([f for f in os.listdir(TEMPLATES) if f.lower().endswith(".png")])
        log(f"更新模板    : {n} 个 [OK]")
    else:
        log("更新模板    : data/bettergi/templates 不存在（不影响打包，但更新流程没模板可用）")

    ocr = os.path.join(ROOT, "data", "py_deps", "ocr")
    log(f"OCR 依赖    : {'[OK] ' + os.path.relpath(ocr, ROOT) if os.path.isdir(ocr) else '缺失（运行期需自行安装）'}")

    name = name or spec_app_name()
    log(f"产物名      : {name}（spec 默认 {spec_app_name()}，--name 可覆盖）")
    if running_app(name):
        log(f"  [X] 检测到 {name}.exe 正在运行，请先退出再打包（文件被占用会导致失败）")
        ok = False
    return ok


def clean() -> None:
    for path in (os.path.join(ROOT, "build"), DIST):
        if os.path.isdir(path):
            log(f"清理 {os.path.relpath(path, ROOT)} ...")
            shutil.rmtree(path, ignore_errors=True)


def prepare_spec(name: str) -> str:
    """如果指定了不同于 spec 的名字，复制一份临时 spec 改名后使用"""
    base = spec_app_name()
    if not name or name == base:
        return SPEC
    text = io.open(SPEC, encoding="utf-8").read()
    text = re.sub(r'name="[^"]+"', 'name="%s"' % name, text)
    tmp = os.path.join(ROOT, "build_exe.tmp.spec")
    io.open(tmp, "w", encoding="utf-8").write(text)
    log(f"使用临时 spec（产物名 {name}）: {os.path.relpath(tmp, ROOT)}")
    return tmp


def run_build(log_path: str, spec_path: str = SPEC, verbose: bool = True) -> int:
    """调用 PyInstaller 并实时把输出打到屏幕（同时写日志）

    - 子进程用 -u 关闭缓冲，PyInstaller 的 INFO 行才能实时出现
    - 万一长时间没有任何输出（分析依赖阶段可能静默很久），每 15s 打一行心跳，
      避免看起来像卡死
    """
    import threading

    cmd = [sys.executable, "-u", "-m", "PyInstaller", spec_path, "--noconfirm", "--clean"]
    log("执行: " + " ".join(cmd))
    log("日志: " + os.path.relpath(log_path, ROOT))
    log("提示: 首次打包通常 8~12 分钟，下面会实时输出 PyInstaller 的每一步\n")

    start = time.time()
    last = {"t": time.time(), "lines": 0}
    fh = open(log_path, "w", encoding="utf-8", errors="replace")

    proc = subprocess.Popen(
        cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace", bufsize=1,
    )
    assert proc.stdout is not None

    def reader() -> None:
        for line in proc.stdout:
            last["t"] = time.time()
            last["lines"] += 1
            try:
                fh.write(line)
                fh.flush()
            except Exception:
                pass
            text = line.rstrip("\r\n")
            if verbose:
                log(text)
                continue
            if "Hidden import" in text and "not found" in text:
                continue
            if any(k in text for k in ("ERROR", "Traceback", "Build complete", "Building COLLECT",
                                       "Collecting", "WARNING: lib not found", "[spec]")):
                log("  " + text[:200])

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()

    # 心跳：15 秒没有任何新输出就提示一次
    while proc.poll() is None:
        time.sleep(3)
        if time.time() - last["t"] >= 15:
            log("    ...仍在打包（已 %ds，累计 %d 行输出；PyInstaller 分析依赖阶段可能较慢）"
                % (int(time.time() - start), last["lines"]))
            last["t"] = time.time()

    thread.join(timeout=10)
    try:
        fh.close()
    except Exception:
        pass

    code = proc.returncode or 0
    log(f"\n耗时 {time.time() - start:.0f}s，日志: {os.path.relpath(log_path, ROOT)}")
    return code


def verify(app_dir: str, name: str) -> bool:
    if not os.path.isdir(app_dir):
        log(f"  [X] 未找到产物目录 {os.path.relpath(app_dir, ROOT)}")
        return False
    exe = ""
    for cand in exe_candidates(app_dir, name):
        if os.path.isfile(cand):
            exe = cand
            break
    if not exe:
        log("  [X] 产物目录里没有 exe")
        return False
    size = os.path.getsize(exe)
    total = dir_size(app_dir)
    log(f"  [OK] {os.path.relpath(exe, ROOT)}  ({human(size)})")
    log(f"  [OK] 产物目录总大小 {human(total)}")
    for item in ("frontend", "backend", "data"):
        p = os.path.join(app_dir, item)
        log(f"  {'[OK]' if os.path.isdir(p) else '[X]'} {item}/")
    return size > 1_000_000


def make_zip(app_dir: str, name: str) -> str:
    zip_path = os.path.join(DIST, name + ".zip")
    log(f"压缩 → {os.path.relpath(zip_path, ROOT)} ...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for base, _dirs, files in os.walk(app_dir):
            for f in files:
                full = os.path.join(base, f)
                rel = os.path.relpath(full, DIST)
                zf.write(full, rel)
    log(f"  [OK] 压缩包 {human(os.path.getsize(zip_path))}")
    return zip_path


def main() -> int:
    parser = argparse.ArgumentParser(description="FURINA AGENT WEB 打包脚本")
    parser.add_argument("--zip", action="store_true", help="打包完成后压缩 dist/ZhiYanAgent.zip")
    parser.add_argument("--no-clean", action="store_true", help="不清理 build/dist（增量打包）")
    parser.add_argument("--check", action="store_true", help="只检查环境，不执行打包")
    parser.add_argument("--no-pause", action="store_true", help="结束时不等待按键")
    parser.add_argument("--name", default="", help="覆盖产物名（默认取 build_exe.spec 里的 name）")
    parser.add_argument("--quiet", action="store_true", help="只显示关键行（默认显示完整打包过程）")
    args = parser.parse_args()

    log("=" * 60)
    log(" FURINA AGENT WEB 打包")
    log("=" * 60)

    name = args.name or spec_app_name()
    app_dir = os.path.join(DIST, name)

    if not check_env(name):
        log("\n环境检查未通过，已中止。")
        return 1
    if args.check:
        log("\n环境检查通过（--check 模式，未执行打包）。")
        return 0

    if not args.no_clean:
        clean()
    else:
        log("跳过清理（--no-clean）")

    log_path = os.path.join(ROOT, "build_%s.log" % _dt.datetime.now().strftime("%Y%m%d_%H%M%S"))
    spec_used = prepare_spec(name)
    try:
        code = run_build(log_path, spec_used, verbose=not args.quiet)
        try:
            log_text = io.open(log_path, encoding="utf-8", errors="replace").read()
        except OSError:
            log_text = ""
        hidden_missing = [ln.strip() for ln in log_text.splitlines()
                          if "Hidden import" in ln and "not found" in ln]
        build_complete = "Build complete" in log_text
        if hidden_missing:
            log(f"\n提示：{len(hidden_missing)} 个可选依赖未安装，已自动跳过（不影响打包）：")
            for ln in hidden_missing[:8]:
                log("  - " + ln.split("Hidden import")[-1].strip(" :'"))
            if len(hidden_missing) > 8:
                log(f"  ... 其余 {len(hidden_missing) - 8} 条见日志")
        if code == 0 and not build_complete:
            log("\n[X] PyInstaller 退出码为 0 但日志里没有 'Build complete'，请检查日志")
            code = 1
    finally:
        if spec_used != SPEC and os.path.isfile(spec_used):
            try:
                os.remove(spec_used)
            except OSError:
                pass
    if code != 0:
        log(f"\n✗ 打包失败（退出码 {code}），请查看日志: {os.path.relpath(log_path, ROOT)}")
        tail = os.path.join(ROOT, log_path)
        try:
            with open(tail, encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()[-25:]
            log("\n--- 日志末尾 ---")
            for line in lines:
                log(line.rstrip())
        except OSError:
            pass
        return code

    log("")
    if not verify(app_dir, name):
        log("\n[X] 产物校验失败")
        return 1

    if args.zip:
        try:
            make_zip(app_dir, name)
        except Exception as e:  # 压缩失败不影响主要产物
            log(f"  [X] 压缩失败: {e}")

    log("\n打包完成 [OK]")
    log(f"产物目录: {os.path.relpath(app_dir, ROOT)}")
    return 0


if __name__ == "__main__":
    _init_console()
    try:
        rc = main()
    except KeyboardInterrupt:
        log("\n已取消")
        rc = 130
    if "--no-pause" not in sys.argv and os.name == "nt":
        try:
            input("\n按回车退出...")
        except (EOFError, KeyboardInterrupt):
            pass
    sys.exit(rc)
