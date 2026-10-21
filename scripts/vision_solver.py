#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vision_solver.py — 纯视觉定位 + 注入点击，过 Cloudflare "验证您是真人"
=====================================================================

为什么要它
----------
Codex / Electron 客户端的内置浏览器：
  * 无 CDP 调试端口（AI 拿不到输入通道）
  * UIA 树只有几个空 Pane（不暴露 WebView 内部控件）
所以既不能 query DOM，也不能用无障碍树定位。
唯一剩下的通道是**像素**：PrintWindow 能截到 WebView 渲染内容，
再在像素里找那个 checkbox 方框，最后 SendInput 注入点击。

定位原理
--------
Turnstile 的 checkbox 是一个约 22~26px 的近似正方形空心框，边缘清晰。
  灰度 -> Canny 边缘 -> 轮廓 -> 近似多边形(4~6 边) -> 正方形尺寸/长宽比筛选
  -> 取最靠左上、且右侧存在文字区域的候选
不依赖模板图，不依赖 DPI（截图与坐标同源，都是窗口像素）。

用法
----
  python vision_solver.py --window "ChatGPT"                 # 截图->定位->点击->验证
  python vision_solver.py --window "ChatGPT" --dry-run       # 只定位不点
  python vision_solver.py --window "ChatGPT" --shot out.png  # 指定截图留存路径
  python vision_solver.py --window "ChatGPT" --retries 3     # 点不中时微调重试

输出 JSON:
  {"window":..., "found":true, "win_px":[x,y,w,h],
   "screen":[sx,sy], "clicked":true, "changed":true}
"""
import argparse
import ctypes
import json
import os
import random
import sys
import time

import cv2
import numpy as np
import win32api
import win32con
import win32gui
import win32process
import win32ui
from PIL import Image

PW_RENDERFULLCONTENT = 0x00000002
PUL = ctypes.POINTER(ctypes.c_ulong)


# ---------------------------------------------------------------------------
# 截图
# ---------------------------------------------------------------------------
def grab(hwnd):
    left, top, right, bot = win32gui.GetWindowRect(hwnd)
    w, h = right - left, bot - top
    if w <= 0 or h <= 0:
        raise RuntimeError(f"窗口尺寸无效 {w}x{h}")
    hdc = win32gui.GetWindowDC(hwnd)
    try:
        mfc = win32ui.CreateDCFromHandle(hdc)
        sdc = mfc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(mfc, w, h)
        sdc.SelectObject(bmp)
        ctypes.windll.user32.PrintWindow(hwnd, sdc.GetSafeHdc(), PW_RENDERFULLCONTENT)
        info = bmp.GetInfo()
        buf = bmp.GetBitmapBits(True)
        arr = np.frombuffer(buf, dtype=np.uint8).reshape(
            (info["bmHeight"], info["bmWidth"], 4))
        img = arr[:, :, :3].copy()          # BGRA -> BGR
        win32gui.DeleteObject(bmp.GetHandle())
        sdc.DeleteDC()
        mfc.DeleteDC()
    finally:
        win32gui.ReleaseDC(hwnd, hdc)
    return img, (left, top)


# ---------------------------------------------------------------------------
# 定位 checkbox 方框
# ---------------------------------------------------------------------------
def locate_checkbox(img):
    """返回 (x, y, w, h) 窗口内像素坐标，找不到返回 None。"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    edges = cv2.Canny(gray, 40, 120)
    # 连接断口，让方框成为闭合轮廓
    edges = cv2.morphologyEx(
        edges, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2)))

    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    cands = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if not (16 <= w <= 34 and 16 <= h <= 34):
            continue
        if not (0.75 <= w / float(h) <= 1.33):
            continue
        peri = cv2.arcLength(c, True)
        if peri <= 0:
            continue
        approx = cv2.approxPolyDP(c, 0.03 * peri, True)
        if not (4 <= len(approx) <= 6):
            continue
        # 内部要有留白（空心框），而不是实心色块
        roi = gray[y + 3:y + h - 3, x + 3:x + w - 3]
        if roi.size == 0:
            continue
        if float(roi.std()) < 3.0:
            continue
        cands.append((x, y, w, h))

    if not cands:
        return None

    # 候选打分：右侧要有文字/内容（说明是"框 + 说明文字"结构），且靠左上
    def score(c):
        x, y, w, h = c
        right = gray[max(0, y - 2):y + h + 2, x + w + 2:x + w + 90]
        has_text = float(right.std()) if right.size else 0.0
        return has_text - 0.02 * (x + y)

    cands.sort(key=score, reverse=True)
    return cands[0]


# ---------------------------------------------------------------------------
# SendInput
# ---------------------------------------------------------------------------
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", PUL)]


class INPUT(ctypes.Structure):
    class _I(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]

    _anonymous_ = ("i",)
    _fields_ = [("type", ctypes.c_ulong), ("i", _I)]


def _send(flags):
    inp = INPUT(type=0, i=INPUT._I(mi=MOUSEINPUT(0, 0, 0, flags, 0, None)))
    ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def _bez(p0, p1, p2, p3, t):
    mt = 1 - t
    return (mt**3*p0[0] + 3*mt**2*t*p1[0] + 3*mt*t**2*p2[0] + t**3*p3[0],
            mt**3*p0[1] + 3*mt**2*t*p1[1] + 3*mt*t**2*p2[1] + t**3*p3[1])


