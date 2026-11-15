# -*- mode: python ; coding: utf-8 -*-
"""
ZhiYanAgent 一键打包 spec（PyInstaller onedir）
用法: pyinstaller build_exe.spec --noconfirm
产物: dist/ZhiYanAgent/ZhiYanAgent.exe

便携说明（相对路径）:
- 可写数据（data/、logs/）解析到 exe 同级目录，整个文件夹可移动到任意位置运行
- 包内仅含程序与只读资源（frontend、内置插件等）；data 首次运行自动创建
- 如需携带已有数据，把 data 目录放到 exe 同级即可
"""
import os
import sys
import glob
from PyInstaller.utils.hooks import collect_submodules, collect_data_files, collect_dynamic_libs

ROOT = os.path.dirname(os.path.abspath(SPEC))
BACKEND = os.path.join(ROOT, "backend")
# 关键：让 collect_* 与 Analysis 都能解析到 zhiyan_web / astrbot 包
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


def _as_str_list(items):
    """把 collect_* 返回的任意可迭代项规整成字符串列表（去掉意外出现的 tuple/set）"""
    out = []
    for it in (items or []):
        if isinstance(it, str):
            out.append(it)
        elif isinstance(it, (list, tuple, set)):
            for sub in it:
                if isinstance(sub, str):
                    out.append(sub)
    return out


# ---------- 打包资源 ----------
datas = []
# 1) 前端静态资源（不可变，打包进 _internal/frontend）
datas.append((os.path.join(ROOT, "frontend"), "frontend"))

# 2) 内置插件目录（resource_path 期望在 backend/zhiyan_web/plugins/builtin_plugins）
_bp = os.path.join(BACKEND, "zhiyan_web", "plugins", "builtin_plugins")
for _f in glob.glob(os.path.join(_bp, "**", "*"), recursive=True):
    if os.path.isfile(_f):
        if "__pycache__" in _f:
            continue
        _rel = os.path.relpath(os.path.dirname(_f), BACKEND)
        datas.append((_f, os.path.join("backend", _rel)))

# 3) 应用包内的非 py 数据文件
datas += collect_data_files("zhiyan_web")
datas += collect_data_files("astrbot")

# 3.5) BetterGI/原神更新流程的图像模板（用户截图训练出来的按钮模板）
_tpl = os.path.join(ROOT, "data", "bettergi", "templates")
if os.path.isdir(_tpl):
    datas.append((_tpl, os.path.join("data", "bettergi", "templates")))
    print("[spec] 更新流程模板随包携带: %s" % _tpl)

# 4) 内置 ffmpeg（录屏/转码用）；封包后位于 exe 同级 data/ffmpeg.exe，与运行时 ffmpeg 探测路径一致
_ffmpeg = os.path.join(ROOT, "data", "ffmpeg.exe")
if os.path.isfile(_ffmpeg):
    datas.append((_ffmpeg, "data"))

# ---------- 隐藏导入：覆盖动态/懒加载模块 ----------
hiddenimports = _as_str_list(collect_submodules("zhiyan_web"))
hiddenimports += _as_str_list(collect_submodules("astrbot"))
hiddenimports += _as_str_list(collect_submodules("webview"))

# uvicorn[standard] 动态模块
hiddenimports += [
    "uvicorn", "uvicorn.main", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on", "uvicorn.lifespan.off",
    "uvicorn.logging", "uvicorn.loops.asyncio", "uvicorn.loops.uvloop",
    "uvicorn.protocols.http.h11_impl", "uvicorn.protocols.websockets.ws_impl",
    "websockets", "httptools", "httpx", "h11",
]

# 其它常见动态依赖
hiddenimports += [
    "clr", "pythonnet", "playwright", "playwright.sync_api", "playwright.async_api",
    "pyautogui", "pydirectinput", "comtypes", "pywinauto", "mouseinfo",
    "win32gui", "win32con", "win32api", "win32process",
    "PIL", "mutagen", "edge_tts", "pyttsx3", "cryptography", "Crypto",
]

