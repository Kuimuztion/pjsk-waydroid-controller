#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Kuimuztion
"""
PJSK 屏幕定格线专属永久置顶透明键位字幕 (Fedora KDE Wayland)
优化版：
  1. 大按键双轨合击徽标（Z, X, C, V, B）移至判定线【正下方】：
     - [ Z : A+S ] 放大显眼显示在下方 A 与 S 的正中间下方；
     - [ X : S+D ] 放大显眼显示在下方 S 与 D 的正中间下方；
     - [ C : D+J ] 放大显眼显示在下方 D 与 J 的正中间下方；
     - [ V : J+K ] 放大显眼显示在下方 J 与 K 的正中间下方；
     - [ B : K+L ] 放大显眼显示在下方 K 与 L 的正中间下方；
  2. 判定线上方视野完全清空，绝不遮挡下落音符视线；
  3. 纯透明背景（WA_TranslucentBackground）与鼠标穿透（WindowTransparentForInput）；
  4. 支持 Ctrl+C 瞬间秒退（配置 signal.SIG_DFL 与周期定时器）。
"""

import sys
import os
import signal
import argparse
import subprocess
import tempfile
from PySide6.QtWidgets import QApplication, QWidget
from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import (
    QPainter, QColor, QFont, QPen, QBrush,
    QPainterPath, QLinearGradient
)

# 确保 Ctrl+C (SIGINT) 和 kill (SIGTERM) 能立即强退进程
signal.signal(signal.SIGINT, signal.SIG_DFL)
signal.signal(signal.SIGTERM, signal.SIG_DFL)

SCREEN_W = 1920
SCREEN_H = 1080

# 判定框标准几何尺寸（1920x1080）
BOX_TOP_Y = 782
BOX_BOTTOM_Y = 883
BOX_CENTER_Y = 832.5

# 6 个标准大音轨定义 (各占 2 个小轨)
MAIN_LANES = [
    {"key": "A", "sub": "轨1-2",   "cx": 252,  "color": QColor(0, 229, 255)},    # 荧光青
    {"key": "S", "sub": "轨3-4",   "cx": 533,  "color": QColor(64, 196, 255)},   # 亮天蓝
    {"key": "D", "sub": "轨5-6",   "cx": 814,  "color": QColor(0, 230, 118)},    # 翡翠绿
    {"key": "J", "sub": "轨7-8",   "cx": 1098, "color": QColor(255, 215, 0)},    # 琥珀金
    {"key": "K", "sub": "轨9-10",  "cx": 1380, "color": QColor(255, 64, 129)},   # 霓虹粉
    {"key": "L", "sub": "轨11-12", "cx": 1660, "color": QColor(224, 64, 251)},   # 魅惑紫
]

# 5 个相邻大轨合击键定义 (位于对应两键正中间的下方)
COMBO_LANES = [
    {"key": "Z", "lanes": "A + S", "cx": (252 + 533) / 2,   "color": QColor(0, 229, 255)},
    {"key": "X", "lanes": "S + D", "cx": (533 + 814) / 2,   "color": QColor(64, 220, 180)},
    {"key": "C", "lanes": "D + J", "cx": (814 + 1098) / 2,  "color": QColor(200, 230, 80)},
    {"key": "V", "lanes": "J + K", "cx": (1098 + 1380) / 2, "color": QColor(255, 170, 40)},
    {"key": "B", "lanes": "K + L", "cx": (1380 + 1660) / 2, "color": QColor(255, 64, 180)},
]

# 12 个小音轨中心 X 坐标
SUB_TRACKS_X = [
    181, 321, 462, 603, 743, 885,
    1027, 1168, 1310, 1450, 1591, 1729
]

