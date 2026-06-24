#!/usr/bin/env bash
# ============================================================
#  洛谷 AI 报告生成器 - 一键启动脚本 (Linux / macOS)
# ============================================================

set -e
cd "$(dirname "$0")"

echo "=========================================================="
echo "  洛谷 AI 报告生成器 - 一键启动"
echo "  工作目录: $(pwd)"
echo "=========================================================="

# 1) 检查 Python
if ! command -v python3 >/dev/null 2>&1; then
    echo "[错误] 未检测到 python3，请先安装 Python 3.10+"
    exit 1
fi
PYVER=$(python3 --version 2>&1 | awk '{print $2}')
echo "[1/4] 检测到 Python $PYVER"

# 2) 安装依赖
echo ""
echo "[2/4] 检查并安装依赖 (requirements.txt) ..."
python3 -m pip install --upgrade pip >/dev/null
python3 -m pip install -r requirements.txt
echo "      依赖 OK"

# 3) 杀掉占用端口的旧进程 (8765)
PORT=8765
echo ""
echo "[3/4] 清理端口 $PORT 上的旧进程 ..."
PID=$(lsof -ti :$PORT 2>/dev/null || true)
if [ -n "$PID" ]; then
    echo "      结束 PID=$PID"
    kill -9 $PID 2>/dev/null || true
fi

# 4) 启动 Web
echo ""
echo "[4/4] 启动 Web 服务 ..."
echo "      访问地址: http://127.0.0.1:$PORT/"
echo "      按 Ctrl+C 可停止服务"
echo ""

# 检查 Playwright Chromium
if [ ! -d "$HOME/.cache/ms-playwright/chromium-"* ] && [ ! -d "$HOME/Library/Caches/ms-playwright/chromium-"* ]; then
    read -p "未检测到 Playwright Chromium，是否现在安装 (PDF 导出需要)? [y/N]: " INSTALL_PW
    if [[ "$INSTALL_PW" =~ ^[Yy]$ ]]; then
        echo "      正在安装 Playwright Chromium ..."
        python3 -m playwright install chromium
    fi
fi

echo ""
echo "=========================================================="
echo "  服务启动中 ... (2 秒后自动打开浏览器)"
echo "=========================================================="

# 2 秒后打开浏览器
( sleep 2 && (xdg-open http://127.0.0.1:$PORT/ 2>/dev/null || open http://127.0.0.1:$PORT/ 2>/dev/null || echo "请手动打开 http://127.0.0.1:$PORT/") ) &

python3 -m luogu_report_generator web --host 127.0.0.1 --port $PORT

echo ""
echo "[已停止] Web 服务已退出"
