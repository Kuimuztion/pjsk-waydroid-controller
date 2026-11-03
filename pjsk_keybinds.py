#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-only
# Copyright (C) 2026 Kuimuztion
"""
PJSK 12小轨 + 6大轨 + 偏轨/宽轨 + 底排合击 触控映射引擎（延迟修复版）

本版改动：
  1. 槽位改为 FIFO 分配。刚抬起的槽位排到队尾，下一次按下总是使用其他槽位。
     旧版使用 min(free_slots)，连续点同一个键时总是复用槽位 0，抬起和再次按下
     落在同一个游戏帧内，第二下容易被合并或延后处理。
  2. 缩短解释器 GIL 切换间隔，减少心跳线程对按键线程的干扰。
  3. 详细模式 (-v) 下输出"内核事件时间戳 -> 写入 uinput"的耗时，便于判断延迟来源。
  4. 键位映射保持不变。上滑（Space 等）仍需玩家手动按下，脚本只负责执行滑动手势。
"""

import sys
import time
import signal
import select
import threading
import subprocess
from collections import deque
from evdev import UInput, AbsInfo, ecodes as e, InputDevice, list_devices

# 默认 GIL 切换间隔为 5ms，心跳线程运行时按键线程可能等待数毫秒
sys.setswitchinterval(0.001)

SCREEN_W = 1920
SCREEN_H = 1080
JUDGE_Y = 845  # 判定线标准 Y 坐标
NUM_SLOTS = 10  # 硬件槽位数量 0~9
KEEPALIVE_INTERVAL = 0.030  # 长条心跳间隔（秒）

# 12 个小音轨（Sub-tracks 1..12）中心横坐标
SUB_TRACKS = {
    1:  181,
    2:  321,
    3:  462,
    4:  603,
    5:  743,
    6:  885,
    7:  1027,
    8:  1168,
    9:  1310,
    10: 1450,
    11: 1591,
    12: 1729,
}

# 6 个标准大音轨（各占 2 个小轨）中心横坐标
BIG_LANES = {
    1: 251,   # 轨 1-2 (A)
    2: 533,   # 轨 3-4 (S)
    3: 814,   # 轨 5-6 (D)
    4: 1098,  # 轨 7-8 (J)
    5: 1380,  # 轨 9-10 (K)
    6: 1660,  # 轨 11-12 (L)
}

# 偏轨与特殊宽轨
SPECIAL_LANES = {
    "2-3":   391,   # 跨轨 2-3 (W)
    "4-5":   673,   # 跨轨 4-5 (E)
    "4-6":   744,   # 跨 4-6 三轨宽按键 (R)
    "8-9":   1239,  # 跨轨 8-9 (U)
    "10-11": 1521,  # 跨轨 10-11 (I)
}