class PjskKeyOverlay(QWidget):
    def __init__(self, offset_y=0, opacity=1.0, show_sub_tracks=False, show_combos=True):
        super().__init__()
        self.offset_y = offset_y
        self.global_opacity = opacity
        self.show_sub_tracks = show_sub_tracks
        self.show_combos = show_combos

        self.setWindowTitle("PJSK_KEY_OVERLAY")
        # 强制设置无边框、置顶、鼠标输入完全穿透、工具窗口
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.WindowTransparentForInput |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self.setGeometry(0, 0, SCREEN_W, SCREEN_H)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setOpacity(self.global_opacity)

        base_y = BOX_CENTER_Y + self.offset_y

        # ==================== 1. 判定线内：6 大轨主按键徽章 [A] [S] [D] [J] [K] [L] ====================
        badge_w = 70
        badge_h = 48
        badge_r = 10

        for item in MAIN_LANES:
            cx = item["cx"]
            key_name = item["key"]
            sub_name = item["sub"]
            theme_color = item["color"]

            bx = cx - badge_w / 2
            by = base_y - badge_h / 2

            # 背景：高对比暗色半透明圆角底板
            rect = QRectF(bx, by, badge_w, badge_h)
            path = QPainterPath()
            path.addRoundedRect(rect, badge_r, badge_r)

            grad = QLinearGradient(bx, by, bx, by + badge_h)
            grad.setColorAt(0.0, QColor(14, 20, 32, 215))
            grad.setColorAt(1.0, QColor(5, 9, 18, 235))
            painter.fillPath(path, grad)

            # 边框发光
            pen = QPen(theme_color, 2.4)
            painter.strokePath(path, pen)

            # 大按键字母 (A, S, D, J, K, L)
            painter.setFont(QFont("Cantarell", 23, QFont.Weight.ExtraBold))
            painter.setPen(QColor(255, 255, 255))
            letter_rect = QRectF(bx, by - 4, badge_w, badge_h * 0.72)
            painter.drawText(letter_rect, Qt.AlignmentFlag.AlignCenter, key_name)

            # 轨道角标 (如 轨1-2)
            painter.setFont(QFont("Noto Sans", 9, QFont.Weight.Medium))
            painter.setPen(QColor(theme_color.red(), theme_color.green(), theme_color.blue(), 230))
            sub_rect = QRectF(bx, by + badge_h * 0.58, badge_w, badge_h * 0.38)
            painter.drawText(sub_rect, Qt.AlignmentFlag.AlignCenter, sub_name)

        # ==================== 2. 判定线正下方：大尺寸合击徽标 [ Z : A+S ] ====================
        # 位置：位于下方 A 和 S（以及 S 和 D、D 和 J 等）两键正中间的下方
        if self.show_combos:
            combo_w = 126   # 大尺寸宽度
            combo_h = 44    # 大尺寸高度
            combo_r = 9
            # 放在判定框底边 (BOX_BOTTOM_Y=883) 下方 14 像素处
            combo_y = (BOX_BOTTOM_Y + self.offset_y) + 14

            for cb in COMBO_LANES:
                ccx = cb["cx"]
                ckey = cb["key"]
                clanes = cb["lanes"]
                ccolor = cb["color"]

                cbx = ccx - combo_w / 2
                crect = QRectF(cbx, combo_y, combo_w, combo_h)
                cpath = QPainterPath()
                cpath.addRoundedRect(crect, combo_r, combo_r)

                # 渐变底板填充
                cgrad = QLinearGradient(cbx, combo_y, cbx, combo_y + combo_h)
                cgrad.setColorAt(0.0, QColor(18, 24, 38, 225))
                cgrad.setColorAt(1.0, QColor(8, 12, 22, 245))
                painter.fillPath(cpath, cgrad)

                # 醒目发光外边框
                painter.strokePath(cpath, QPen(ccolor, 2.2))

                # 左半部分：大合击按键名 [Z]
                painter.setFont(QFont("Cantarell", 17, QFont.Weight.ExtraBold))
                painter.setPen(ccolor)
                key_rect = QRectF(cbx + 8, combo_y, 32, combo_h)
                painter.drawText(key_rect, Qt.AlignmentFlag.AlignCenter, ckey)

                # 分隔线
                painter.setPen(QPen(QColor(255, 255, 255, 70), 1.2))
                painter.drawLine(cbx + 44, combo_y + 8, cbx + 44, combo_y + combo_h - 8)

                # 右半部分：大字标明对应双键 [A + S]
                painter.setFont(QFont("Cantarell", 14, QFont.Weight.Bold))
                painter.setPen(QColor(255, 255, 255))
                lanes_rect = QRectF(cbx + 48, combo_y, combo_w - 52, combo_h)
                painter.drawText(lanes_rect, Qt.AlignmentFlag.AlignCenter, clanes)

        # ==================== 3. 12 单小音轨编号 [1] ~ [12] (默认可选) ====================
        if self.show_sub_tracks:
            num_y = (BOX_BOTTOM_Y + self.offset_y) + 68
            num_w = 26
            num_h = 18

            for idx, tx in enumerate(SUB_TRACKS_X, start=1):
                nx = tx - num_w / 2
                nrect = QRectF(nx, num_y, num_w, num_h)
                npath = QPainterPath()
                npath.addRoundedRect(nrect, 4, 4)

                painter.fillPath(npath, QBrush(QColor(8, 12, 20, 190)))
                painter.strokePath(npath, QPen(QColor(180, 200, 220, 150), 1.0))

                painter.setFont(QFont("Cantarell", 9, QFont.Weight.Bold))
                painter.setPen(QColor(220, 235, 255))
                painter.drawText(nrect, Qt.AlignmentFlag.AlignCenter, str(idx))