# ---------- 过滤可选隐藏导入 ----------
# 这些是「装了就用、没装也能跑」的可选依赖。PyInstaller 对不存在的 hiddenimport
# 会打 ERROR 日志，虽然不中断构建，但看着像失败；这里先按 find_spec 过滤掉。
OPTIONAL_HIDDEN = (
    "pydirectinput",
    "pywinauto",
    "uvicorn.protocols.websockets.ws_impl",
    "uvicorn.protocols.websockets.wsproto_impl",
    "mouseinfo",
    "pyscreeze",
    "pygetwindow",
    "pymsgbox",
    "pytweening",
    "pyperclip",
)


def _importable(mod_name):
    import importlib.util
    try:
        return importlib.util.find_spec(mod_name) is not None
    except Exception:
        return False


_skipped = [m for m in hiddenimports if m in OPTIONAL_HIDDEN and not _importable(m)]
if _skipped:
    print("[spec] 跳过未安装的可选依赖: %s" % ", ".join(_skipped))
hiddenimports = [
    m for m in hiddenimports
    if not (m in OPTIONAL_HIDDEN and not _importable(m))
]

# 去重
seen = set()
hiddenimports = [x for x in hiddenimports if not (x in seen or seen.add(x))]

# ---------- 原生动态库（含 pythonnet 的 .dlc / .dll） ----------
binaries = []
for _pkg in ("webview", "pythonnet", "clr", "playwright", "system_hooks", "comtypes"):
    try:
        binaries += collect_dynamic_libs(_pkg)
    except Exception:
        pass
binaries += collect_dynamic_libs("cryptography")

# ---------- 本地 OCR（BetterGI 图像识别兜底） ----------
# 兼容两套引擎：rapidocr（3.x，Python 3.13+）与 rapidocr-onnxruntime（1.x，Python 3.8~3.12）
# 也支持应用内「安装 OCR 引擎」装到 data/py_deps/ocr 的独立目录
_ocr_vendor = os.path.join(ROOT, "data", "py_deps", "ocr")
# 说明：onnxruntime / rapidocr 装在 data/py_deps/ocr 这种独立目录里，
# PyInstaller 分析 onnxruntime.capi 时会让隔离子进程崩溃（Python 3.14 + 该版本 DLL）。
# 运行期 vision._ensure_sys_path() 会把 data/py_deps/ocr 加进 sys.path，
# 所以这里**不做模块分析**，直接把整个目录当资源拷进包内即可。
if os.path.isdir(_ocr_vendor):
    datas.append((_ocr_vendor, os.path.join("data", "py_deps", "ocr")))
    print("[spec] OCR 依赖目录随包携带（跳过分析）: %s" % _ocr_vendor)
else:
    print("[spec] 未找到 data/py_deps/ocr，OCR 需运行时自行安装")

# ---------- 组合 ----------
a = Analysis(
    [os.path.join(ROOT, "desktop.py")],
    pathex=[BACKEND, ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter", "test", "unittest", "pydoc", "doctest",
        "numpy.testing", "torch", "transformers", "scipy",
        # OCR 里用不到的重型/会崩的子模块
        "onnxruntime",
        "onnxruntime.capi",
        "onnxruntime.quantization",
        "onnxruntime.tools",
        "onnxruntime.transformers",
        "onnxruntime.datasets",
        "rapidocr",
        "rapidocr_onnxruntime",
        "sympy",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Furina Agent",
    debug=False,
    strip=False,
    upx=True,
    console=False,
    # 软件图标：使用软件根目录的 zhiyan.ico（由内置脚本生成，无第三方美术素材）
    icon=os.path.join(ROOT, "furina.ico"),
    # 所有依赖/资源直接平铺在 exe 同级目录（不产生 _internal 子目录），
    # 保持代码库文件夹架构：exe 同级即 frontend/ backend/ 等
    contents_directory='.',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name="Furina Agent",
)