# 物理按键与触控坐标完整映射表（targets 包含一个或多个横坐标）
KEY_MAP = {
    # ---------------- 底排复合按键：大按键/双押合击 (Z, X, C, V, B) ----------------
    e.KEY_Z:     {"name": "合击 [Z] (A+S)",     "targets": [BIG_LANES[1], BIG_LANES[2]]},
    e.KEY_X:     {"name": "合击 [X] (S+D)",     "targets": [BIG_LANES[2], BIG_LANES[3]]},
    e.KEY_C:     {"name": "合击 [C] (D+J)",     "targets": [BIG_LANES[3], BIG_LANES[4]]},
    e.KEY_V:     {"name": "合击 [V] (J+K)",     "targets": [BIG_LANES[4], BIG_LANES[5]]},
    e.KEY_B:     {"name": "合击 [B] (K+L)",     "targets": [BIG_LANES[5], BIG_LANES[6]]},
    # 兼容备用别名
    e.KEY_N:     {"name": "合击 [N] (J+K)",     "targets": [BIG_LANES[4], BIG_LANES[5]]},
    e.KEY_M:     {"name": "合击 [M] (J+K)",     "targets": [BIG_LANES[4], BIG_LANES[5]]},
    e.KEY_COMMA: {"name": "合击 [,] (K+L)",     "targets": [BIG_LANES[5], BIG_LANES[6]]},
    e.KEY_DOT:   {"name": "合击 [.] (K+L)",     "targets": [BIG_LANES[5], BIG_LANES[6]]},
    e.KEY_SLASH: {"name": "合击 [/] (K+L)",     "targets": [BIG_LANES[5], BIG_LANES[6]]},

    # ---------------- 顶排数字键：12 个单小音轨（1:1 精细打击） ----------------
    e.KEY_1:     {"name": "小轨 1",      "targets": [SUB_TRACKS[1]]},
    e.KEY_2:     {"name": "小轨 2",      "targets": [SUB_TRACKS[2]]},
    e.KEY_3:     {"name": "小轨 3",      "targets": [SUB_TRACKS[3]]},
    e.KEY_4:     {"name": "小轨 4",      "targets": [SUB_TRACKS[4]]},
    e.KEY_5:     {"name": "小轨 5",      "targets": [SUB_TRACKS[5]]},
    e.KEY_6:     {"name": "小轨 6",      "targets": [SUB_TRACKS[6]]},
    e.KEY_7:     {"name": "小轨 7",      "targets": [SUB_TRACKS[7]]},
    e.KEY_8:     {"name": "小轨 8",      "targets": [SUB_TRACKS[8]]},
    e.KEY_9:     {"name": "小轨 9",      "targets": [SUB_TRACKS[9]]},
    e.KEY_0:     {"name": "小轨 10",     "targets": [SUB_TRACKS[10]]},
    e.KEY_MINUS: {"name": "小轨 11",     "targets": [SUB_TRACKS[11]]},
    e.KEY_EQUAL: {"name": "小轨 12",     "targets": [SUB_TRACKS[12]]},

    # ---------------- 主键位行：6 个标准大音轨 ----------------
    e.KEY_A: {"name": "大轨 1 (轨1-2)",   "targets": [BIG_LANES[1]]},
    e.KEY_S: {"name": "大轨 2 (轨3-4)",   "targets": [BIG_LANES[2]]},
    e.KEY_D: {"name": "大轨 3 (轨5-6)",   "targets": [BIG_LANES[3]]},
    e.KEY_F: {"name": "大轨 4 (轨7-8)",   "targets": [BIG_LANES[4]]},
    e.KEY_J: {"name": "大轨 4 (轨7-8)",   "targets": [BIG_LANES[4]]},
    e.KEY_G: {"name": "大轨 5 (轨9-10)",  "targets": [BIG_LANES[5]]},
    e.KEY_K: {"name": "大轨 5 (轨9-10)",  "targets": [BIG_LANES[5]]},
    e.KEY_H: {"name": "大轨 6 (轨11-12)", "targets": [BIG_LANES[6]]},
    e.KEY_L: {"name": "大轨 6 (轨11-12)", "targets": [BIG_LANES[6]]},

    # ---------------- 上排字母键：偏轨与宽轨 ----------------
    e.KEY_W: {"name": "偏轨 2-3",        "targets": [SPECIAL_LANES["2-3"]]},
    e.KEY_E: {"name": "偏轨 4-5",        "targets": [SPECIAL_LANES["4-5"]]},
    e.KEY_R: {"name": "宽轨 4-6",        "targets": [SPECIAL_LANES["4-6"]]},
    e.KEY_U: {"name": "偏轨 8-9",        "targets": [SPECIAL_LANES["8-9"]]},
    e.KEY_I: {"name": "偏轨 10-11",      "targets": [SPECIAL_LANES["10-11"]]},

    # ---------------- 小键盘区 ----------------
    e.KEY_KP1: {"name": "大轨 1 (KP1)",   "targets": [BIG_LANES[1]]},
    e.KEY_KP2: {"name": "大轨 2 (KP2)",   "targets": [BIG_LANES[2]]},
    e.KEY_KP3: {"name": "大轨 3 (KP3)",   "targets": [BIG_LANES[3]]},
    e.KEY_KP4: {"name": "大轨 4 (KP4)",   "targets": [BIG_LANES[4]]},
    e.KEY_KP5: {"name": "大轨 5 (KP5)",   "targets": [BIG_LANES[5]]},
    e.KEY_KP6: {"name": "大轨 6 (KP6)",   "targets": [BIG_LANES[6]]},
    e.KEY_KP7: {"name": "小轨 1 (KP7)",   "targets": [SUB_TRACKS[1]]},
    e.KEY_KP8: {"name": "小轨 6 (KP8)",   "targets": [SUB_TRACKS[6]]},
    e.KEY_KP9: {"name": "小轨 12 (KP9)",  "targets": [SUB_TRACKS[12]]},
}

