#!/usr/bin/env bash
# 一键启动 RAG demo：后端 FastAPI(8000) + 前端 Vite(5173)
# 用法：./start.sh         启动（Ctrl+C 停止）
#       ./start.sh stop    停止之前启动的服务
set -euo pipefail

cd "$(dirname "$0")"
mkdir -p logs

# ---------- 停止子命令 ----------
stop_services() {
  echo "正在停止服务..."
  for name in backend frontend; do
    pidfile="logs/$name.pid"
    if [ -f "$pidfile" ]; then
      pid=$(cat "$pidfile")
      kill "$pid" 2>/dev/null || true
      rm -f "$pidfile"
    fi
  done
  pkill -f "uvicorn app.main" 2>/dev/null || true
  pkill -f "python_ai/frontend.*vite" 2>/dev/null || true
  echo "已停止"
}

if [ "${1:-}" = "stop" ]; then
  stop_services
  exit 0
fi

# ---------- 端口预检 ----------
port_in_use() {
  lsof -nP -tiTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

if port_in_use 8000 || port_in_use 5173; then
  echo "错误：端口 8000 或 5173 已被占用，服务可能已经在运行。"
  echo "  - 直接使用：http://localhost:5173"
  echo "  - 先停止再重启：./start.sh stop && ./start.sh"
  echo "  - 查看占用进程：lsof -nP -iTCP:8000 -iTCP:5173 -sTCP:LISTEN"
  exit 1
fi

# ---------- 后端 ----------
if [ ! -d .venv ]; then
  echo "[init] 创建 Python 虚拟环境..."
  uv venv
fi
if ! .venv/bin/python -c "import fastapi, pymilvus, pdfplumber" 2>/dev/null; then
  echo "[init] 安装 Python 依赖..."
  uv pip install -r requirements.txt
fi

# ---------- 前端 ----------
if [ ! -d frontend/node_modules ]; then
  echo "[init] 安装前端依赖..."
  (cd frontend && npm install)
fi

echo "[backend ] http://localhost:8000  (API 文档: http://localhost:8000/docs)"
echo "[frontend] http://localhost:5173"
echo "日志：logs/backend.log / logs/frontend.log，Ctrl+C 停止全部服务"

.venv/bin/uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 >logs/backend.log 2>&1 &
BACKEND_PID=$!
echo "$BACKEND_PID" > logs/backend.pid
(cd frontend && npm run dev >../logs/frontend.log 2>&1) &
FRONTEND_PID=$!
echo "$FRONTEND_PID" > logs/frontend.pid

cleanup() {
  echo ""
  stop_services
}
trap cleanup INT TERM

# 任一服务退出则结束脚本（兼容 bash 3.2，无 wait -n）
while kill -0 "$BACKEND_PID" 2>/dev/null && kill -0 "$FRONTEND_PID" 2>/dev/null; do
  sleep 2
done
echo "检测到服务异常退出，最近日志："
tail -5 logs/backend.log || true
tail -5 logs/frontend.log || true
cleanup
