"""在客户端内置浏览器里执行按键操作（硬刷新等）。

前置窗口 -> 点页面空白处拿焦点 -> 发组合键 -> 多帧抓拍。
"""
import argparse
import json
import sys
import time

import ctypes
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

import win32api
import win32con
import win32gui

from foreground import force_foreground, owner_at
from os_probe_keys import INPUT, INPUT_KEYBOARD, KEYBDINPUT, KEYEVENTF_KEYUP, _send
from win_shot import capture

VK = {"ctrl": 0x11, "shift": 0x10, "alt": 0x12, "r": 0x52, "f5": 0x74,
      "f12": 0x7B, "esc": 0x1B, "enter": 0x0D, "l": 0x4C, "tab": 0x09,
      "a": 0x41, "c": 0x43, "v": 0x56, "end": 0x23, "home": 0x24}


def tap(vk, up=False):
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.union.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, None)
    _send(inp)
    time.sleep(0.05)


def combo(spec):
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    vks = [VK[p] for p in parts if p in VK]
    for v in vks:
        tap(v)
    time.sleep(0.08)
    for v in reversed(vks):
        tap(v, up=True)
    time.sleep(0.1)


def type_text(s):
    """逐字符以 Unicode 注入，能打任意 ASCII/中文。

    dwFlags 必须是 UNICODE(0x4) 与 UNICODE|KEYUP(0x6) 成对出现，
    少了 KEYUP 字符会卡在按下态，浏览器收不到完整 keystroke。
    """
    for ch in s:
        code = ord(ch)
        for flag in (0x0004, 0x0006):
            inp = INPUT(type=INPUT_KEYBOARD)
            inp.union.ki = KEYBDINPUT(0, code, flag, 0, None)
            _send(inp)
        time.sleep(0.012)


def click_at(x, y):
    win32api.SetCursorPos((int(x), int(y)))
    time.sleep(0.2)
    for flag in (win32con.MOUSEEVENTF_LEFTDOWN, win32con.MOUSEEVENTF_LEFTUP):
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.mouse_event(flag, 0, 0, 0, 0)
        time.sleep(0.06)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hwnd", type=int, required=True)
    ap.add_argument("--keys", action="append", default=[],
                    help="可多次传，如 --keys ctrl+shift+r")
    ap.add_argument("--type", dest="text", help="要输入的文本（在前置后用 Unicode 注入）")
    ap.add_argument("--post-keys", action="append", default=[],
                    help="在输入文本之后发送的按键，如 --post-keys enter")
    ap.add_argument("--pre-keys", action="append", default=[],
                    help="在点击之前发送的按键（如先 esc 关掉下拉）")
    ap.add_argument("--backspaces", type=int, default=0,
                    help="输入前先发 N 次 Backspace 清空")
    ap.add_argument("--click", help="x,y 先把焦点点到这个位置")
    ap.add_argument("--wait", type=float, default=6.0)
    ap.add_argument("--shots", type=int, default=2)
    ap.add_argument("--prefix", default="act")
    a = ap.parse_args()

    ok, fg = force_foreground(a.hwnd)
    res = {"foreground_ok": ok, "foreground": fg, "keys": a.keys}

    for spec in a.pre_keys:
        combo(spec)
        time.sleep(0.3)

    if a.click:
        x, y = [float(v) for v in a.click.split(",")]
        owner, title = owner_at(x, y)
        res["owner_at_click"] = owner
        if owner != a.hwnd:
            print(json.dumps({**res, "error": "click_target_covered",
                              "owner_title": title}, ensure_ascii=False))
            return 1
        click_at(x, y)
        time.sleep(0.5)

    for spec in a.keys:
        combo(spec)
        time.sleep(0.35)

    if a.backspaces:
        for _ in range(a.backspaces):
            tap(0x08)
            tap(0x08, up=True)
            time.sleep(0.015)
        time.sleep(0.2)

    if a.text:
        type_text(a.text)
        res["typed"] = a.text
        time.sleep(0.3)

    for spec in a.post_keys:
        combo(spec)
        time.sleep(0.3)

    shots = []
    for i in range(a.shots):
        time.sleep(a.wait if i == 0 else a.wait * 0.6)
        f = f"{a.prefix}_{i}.png"
        capture(a.hwnd, f)
        shots.append(f)

    print(json.dumps({**res, "shots": shots}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
