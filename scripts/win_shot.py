#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
win_shot.py — 截取指定窗口（不依赖 CDP / a11y），输出 PNG + 坐标映射

用法:
  python win_shot.py --window "ChatGPT" --out shot.png
  python win_shot.py --hwnd 123456 --out shot.png
  python win_shot.py --list                 列出可截图的顶层窗口

输出 JSON:
  {"hwnd":..., "title":..., "rect":[l,t,r,b], "size":[w,h],
   "capture":"printwindow|bitblt", "out":"shot.png",
   "map":"图片像素(px,py) -> 屏幕坐标 (rect[0]+px, rect[1]+py)"}
"""
import argparse
import ctypes
import json
import sys

# DPI 统一：必须在任何窗口 API 之前调用，否则 GetWindowRect 返回的是被
# 系统虚拟化压缩过的逻辑像素（1536x864），而实际屏幕是 1920x1080。
# 混用会导致"图片像素 + rect 原点"算出来的屏幕坐标整体偏移 20%。
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import win32gui
import win32ui
import win32con
from PIL import Image

PW_RENDERFULLCONTENT = 0x00000002


def _to_image(bitmap):
    info = bitmap.GetInfo()
    buf = bitmap.GetBitmapBits(True)
    return Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]),
                            buf, "raw", "BGRX", 0, 1)


def capture(hwnd, out):
    """优先 PrintWindow(可截被遮挡/超屏窗口)，失败退 BitBlt 屏幕区域。"""
    left, top, right, bot = win32gui.GetWindowRect(hwnd)
    w, h = right - left, bot - top
    if w <= 0 or h <= 0:
        raise RuntimeError(f"窗口尺寸无效: {w}x{h}")

    mode = "printwindow"
    hwnd_dc = win32gui.GetWindowDC(hwnd)
    try:
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(mfc_dc, w, h)
        save_dc.SelectObject(bmp)
        ok = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(),
                                              PW_RENDERFULLCONTENT)
        img = _to_image(bmp)
        win32gui.DeleteObject(bmp.GetHandle())
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
    finally:
        win32gui.ReleaseDC(hwnd, hwnd_dc)

    # PrintWindow 返回 0 或全黑 -> 退 BitBlt
    if not ok or img.getbbox() is None or _is_blank(img):
        mode = "bitblt"
        hdc = win32gui.GetDC(0)
        src = win32ui.CreateDCFromHandle(hdc)
        mem = src.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(src, w, h)
        mem.SelectObject(bmp)
        mem.BitBlt((0, 0), (w, h), src, (left, top), win32con.SRCCOPY)
        img = _to_image(bmp)
        win32gui.DeleteObject(bmp.GetHandle())
        mem.DeleteDC()
        src.DeleteDC()
        win32gui.ReleaseDC(0, hdc)

    img.save(out)
    return {
        "hwnd": hwnd, "title": win32gui.GetWindowText(hwnd),
        "rect": [left, top, right, bot], "size": [w, h],
        "capture": mode, "out": out,
        "map": "图片像素(px,py) -> 屏幕坐标 (rect[0]+px, rect[1]+py)",
    }


def _is_blank(img):
    colors = img.getcolors(maxcolors=2048)
    return bool(colors) and len(colors) == 1


def find_window(title_hint):
    cands = []
    def cb(h, _):
        if not win32gui.IsWindowVisible(h):
            return
        t = win32gui.GetWindowText(h)
        if not t:
            return
        r = win32gui.GetWindowRect(h)
        if r[2] - r[0] < 200:
            return
        if title_hint.lower() in t.lower():
            cands.append((h, t, r))
    win32gui.EnumWindows(cb, None)
    return cands


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window")
    ap.add_argument("--hwnd", type=int)
    ap.add_argument("--out", default="shot.png")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        ctypes.windll.user32.SetProcessDPIAware()

    if a.list:
        rows = []
        win32gui.EnumWindows(
            lambda h, _: rows.append({
                "hwnd": h, "title": win32gui.GetWindowText(h),
                "class": win32gui.GetClassName(h),
                "rect": list(win32gui.GetWindowRect(h)),
            }) if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) else None,
            None)
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0

    if a.hwnd:
        hwnd = a.hwnd
    elif a.window:
        c = find_window(a.window)
        if not c:
            print(json.dumps({"error": "window_not_found", "hint": a.window},
                             ensure_ascii=False))
            return 1
        hwnd = c[0][0]
    else:
        ap.error("需要 --window 或 --hwnd")

    print(json.dumps(capture(hwnd, a.out), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