# 划键 (Flick)，由玩家手动按下触发
FLICK_KEYS = {
    e.KEY_SPACE:   "空格键 (Space)",
    e.KEY_KP0:     "小键盘 0 (KP0)",
    e.KEY_INSERT:  "小键盘 0 (Insert)",
    e.KEY_KPENTER: "小键盘 Enter",
    e.KEY_KPPLUS:  "小键盘 +",
}

capabilities = {
    e.EV_ABS: [
        (e.ABS_X, AbsInfo(0, 0, SCREEN_W, 0, 0, 0)),
        (e.ABS_Y, AbsInfo(0, 0, SCREEN_H, 0, 0, 0)),
        (e.ABS_MT_SLOT, AbsInfo(0, 0, NUM_SLOTS - 1, 0, 0, 0)),
        (e.ABS_MT_TRACKING_ID, AbsInfo(0, 0, 65535, 0, 0, 0)),
        (e.ABS_MT_POSITION_X, AbsInfo(0, 0, SCREEN_W, 0, 0, 0)),
        (e.ABS_MT_POSITION_Y, AbsInfo(0, 0, SCREEN_H, 0, 0, 0)),
        (e.ABS_MT_PRESSURE, AbsInfo(50, 0, 255, 0, 0, 0)),
    ],
    e.EV_KEY: [e.BTN_TOUCH],
}

try:
    ui = UInput(
        events=capabilities,
        input_props=[e.INPUT_PROP_DIRECT],
        name="PJSK MultiTouch 12Lane Engine",
        version=0x3
    )
except Exception as ex:
    print(f"[-] 创建虚拟直接触摸设备失败: {ex}", flush=True)
    print("[-] 请使用 sudo 运行本脚本：sudo python3 ~/Documents/pjsk_keybinds.py", flush=True)
    sys.exit(1)

# 原生安全退出处理器
def emergency_exit(sig, frame):
    print("\n[+] 收到中断信号，正在瞬时安全退出...", flush=True)
    try:
        ui.close()
    except Exception:
        pass
    sys.exit(0)

signal.signal(signal.SIGINT, emergency_exit)
signal.signal(signal.SIGTERM, emergency_exit)