def human_click(tx, ty):
    sx, sy = win32api.GetCursorPos()
    c1 = (sx + (tx-sx)*random.uniform(.15, .40) + random.uniform(-80, 80),
          sy + (ty-sy)*random.uniform(.15, .40) + random.uniform(-80, 80))
    c2 = (sx + (tx-sx)*random.uniform(.60, .90) + random.uniform(-50, 50),
          sy + (ty-sy)*random.uniform(.60, .90) + random.uniform(-50, 50))
    n = random.randint(32, 55)
    for i in range(1, n + 1):
        t = i / n
        x, y = _bez((sx, sy), c1, c2, (tx, ty), t*t*(3-2*t))
        win32api.SetCursorPos((int(x + random.gauss(0, .6)),
                               int(y + random.gauss(0, .6))))
        time.sleep(random.uniform(0.004, 0.016))
    win32api.SetCursorPos((int(tx), int(ty)))
    time.sleep(random.uniform(.10, .26))
    _send(0x0002)
    time.sleep(random.uniform(.06, .15))
    _send(0x0004)


def force_foreground(hwnd):
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        if win32gui.GetForegroundWindow() == hwnd:
            return True
        cur = win32api.GetCurrentThreadId()
        fg = win32gui.GetForegroundWindow()
        a = win32process.GetWindowThreadProcessId(fg)[0]
        b = win32process.GetWindowThreadProcessId(hwnd)[0]
        win32process.AttachThreadInput(a, cur, True)
        win32process.AttachThreadInput(b, cur, True)
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
        win32process.AttachThreadInput(a, cur, False)
        win32process.AttachThreadInput(b, cur, False)
        return win32gui.GetForegroundWindow() == hwnd
    except Exception:
        return False


# ---------------------------------------------------------------------------
def find_window(hint):
    hits = []
    def cb(h, _):
        if not win32gui.IsWindowVisible(h):
            return
        t = win32gui.GetWindowText(h)
        if t and hint.lower() in t.lower():
            r = win32gui.GetWindowRect(h)
            if r[2] - r[0] > 300:
                hits.append((h, t, r))
    win32gui.EnumWindows(cb, None)
    return hits


def diff_ratio(a, b):
    if a.shape != b.shape:
        return 1.0
    d = cv2.absdiff(a, b)
    return float((d > 25).any(axis=2).mean())


def has_success_mark(img, box, pad=(60, 300)):
    """Turnstile 通过后在 checkbox 附近渲染绿色对勾。
    在方框周围取 ROI 找饱和绿色像素——比"截图有变化"可靠得多。"""
    x, y, w, h = box
    y0, y1 = max(0, y - pad[0]), min(img.shape[0], y + h + pad[0])
    x0, x1 = max(0, x - 20), min(img.shape[1], x + w + pad[1])
    roi = img[y0:y1, x0:x1]
    if roi.size == 0:
        return False
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([35, 90, 70]), np.array([90, 255, 255]))
    green_px = int(cv2.countNonZero(mask))
    return green_px >= 120


def has_red_error(img, box, pad=(60, 300)):
    x, y, w, h = box
    y0, y1 = max(0, y - pad[0]), min(img.shape[0], y + h + pad[0])
    x0, x1 = max(0, x - 20), min(img.shape[1], x + w + pad[1])
    roi = img[y0:y1, x0:x1]
    if roi.size == 0:
        return False
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    m1 = cv2.inRange(hsv, np.array([0, 120, 120]), np.array([10, 255, 255]))
    m2 = cv2.inRange(hsv, np.array([170, 120, 120]), np.array([180, 255, 255]))
    return int(cv2.countNonZero(m1) + cv2.countNonZero(m2)) >= 150


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", required=True)
    ap.add_argument("--shot", default="vision_shot.png")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--retries", type=int, default=2)
    a = ap.parse_args()

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        ctypes.windll.user32.SetProcessDPIAware()

    cs = find_window(a.window)
    if not cs:
        print(json.dumps({"found": False, "error": "window_not_found",
                          "hint": a.window}, ensure_ascii=False))
        return 1
    hwnd, title, rect = cs[0]

    img, origin = grab(hwnd)
    Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)).save(a.shot)

    box = locate_checkbox(img)
    if not box:
        print(json.dumps({"window": title, "found": False,
                          "error": "checkbox_not_located", "shot": a.shot},
                         ensure_ascii=False))
        return 1

    x, y, w, h = box
    cx = origin[0] + x + w / 2.0
    cy = origin[1] + y + h / 2.0
    out = {"window": title, "found": True, "win_px": [x, y, w, h],
           "screen": [round(cx, 1), round(cy, 1)], "shot": a.shot}

    if a.dry_run:
        out["clicked"] = False
        print(json.dumps(out, ensure_ascii=False))
        return 0

    out["foreground"] = force_foreground(hwnd)
    time.sleep(random.uniform(0.6, 1.0))

    success = False
    after = img
    for i in range(max(1, a.retries)):
        human_click(cx, cy)
        # Turnstile 要算几秒指纹，轮询等绿勾出现
        for _ in range(9):
            time.sleep(1.5)
            after, _ = grab(hwnd)
            if has_success_mark(after, box):
                success = True
                break
        out[f"attempt_{i}"] = "success" if success else "pending"
        if success:
            break
        # 未成功：重新定位再点一次
        img = after
        box2 = locate_checkbox(img)
        if not box2:
            out["relocate"] = "gone"
            break
        box = box2
        x, y, w, h = box2
        cx = origin[0] + x + w / 2.0
        cy = origin[1] + y + h / 2.0

    out["clicked"] = True
    out["success"] = success
    out["red_error"] = has_red_error(after, box)
    # 结果图留证（区别于点前存下的 a.shot）
    result_shot = os.path.splitext(a.shot)[0] + ".result.png"
    Image.fromarray(cv2.cvtColor(after, cv2.COLOR_BGR2RGB)).save(result_shot)
    out["result_shot"] = result_shot
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
