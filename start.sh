#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
APP_DIR="$(pwd)"

# 默认配置
DATA_DIR="${NICEVID_DATA_DIR:-$APP_DIR/data}"
STORAGE_SECRET="${NICEVID_STORAGE_SECRET:-nicevid-secret-key-change-in-production}"
RELOAD="${NICEVID_RELOAD:-false}"
PORT="${NICEVID_PORT:-8080}"
HOST="${NICEVID_HOST:-0.0.0.0}"
SKIP_XVFB="${NICEVID_SKIP_XVFB:-false}"

# 颜色
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}╔════════════════════════════╗${NC}"
echo -e "${CYAN}║     VidZap 启动脚本        ║${NC}"
echo -e "${CYAN}╚════════════════════════════╝${NC}"
echo ""

# 创建必要目录
mkdir -p "$DATA_DIR" "$APP_DIR/downloads" "$APP_DIR/cookies"
echo -e "${GREEN}✓${NC} 目录已就绪"

# 启动 Xvfb（用于抖音笔记提取）
if [ "$SKIP_XVFB" != "true" ]; then
    if pgrep -f "Xvfb :99" > /dev/null 2>&1; then
        echo -e "${GREEN}✓${NC} Xvfb 已在 :99 运行"
    else
        echo -e "${YELLOW}⟳${NC} 启动 Xvfb :99..."
        Xvfb :99 -screen 0 1280x720x24 -nolisten tcp &>/dev/null &
        sleep 1
        if pgrep -f "Xvfb :99" > /dev/null 2>&1; then
            echo -e "${GREEN}✓${NC} Xvfb 已启动"
        else
            echo -e "${YELLOW}⚠ Xvfb 启动失败（不影响普通视频下载）${NC}"
        fi
    fi
fi

# 导出环境
export DISPLAY="${DISPLAY:-:99}"
export NICEVID_DATA_DIR="$DATA_DIR"
export NICEVID_STORAGE_SECRET="$STORAGE_SECRET"
export NICEVID_RELOAD="$RELOAD"

echo -e "${GREEN}✓${NC} 环境变量已设置"
echo ""

echo -e "${CYAN}启动应用...${NC}"
echo -e "  地址: ${YELLOW}http://$HOST:$PORT${NC}"
echo -e "  数据: ${YELLOW}$DATA_DIR${NC}"
echo -e "  热重载: ${YELLOW}$RELOAD${NC}"
echo ""

exec uv run python src/main.py
