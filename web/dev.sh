#!/usr/bin/env bash
# 一键启动 Mini-Agent Web UI 的后端 (FastAPI) + 前端 (Vite).
#
# 用法 (在仓库根目录):
#   ./web/dev.sh                 # 默认端口: 后端 8000, 前端 5173
#   BACKEND_PORT=9000 ./web/dev.sh
#   FRONTEND_PORT=5174 ./web/dev.sh
#
# 关键设计:
#   * uvicorn --reload 的监听范围用 --reload-dir 严格限制到 web/backend,
#     这样 agent 在 workspace/ 写入文件、frontend 热更新, 都不会触发后端重启.
#   * 后端 / 前端分别用后台进程启动, 父进程用 trap 转发 SIGINT/SIGTERM,
#     Ctrl-C 可以同时干净地杀掉两个进程.

set -euo pipefail

# 脚本路径: <repo>/web/dev.sh
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
    echo
    echo "[dev.sh] shutting down..."
    if [[ -n "$BACKEND_PID" ]] && kill -0 "$BACKEND_PID" 2>/dev/null; then
        kill "$BACKEND_PID" 2>/dev/null || true
    fi
    if [[ -n "$FRONTEND_PID" ]] && kill -0 "$FRONTEND_PID" 2>/dev/null; then
        kill "$FRONTEND_PID" 2>/dev/null || true
    fi
    # 给子进程一点退出时间, 再兜底 SIGKILL.
    sleep 0.5
    [[ -n "$BACKEND_PID" ]]  && kill -0 "$BACKEND_PID"  2>/dev/null && kill -9 "$BACKEND_PID"  2>/dev/null || true
    [[ -n "$FRONTEND_PID" ]] && kill -0 "$FRONTEND_PID" 2>/dev/null && kill -9 "$FRONTEND_PID" 2>/dev/null || true
    wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

# --- 后端 ---------------------------------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
    echo "[dev.sh] ERROR: uv not found. Install it from https://docs.astral.sh/uv/" >&2
    exit 1
fi

echo "[dev.sh] starting backend on http://${BACKEND_HOST}:${BACKEND_PORT}"
echo "[dev.sh]   reload-watch = web/backend  (workspace/ is intentionally excluded)"

# --reload-dir 把监听范围限制在 backend 源码目录.
# 这样 workspace/ 下任何写入都不会触发 reload.
uv run uvicorn web.backend.app:app \
    --host "$BACKEND_HOST" \
    --port "$BACKEND_PORT" \
    --reload \
    --reload-dir web/backend \
    --reload-include '*.py' \
    --reload-include '*.yaml' \
    --reload-include '*.yml' \
    --reload-include '*.toml' \
    --reload-include '*.json' \
    &
BACKEND_PID=$!

# --- 前端 ---------------------------------------------------------------------
if [[ ! -d "$SCRIPT_DIR/frontend/node_modules" ]]; then
    echo "[dev.sh] frontend deps missing — running npm install first..."
    (cd "$SCRIPT_DIR/frontend" && npm install)
fi

echo "[dev.sh] starting frontend on http://localhost:${FRONTEND_PORT}"
(
    cd "$SCRIPT_DIR/frontend"
    # --strictPort: 端口被占时直接报错退出, 不要静默换到 5174/5175.
    # 这样用户能立刻看到冲突, 而不是被一个错误地址误导.
    FRONTEND_PORT="$FRONTEND_PORT" npm run dev -- --port "$FRONTEND_PORT" --strictPort
) &
FRONTEND_PID=$!

echo
echo "[dev.sh] both services running. Press Ctrl-C to stop."
echo "         backend  -> http://${BACKEND_HOST}:${BACKEND_PORT}"
echo "         frontend -> http://localhost:${FRONTEND_PORT}"
echo

# 等任一进程退出 (用 poll 循环, 兼容 macOS 自带的 bash 3.2, 它不支持 `wait -n`).
# loop 退出时, EXIT trap 会自动调用 cleanup 杀掉剩下的进程.
while kill -0 "$BACKEND_PID" 2>/dev/null && kill -0 "$FRONTEND_PID" 2>/dev/null; do
    sleep 0.5
done