def enforce_kwin_keep_above():
    """通过 KWin Scripting D-Bus 接口，强制指定窗口永久保持置顶且跳过任务栏"""
    js_code = """
    function pinOverlay(win) {
        if (win && win.caption && win.caption.indexOf("PJSK_KEY_OVERLAY") !== -1) {
            win.keepAbove = true;
            win.skipTaskbar = true;
            win.skipPager = true;
            win.skipSwitcher = true;
        }
    }
    var wins = workspace.windowList();
    for (var i = 0; i < wins.length; i++) {
        pinOverlay(wins[i]);
    }
    workspace.windowAdded.connect(pinOverlay);
    """
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(js_code)
            tmp_path = f.name

        cmd = ["busctl", "--address=unix:path=/run/user/1000/bus", "call",
               "org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", "loadScript", "s", tmp_path]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
        if res.returncode == 0:
            script_id = res.stdout.strip().split()[-1]
            start_cmd = ["busctl", "--address=unix:path=/run/user/1000/bus", "call",
                         "org.kde.KWin", f"/Scripting/Script{script_id}", "org.kde.kwin.Script", "run"]
            subprocess.run(start_cmd, capture_output=True, timeout=2)
        os.unlink(tmp_path)
    except Exception as e:
        pass


def main():
    parser = argparse.ArgumentParser(description="PJSK 判定线屏幕置顶透明键位字幕")
    parser.add_argument("--offset-y", type=int, default=0, help="判定线 Y 轴垂直微调偏移量（像素）")
    parser.add_argument("--opacity", type=float, default=1.0, help="字幕总体不透明度 (0.1 ~ 1.0)")
    parser.add_argument("--show-sub", action="store_true", help="显示底部的 1~12 小轨细分数字编号")
    parser.add_argument("--hide-combos", action="store_true", help="隐藏下方的合击键 (Z/X/C/V/B) 徽标")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    app.setApplicationName("pjsk-overlay")

    overlay = PjskKeyOverlay(
        offset_y=args.offset_y,
        opacity=args.opacity,
        show_sub_tracks=args.show_sub,
        show_combos=not args.hide_combos
    )
    overlay.show()

    # 保持 Python 解释器周期性运行以秒级响应 Ctrl+C (SIGINT)
    sig_timer = QTimer()
    sig_timer.timeout.connect(lambda: None)
    sig_timer.start(200)

    # 窗口弹出后立即通过 KWin D-Bus 赋予 keepAbove 置顶
    QTimer.singleShot(200, enforce_kwin_keep_above)
    timer = QTimer()
    timer.timeout.connect(enforce_kwin_keep_above)
    timer.start(4000)

    print("================================================================", flush=True)
    print("[+] PJSK 专属判定线永久置顶透明字幕已启动！", flush=True)
    print("    - 判定线各大音轨位置已就绪：", flush=True)
    print("      [A] 轨1-2  |  [S] 轨3-4  |  [D] 轨5-6", flush=True)
    print("      [J] 轨7-8  |  [K] 轨9-10 |  [L] 轨11-12", flush=True)
    print("    - 判定线正下方·大尺寸双轨合击徽标：", flush=True)
    print("      [ Z : A+S ] 显示在 A 与 S 正中间下方", flush=True)
    print("      [ X : S+D ] 显示在 S 与 D 正中间下方", flush=True)
    print("      [ C : D+J ] 显示在 D 与 J 正中间下方", flush=True)
    print("      [ V : J+K ] 显示在 J 与 K 正中间下方", flush=True)
    print("      [ B : K+L ] 显示在 K 与 L 正中间下方", flush=True)
    print("    - 特性：判定线上方视野 100% 纯净、纯透明无阻挡、Ctrl+C 秒退。", flush=True)
    print("================================================================", flush=True)

    try:
        sys.exit(app.exec())
    except KeyboardInterrupt:
        print("\n[+] 字幕已安全退出。", flush=True)


if __name__ == "__main__":
    main()