def bind_kwin_output():
    time.sleep(0.4)
    try:
        cmd = ["busctl", "--address=unix:path=/run/user/1000/bus", "call",
               "org.kde.KWin", "/org/kde/KWin/InputDevice",
               "org.kde.KWin.InputDeviceManager", "ListTouch"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
        if res.returncode == 0:
            lines = res.stdout.strip().split()
            if len(lines) >= 3:
                node = lines[-1].strip('"')
                set_cmd = ["busctl", "--address=unix:path=/run/user/1000/bus", "set-property",
                           "org.kde.KWin", f"/org/kde/KWin/InputDevice/{node}",
                           "org.kde.KWin.InputDevice", "outputName", "s", "HDMI-A-1"]
                subprocess.run(set_cmd, capture_output=True, timeout=2)
                print(f"[+] KWin 已将虚拟触控设备 {node} 成功绑定至显示屏 HDMI-A-1", flush=True)
    except Exception:
        pass

threading.Thread(target=bind_kwin_output, daemon=True).start()

keyboards = []
for path in list_devices():
    try:
        dev = InputDevice(path)
        name_lower = dev.name.lower()
        if ("keyboard" in name_lower or "kbd" in name_lower) and "virtual" not in name_lower:
            keyboards.append(dev)
            print(f"[+] 监听键盘设备: {dev.name} ({dev.path})", flush=True)
    except PermissionError:
        continue

if not keyboards:
    print("[-] 无法读取键盘。请使用 sudo 运行本脚本：", flush=True)
    print("    sudo python3 ~/Documents/pjsk_keybinds.py", flush=True)
    sys.exit(1)

print("\n" + "=" * 68, flush=True)
print("[+] PJSK 12小轨 + 6大轨 + [Z/X/C/V/B]并轨合击触控引擎就绪！（延迟修复版）", flush=True)
print("    - 【大按键/并轨合击】:", flush=True)
print("      [Z]=A+S轨并击  |  [X]=S+D轨并击  |  [C]=D+J中桥并击", flush=True)
print("      [V]=J+K轨并击  |  [B]=K+L轨并击", flush=True)
print("    - 【6大标准音轨】 : [A S D J K L] 独立单轨打击", flush=True)
print("    - 【12单小音轨】  : 主键盘数字 [1 到 =] 1:1 独立单轨打击", flush=True)
print("    - 【划键Flick】   : [Space 空格键] 或 小键盘 0 / Enter / +", flush=True)
print("    - 【退出说明】    : 随时按 Ctrl + C 即可瞬时完全退出！", flush=True)
print("    - 【延迟诊断】    : 加 -v 参数可显示每次按下的内核到写入延迟", flush=True)
print("=" * 68, flush=True)
print("[+] 保持终端在后台运行，切回游戏即可直接畅快打歌！\n", flush=True)

# ---------------- 状态与锁 ----------------
# FIFO 槽位队列：队首取用，抬起后放到队尾。
# 这样同一个槽位不会在刚抬起之后立即被再次使用。
free_slots = deque(range(NUM_SLOTS))
# key_code -> [{"slot": slot, "tid": tid, "x": x, "y": y}, ...]
active_touches = {}
_tid = 1000
last_active_x = 960
ui_lock = threading.Lock()

VERBOSE = ("-v" in sys.argv or "--verbose" in sys.argv)


def _next_tid():
    """生成下一个 TrackingID（1000..65000 循环），调用方须持有 ui_lock。"""
    global _tid
    _tid = _tid + 1 if _tid < 65000 else 1000
    return _tid


def _alloc_slot():
    """取出最久未使用的槽位，调用方须持有 ui_lock。"""
    return free_slots.popleft() if free_slots else None


def _free_slot(slot):
    """归还槽位到队尾，调用方须持有 ui_lock。"""
    free_slots.append(slot)


def _all_released():
    """所有槽位都空闲，即没有任何触点（包括上滑）。"""
    return len(free_slots) == NUM_SLOTS


def keepalive_worker():
    """后台长按心跳：为正在按住的长条持续发送亚像素微动，防止长条断连。"""
    jitter = 0
    while True:
        time.sleep(KEEPALIVE_INTERVAL)
        with ui_lock:
            if not active_touches:
                continue
            jitter ^= 1
            for touch_list in active_touches.values():
                for info in touch_list:
                    ui.write(e.EV_ABS, e.ABS_MT_SLOT, info["slot"])
                    ui.write(e.EV_ABS, e.ABS_MT_POSITION_X, info["x"])
                    ui.write(e.EV_ABS, e.ABS_MT_POSITION_Y, JUDGE_Y + jitter)
            ui.syn()

threading.Thread(target=keepalive_worker, daemon=True).start()


def perform_single_flick(slot, x, y):
    steps = 4
    flick_dist = 280
    for i in range(1, steps + 1):
        cur_y = y - int(flick_dist * (i / steps))
        time.sleep(0.003)
        with ui_lock:
            ui.write(e.EV_ABS, e.ABS_MT_SLOT, slot)
            ui.write(e.EV_ABS, e.ABS_MT_POSITION_X, x)
            ui.write(e.EV_ABS, e.ABS_MT_POSITION_Y, cur_y)
            ui.syn()

    with ui_lock:
        ui.write(e.EV_ABS, e.ABS_MT_SLOT, slot)
        ui.write(e.EV_ABS, e.ABS_MT_TRACKING_ID, -1)
        _free_slot(slot)
        if not active_touches and _all_released():
            ui.write(e.EV_KEY, e.BTN_TOUCH, 0)
        ui.syn()


def trigger_flick():
    with ui_lock:
        slot = _alloc_slot()
        if slot is None:
            return
        tid = _next_tid()
        x = last_active_x

        ui.write(e.EV_ABS, e.ABS_MT_SLOT, slot)
        ui.write(e.EV_ABS, e.ABS_MT_TRACKING_ID, tid)
        ui.write(e.EV_ABS, e.ABS_MT_POSITION_X, x)
        ui.write(e.EV_ABS, e.ABS_MT_POSITION_Y, JUDGE_Y)
        ui.write(e.EV_ABS, e.ABS_MT_PRESSURE, 60)
        ui.write(e.EV_KEY, e.BTN_TOUCH, 1)
        ui.syn()

    threading.Thread(target=perform_single_flick, args=(slot, x, JUDGE_Y), daemon=True).start()


def press_key(code, kinfo, ts):
    """按下：为每个目标分配独立槽位，在同一帧内发出所有触点。
    ts 为内核事件时间戳（秒），仅用于 -v 诊断。"""
    global last_active_x
    targets = kinfo["targets"]
    last_active_x = targets[0]

    with ui_lock:
        if code in active_touches:
            return
        touch_list = []
        for tx in targets:
            slot = _alloc_slot()
            if slot is None:
                break
            tid = _next_tid()
            touch_list.append({"slot": slot, "tid": tid, "x": tx, "y": JUDGE_Y})

            ui.write(e.EV_ABS, e.ABS_MT_SLOT, slot)
            ui.write(e.EV_ABS, e.ABS_MT_TRACKING_ID, tid)
            ui.write(e.EV_ABS, e.ABS_MT_POSITION_X, tx)
            ui.write(e.EV_ABS, e.ABS_MT_POSITION_Y, JUDGE_Y)
            ui.write(e.EV_ABS, e.ABS_MT_PRESSURE, 50)
            ui.write(e.EV_ABS, e.ABS_X, tx)
            ui.write(e.EV_ABS, e.ABS_Y, JUDGE_Y)

        if not touch_list:
            return
        active_touches[code] = touch_list
        ui.write(e.EV_KEY, e.BTN_TOUCH, 1)
        ui.syn()
        done = time.time()

    if VERBOSE:
        lat_ms = (done - ts) * 1000
        print(f"  [DOWN] {kinfo['name']} targets={targets} 延迟={lat_ms:.1f}ms", flush=True)


def release_key(code):
    """抬起：释放该键占用的全部槽位，并在同一帧内结束触点。"""
    with ui_lock:
        touch_list = active_touches.pop(code, None)
        if not touch_list:
            return
        for info in touch_list:
            ui.write(e.EV_ABS, e.ABS_MT_SLOT, info["slot"])
            ui.write(e.EV_ABS, e.ABS_MT_TRACKING_ID, -1)
            _free_slot(info["slot"])
        if not active_touches and _all_released():
            ui.write(e.EV_KEY, e.BTN_TOUCH, 0)
        ui.syn()


fd_map = {kb.fd: kb for kb in keyboards}
fd_list = list(fd_map.keys())

try:
    while True:
        r, _, _ = select.select(fd_list, [], [])

        for fd in r:
            kb = fd_map[fd]
            for event in kb.read():
                if event.type != e.EV_KEY:
                    continue

                code = event.code
                val = event.value  # 1: down, 0: up, 2: repeat（忽略）

                if code in KEY_MAP:
                    if val == 1:
                        press_key(code, KEY_MAP[code], event.timestamp())
                    elif val == 0:
                        release_key(code)

                elif code in FLICK_KEYS and val == 1:
                    trigger_flick()

except KeyboardInterrupt:
    print("\n[+] 映射服务已安全退出。", flush=True)
finally:
    try:
        ui.close()
    except Exception:
        pass
