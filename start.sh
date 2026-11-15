#!/usr/bin/env bash
# =============================================================================
# Furina Agent - macOS / Linux 启动脚本
# =============================================================================
# 用法:
#   ./start.sh           直接启动桌面应用（自动补全系统依赖）
#   DEBUG=1 ./start.sh   以调试模式启动（打开开发者工具）
#   SERVER_ONLY=1 ./start.sh   仅启动后端服务（浏览器访问控制台）
# =============================================================================
set -e
cd "$(dirname "$0")"

PY=${PYTHON:-python3}

# ---------- 检查系统依赖 ----------
detect_os() {
    case "$(uname -s)" in
        Darwin*) echo "macos" ;;
        Linux*)  echo "linux" ;;
        *)       echo "unknown" ;;
    esac
}
OS=$(detect_os)

if [ "$SERVER_ONLY" != "1" ]; then
    case "$OS" in
        linux)
            echo "[依赖检查] Linux 需要 webkit2gtk / gtk"
            for pkg in python3-venv build-essential libwebkit2gtk-4.1-dev \
                       libgtk-3-dev libayatana-appindicator3-dev gir1.2-appindicator3-0.1 \
                       python3-dev; do
                if ! dpkg -s "${pkg%%=*}" >/dev/null 2>&1 && command -v apt-get >/dev/null 2>&1; then
                    echo "  检测到缺少系统包: $pkg（可运行 sudo apt install $pkg）"
                fi
            done
            ;;
        macos)
            echo "[依赖检查] macOS 建议安装 python3（brew install python@3.12）"
            ;;
    esac
fi

# ---------- 虚拟环境 ----------
if [ ! -d "venv" ]; then
    echo "[环境] 创建虚拟环境 venv ..."
    "$PY" -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate

echo "[环境] 安装 Python 依赖（首次较慢）..."
pip install --upgrade pip >/dev/null 2>&1 || true
pip install -r requirements.txt

# macOS 桌面与 TTS 依赖
if [ "$OS" = "macos" ]; then
    pip install pyobjc >/dev/null 2>&1 || true
fi

# ---------- 启动 ----------
if [ "$SERVER_ONLY" = "1" ]; then
    echo "[启动] 仅启动后端服务..."
    exec python -m uvicorn --app-dir backend zhiyan_web.app:app --host 127.0.0.1 --port 8001
fi

if [ "$DEBUG" = "1" ]; then
    echo "[启动] 调试模式..."
    exec python desktop.py --debug
fi

echo "[启动] 桌面应用..."
exec python desktop.py