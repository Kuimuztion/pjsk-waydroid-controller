#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Kuimuztion
# 一键启动 PJSK 置顶透明字幕与无冲触控映射
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OVERLAY_PY="$SCRIPT_DIR/pjsk_overlay.py"
KEYBINDS_PY="$SCRIPT_DIR/pjsk_keybinds.py"

echo "================================================================"
echo "[+] 启动 PJSK 判定线透明字幕与输入映射套件..."
echo "================================================================"

cleanup() {
    echo -e "\n[+] 正在退出所有服务..."
    if [[ -n "${OVERLAY_PID:-}" ]] && kill -0 "$OVERLAY_PID" 2>/dev/null; then
        kill -9 "$OVERLAY_PID" 2>/dev/null || true
    fi
    exit 0
}

trap cleanup INT TERM EXIT

# 1. 启动透明置顶字幕（作为普通桌面用户运行，无需 sudo）
python3 "$OVERLAY_PY" &
OVERLAY_PID=$!

sleep 0.4

# 2. 启动按键映射服务（需要读取原始键盘事件，需输入 sudo 密码）
echo "[+] 启动按键触控引擎（如需密码请输入）："
sudo python3 "$KEYBINDS_PY" || true
