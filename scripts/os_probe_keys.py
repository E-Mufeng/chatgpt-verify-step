#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
os_probe_keys.py — 对指定窗口发 OS 级按键（SendInput），用于给客户端内置浏览器开 DevTools

为什么走 OS 级：
    客户端（真 Chromium 壳）没有对外暴露 CDP 端口，也没有
    DevToolsActivePort 文件，脚本层进不去。但 Windows 的 SendInput 产生的是
    isTrusted=true 的真实硬件事件，浏览器无法区分，能直接触达 renderer。

子命令:
    keys   --hwnd H --click X,Y --send f12 [--wait 2.5] [--out shot.png]
    keys   --hwnd H --send ctrl+shift+i
"""
import argparse
import ctypes
import json
import os
import sys
import time
from ctypes import wintypes

# DPI 统一：先声明 Per-Monitor DPI Aware，之后 GetWindowRect / GetSystemMetrics /
# SendInput 全部走同一套物理像素，坐标才不会整体偏移。
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

try:
    import win32con
    import win32gui
except ImportError:
    print("need pywin32", file=sys.stderr)
    raise

user32 = ctypes.WinDLL("user32", use_last_error=True)

# ---------------------------------------------------------------- SendInput --
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _INPUTunion(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", _INPUTunion)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)


def _send(inp):
    n = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    if n != 1:
        raise OSError(f"SendInput failed: {ctypes.get_last_error()}")


def screen_size():
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def move_to(x, y):
    sw, sh = screen_size()
    nx = int(x * 65535 / (sw - 1))
    ny = int(y * 65535 / (sh - 1))
    inp = INPUT(type=INPUT_MOUSE)
    inp.union.mi = MOUSEINPUT(nx, ny, 0, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, 0, None)
    _send(inp)


def click(x, y):
    move_to(x, y)
    time.sleep(0.15)
    for flag in (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP):
        inp = INPUT(type=INPUT_MOUSE)
        inp.union.mi = MOUSEINPUT(0, 0, 0, flag, 0, None)
        _send(inp)
        time.sleep(0.05)


def _bez(p0, p1, p2, p3, t):
    """三次贝塞尔"""
    u = 1 - t
    return (u*u*u*p0[0] + 3*u*u*t*p1[0] + 3*u*t*t*p2[0] + t*t*t*p3[0],
            u*u*u*p0[1] + 3*u*u*t*p1[1] + 3*u*t*t*p2[1] + t*t*t*p3[1])


def human_click(tx, ty):
    """带贝塞尔轨迹与抖动的点击。Turnstile 会分析鼠标轨迹曲率，
    瞬移到目标再按下这个动作本身就是一个很强的机器信号。"""
    import random
    import win32api

    sx, sy = win32api.GetCursorPos()
    c1 = (sx + (tx - sx) * random.uniform(.15, .40) + random.uniform(-80, 80),
          sy + (ty - sy) * random.uniform(.15, .40) + random.uniform(-80, 80))
    c2 = (sx + (tx - sx) * random.uniform(.60, .90) + random.uniform(-50, 50),
          sy + (ty - sy) * random.uniform(.60, .90) + random.uniform(-50, 50))
    n = random.randint(32, 55)
    for i in range(1, n + 1):
        t = i / n
        x, y = _bez((sx, sy), c1, c2, (tx, ty), t * t * (3 - 2 * t))
        win32api.SetCursorPos((int(x + random.gauss(0, .6)),
                               int(y + random.gauss(0, .6))))
        time.sleep(random.uniform(0.004, 0.016))
    win32api.SetCursorPos((int(tx), int(ty)))
    time.sleep(random.uniform(.10, .26))
    for flag in (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP):
        inp = INPUT(type=INPUT_MOUSE)
        inp.union.mi = MOUSEINPUT(0, 0, 0, flag, 0, None)
        _send(inp)
        time.sleep(random.uniform(.06, .15))


VK = {"f12": 0x7B, "esc": 0x1B, "enter": 0x0D, "tab": 0x09,
      "ctrl": 0x11, "shift": 0x10, "alt": 0x12, "f5": 0x74,
      "f11": 0x7A, "del": 0x2E, "space": 0x20}


def key(vk, up=False):
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.union.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, None)
    _send(inp)
    time.sleep(0.04)


def send_combo(spec):
    """'f12' / 'ctrl+shift+i' / 'none'（none = 不发按键）"""
    if not spec or spec.strip().lower() in ("none", "-"):
        return
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    vks = []
    for p in parts:
        if p in VK:
            vks.append(VK[p])
        elif len(p) == 1:
            vks.append(ord(p.upper()))
        else:
            raise ValueError(f"unknown key: {p}")
    for v in vks:
        key(v, up=False)
    for v in reversed(vks):
        key(v, up=True)


def bring_front(hwnd):
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    except Exception:
        pass
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        # SetForegroundWindow 常被前台锁拒绝，绕一下：AttachThreadInput
        try:
            fg = win32gui.GetForegroundWindow()
            tid_fg = user32.GetWindowThreadProcessId(fg, None)
            tid_me = ctypes.windll.kernel32.GetCurrentThreadId()
            user32.AttachThreadInput(tid_fg, tid_me, True)
            win32gui.SetForegroundWindow(hwnd)
            user32.AttachThreadInput(tid_fg, tid_me, False)
        except Exception:
            pass
    time.sleep(0.5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hwnd", type=int, required=True)
    ap.add_argument("--click", help="屏幕坐标 X,Y（点击以获取焦点）")
    ap.add_argument("--human", action="store_true",
                    help="点击时使用贝塞尔人类轨迹（过 Turnstile 必须）")
    ap.add_argument("--send", required=True, help="按键组合，如 f12 / ctrl+shift+i")
    ap.add_argument("--wait", type=float, default=2.5)
    ap.add_argument("--out", default=None, help="完成后截图")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    hwnd = a.hwnd
    if not win32gui.IsWindow(hwnd):
        print(json.dumps({"error": "invalid hwnd"}), flush=True)
        return 1

    rect = win32gui.GetWindowRect(hwnd)
    res = {"hwnd": hwnd, "title": win32gui.GetWindowText(hwnd),
           "rect": list(rect), "send": a.send}

    if a.dry_run:
        res["dry_run"] = True
        print(json.dumps(res, ensure_ascii=False), flush=True)
        return 0

    bring_front(hwnd)
    res["foreground"] = win32gui.GetForegroundWindow() == hwnd

    if a.click:
        x, y = [float(v) for v in a.click.split(",")]
        if a.human:
            human_click(x, y)
        else:
            click(x, y)
        res["clicked"] = [x, y]
        res["human_track"] = bool(a.human)
        time.sleep(0.4)

    send_combo(a.send)
    res["sent"] = True

    time.sleep(a.wait)

    if a.out:
        # 复用 win_shot 的 printwindow
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        try:
            from win_shot import capture as capture_window
            capture_window(hwnd, a.out)
            res["out"] = a.out
        except Exception as e:
            res["shot_error"] = repr(e)[:200]

    print(json.dumps(res, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
