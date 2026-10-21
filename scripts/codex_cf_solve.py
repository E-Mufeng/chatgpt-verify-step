#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""codex_cf_solve.py — 在客户端内置浏览器（WebView）里自动完成 Cloudflare Turnstile 验证。

为什么必须是「像素 + 系统输入」：
    客户端是 OpenAI 定制的 Chromium（MSIX 包），
      1. 没有任何 CDP 端口（无 remote-debugging / DevToolsActivePort）
      2. UIA 无障碍树只暴露 16 个节点（窗口按钮），WebView 内部完全不开放
    所以脚本层既拿不到 DOM，也拿不到控件。唯一通道是：
      PrintWindow 截像素 -> OpenCV 定位 checkbox -> SendInput 注入真人轨迹点击

关键坑（都已处理，别再踩）：
    - 窗口被别的窗口盖住时，SendInput 会打到上层窗口上，widget 毫无反应。
      必须先 force_foreground（AttachThreadInput 法），并用 WindowFromPoint 复核。
    - Turnstile 的 iframe 是 0x0 长到 300x65，渲染要 10 秒；「尺寸为 0」不是已通过。
    - 白卡片下的 checkbox 是 24~32px 的近似正方形；直接用「最小方框」启发式会被
      文字笔画骗到，必须先定位白色卡片再在卡片内找。
    - 点击后 widget 会先转绿色「正在验证...」，再消失。消失才是通过。

用法:
    python codex_cf_solve.py                 # 自动找窗口、自动点、最多重试 3 次
    python codex_cf_solve.py --hwnd 590506   # 指定窗口
    python codex_cf_solve.py --dry-run       # 只定位不点击，输出坐标
    python codex_cf_solve.py                 # 定位→点击→收尾(关升级模态)→输出终态，一条跑完
    python codex_cf_solve.py --no-settle      # 只点验证框，不关后续弹层
    python codex_cf_solve.py --wait-home 30   # 收尾后再多等 30s 重抓终态图
"""
import argparse
import json
import random
import sys
import time

import cv2
import ctypes
import numpy as np

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

import win32api
import win32con
import win32gui

from foreground import force_foreground, owner_at
from os_probe_keys import INPUT, INPUT_MOUSE, MOUSEINPUT, \
    MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, _send
from win_shot import capture, find_window

# 让脚本无论从哪个 cwd 被调用都能找到兄弟模块（解决「路径写死」问题）
import os as _os
sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))

TMP = "_cf_shot.png"


# ---------------------------------------------------------------- 视觉定位 --
def locate(shot):
    """返回 dict(card=(x,y,w,h), box=(cx,cy,side)) 或 None。"""
    im = cv2.imread(shot)
    if im is None:
        return None
    g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)

    # 1) 白色卡片：Turnstile 的 widget 背景是纯白，页面是深色
    _, th = cv2.threshold(g, 200, 255, cv2.THRESH_BINARY)
    cs, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    card = None
    for c in cs:
        x, y, w, h = cv2.boundingRect(c)
        # 300x65 标准尺寸，放宽到 250~520 x 50~120
        if 240 <= w <= 540 and 45 <= h <= 130:
            a = cv2.contourArea(c)
            if card is None or a > card[4]:
                card = (x, y, w, h, a)
    if card is None:
        return None

    x, y, w, h, _ = card
    roi = g[y:y + h, x:x + w]

    # 2) 卡片内的方框：深色描边、近似正方形，落在左半区
    _, inv = cv2.threshold(roi, 150, 255, cv2.THRESH_BINARY_INV)
    cs2, _ = cv2.findContours(inv, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    box = None
    for c in cs2:
        bx, by, bw, bh = cv2.boundingRect(c)
        if not (16 <= bw <= 40 and 16 <= bh <= 40):
            continue
        if abs(bw - bh) > 7:
            continue
        if bx > w * 0.45:          # checkbox 一定在左侧
            continue
        # 轮廓要接近空心方框：周长/面积比高
        peri = cv2.arcLength(c, True)
        area = cv2.contourArea(c)
        if area <= 0:
            continue
        thin = peri * peri / area
        if thin < 45:              # 实心色块会被排除
            continue
        score = thin + area / 10.0
        if box is None or score > box[5]:
            box = (bx, by, bw, bh, area, score)

    if box is None:
        return {"card": (x, y, w, h), "box": None}
    bx, by, bw, bh, _, _ = box
    return {"card": (x, y, w, h), "box": (x + bx + bw / 2.0, y + by + bh / 2.0,
                                          (bw + bh) / 2.0)}


# ---------------------------------------------------------------- 输入注入 --
def _bez(p0, p1, p2, p3, t):
    u = 1 - t
    return (u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
            u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1])


def warmup(anchor, radius=170, steps=40):
    """在页面上自然游走，制造真人浏览信号。"""
    for _ in range(random.randint(2, 3)):
        tx = anchor[0] + random.uniform(-radius, radius)
        ty = anchor[1] + random.uniform(-radius * .55, radius * .55)
        sx, sy = win32api.GetCursorPos()
        c1 = (sx + (tx - sx) * .3 + random.uniform(-60, 60), sy + (ty - sy) * .3 + random.uniform(-60, 60))
        c2 = (sx + (tx - sx) * .75 + random.uniform(-40, 40), sy + (ty - sy) * .75 + random.uniform(-40, 40))
        for i in range(1, steps + 1):
            px, py = _bez((sx, sy), c1, c2, (tx, ty), i / steps)
            win32api.SetCursorPos((int(px + random.gauss(0, .5)), int(py + random.gauss(0, .5))))
            time.sleep(random.uniform(.008, .019))
        time.sleep(random.uniform(.18, .42))


def human_click(tx, ty):
    sx, sy = win32api.GetCursorPos()
    c1 = (sx + (tx - sx) * random.uniform(.15, .40) + random.uniform(-90, 90),
          sy + (ty - sy) * random.uniform(.15, .40) + random.uniform(-90, 90))
    c2 = (sx + (tx - sx) * random.uniform(.60, .90) + random.uniform(-55, 55),
          sy + (ty - sy) * random.uniform(.60, .90) + random.uniform(-55, 55))
    n = random.randint(34, 58)
    for i in range(1, n + 1):
        t = i / n
        px, py = _bez((sx, sy), c1, c2, (tx, ty), t * t * (3 - 2 * t))
        win32api.SetCursorPos((int(px + random.gauss(0, .5)), int(py + random.gauss(0, .5))))
        time.sleep(random.uniform(.004, .016))
    win32api.SetCursorPos((int(tx), int(ty)))
    time.sleep(random.uniform(.10, .26))
    for flag in (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP):
        inp = INPUT(type=INPUT_MOUSE)
        inp.union.mi = MOUSEINPUT(0, 0, 0, flag, 0, None)
        _send(inp)
        time.sleep(random.uniform(.07, .15))


# ---------------------------------------------------------------- 主流程 ----
def pick_window(hwnd=None):
    if hwnd:
        return hwnd, win32gui.GetWindowText(hwnd)
    # 优先用稳健定位器（进程优先 + 顶层窗口，避免「标题+面积最大」的脆弱性）
    try:
        from find_target_window import get_client_window
        h, t = get_client_window()
        if h:
            return h, t
    except Exception:
        pass
    # 兜底：旧版标题枚举（仅在定位器不可用时）
    for hint in ("ChatGPT", "Codex"):
        c = find_window(hint)
        if c:
            c.sort(key=lambda r: -(r[2][2] - r[2][0]) * (r[2][3] - r[2][1]))
            return c[0][0], c[0][1]
    return None, None


# ---------------------------------------------------------------- 收尾/稳态 --
def _send_esc():
    """向前台窗口发送一次 Esc（关闭营销/升级模态的通用手段）。"""
    win32api.keybd_event(win32con.VK_ESCAPE, 0, 0, 0)
    time.sleep(0.06)
    win32api.keybd_event(win32con.VK_ESCAPE, 0, win32con.KEYEVENTF_KEYUP, 0)


def detect_modal(gray):
    """升级/营销模态特征：深色主题下出现大面积亮白卡片（对比强烈）。
    返回 (max_bright_ratio, is_modal)。阈值保守：正常首页不会触发，只有
    整屏定价的白卡才会越过 5% 连通亮块阈值。不靠 OCR、不靠文字。"""
    H, W = gray.shape
    bright = (gray > 190).astype(np.uint8)
    nb, _, stats, _ = cv2.connectedComponents(bright, 8)
    area = stats[1:, cv2.CC_STAT_AREA].max() if nb > 1 else 0
    ratio = float(area) / (W * H)
    return ratio, ratio > 0.05


def settle(hwnd, left, top, shots=2):
    """验证通过后收尾：若页面停在升级/营销模态，关闭它，输出最终稳态。

    不做「没有验证框就当通过」的盲判——而是真去检测并关掉挡在前面的白卡，
    让页面落到可用态。失败（关不掉）如实返回 modal_closed:false，不掩盖。"""
    out = {"settle": True}
    last_modal = None
    for i in range(shots):
        time.sleep(2.0 if i else 1.2)
        capture(hwnd, TMP)
        im = cv2.imread(TMP)
        if im is None:
            out["settle_shot_ok"] = False
            continue
        g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
        ratio, modal = detect_modal(g)
        out[f"settle_{i}"] = {"bright_ratio": round(ratio, 4), "modal": bool(modal)}
        last_modal = modal
        if not modal:
            break
        # 有关闭不了的模态：先 Esc，再尝试点右上角关闭钮
        _send_esc()
        time.sleep(1.4)
        capture(hwnd, TMP)
        g2 = cv2.cvtColor(cv2.imread(TMP), cv2.COLOR_BGR2GRAY)
        _, modal2 = detect_modal(g2)
        if not modal2:
            out[f"settle_{i}"]["closed_by"] = "esc"
            last_modal = False
            break
        # Esc 无效：点 ChatGPT 面板右上角关闭钮（估算位置，已加窗口偏移成屏幕坐标）
        cx = int(left + g2.shape[1] * 0.965)
        cy = int(top + g2.shape[0] * 0.055)
        human_click(cx, cy)
        time.sleep(1.6)
        capture(hwnd, TMP)
        g3 = cv2.cvtColor(cv2.imread(TMP), cv2.COLOR_BGR2GRAY)
        _, modal3 = detect_modal(g3)
        out[f"settle_{i}"]["closed_by"] = "esc+x" if not modal3 else "failed"
        last_modal = modal3
    out["modal_closed"] = (last_modal is False)
    out["final_shot"] = TMP
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hwnd", type=int)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--warmup", action="store_true", default=True)
    ap.add_argument("--no-warmup", dest="warmup", action="store_false")
    ap.add_argument("--wait-home", type=float, default=0,
                    help="验证通过后额外等待多少秒再抓最终状态")
    ap.add_argument("--no-settle", dest="settle", action="store_false", default=True,
                    help="跳过收尾（不关升级/营销模态，仅做点击）")
    a = ap.parse_args()

    hwnd, title = pick_window(a.hwnd)
    if not hwnd:
        print(json.dumps({"result": "window_not_found"}, ensure_ascii=True))
        return 1

    out = {"hwnd": hwnd, "title": title}

    # 每次定位前必须保证窗口在最前，否则点击会打到上层窗口
    ok, fg = force_foreground(hwnd)
    out["foreground_ok"] = ok
    if not ok:
        print(json.dumps({**out, "result": "cannot_foreground",
                          "hint": "有窗口强制置顶，先最小化它"}, ensure_ascii=True))
        return 1

    left, top = win32gui.GetWindowRect(hwnd)[:2]

    max_try = 1 if a.dry_run else a.retries
    for attempt in range(max_try):
        capture(hwnd, TMP)
        loc = locate(TMP)
        if loc is None:
            out["result"] = "no_widget"
            out["attempt"] = attempt
            out["note"] = "页面里没有 Turnstile 卡片：可能已验证过、或页面还没渲染出来、或已进入首页"
            print(json.dumps(out, ensure_ascii=True))
            # 没有 widget 本身可能就是已通过
            if attempt == 0:
                out["result"] = "already_clean"
            return 0 if out["result"] == "already_clean" else 2

        if loc["box"] is None:
            out["result"] = "card_found_no_checkbox"
            out["card"] = loc["card"]
            out["attempt"] = attempt
            print(json.dumps(out, ensure_ascii=True))
            return 2

        bx, by, side = loc["box"]
        sx, sy = left + bx, top + by
        out[f"attempt_{attempt}"] = {"img": [round(bx, 1), round(by, 1), round(side, 1)],
                                     "screen": [round(sx, 1), round(sy, 1)]}

        if a.dry_run:
            out["result"] = "dry_run_found"
            print(json.dumps(out, ensure_ascii=True))
            return 0

        owner, otitle = owner_at(sx, sy)
        out[f"attempt_{attempt}"]["owner"] = otitle
        if owner != hwnd:
            out["result"] = "target_covered"
            out["hint"] = f"({sx:.0f},{sy:.0f}) 被 {otitle} 挡住"
            print(json.dumps(out, ensure_ascii=True))
            return 1

        if a.warmup:
            warmup((sx, sy))
        human_click(sx, sy)

        # 观察 12 秒：卡片消失/checkbox 变化都算有反应
        passed = False
        for _ in range(6):
            time.sleep(2.0)
            capture(hwnd, TMP)
            cur = locate(TMP)
            if cur is None:
                passed = True
                break
            if cur["box"] is None:
                passed = True
                break
        if passed:
            out["result"] = "passed"
            out["attempt"] = attempt
            break
    else:
        out["result"] = "failed_all_attempts"

    if out.get("result") == "passed":
        if a.settle:
            out.update(settle(hwnd, left, top, shots=2))
        elif a.wait_home > 0:
            time.sleep(a.wait_home)
            capture(hwnd, TMP)
            out["final_shot"] = TMP
        # 兼容旧 --wait-home：收尾后再多等一会儿并重抓一张终态
        if a.wait_home > 0 and a.settle:
            time.sleep(a.wait_home)
            capture(hwnd, TMP)
            out["final_shot"] = TMP

    print(json.dumps(out, ensure_ascii=True))
    return 0 if out.get("result") == "passed" else 2


if __name__ == "__main__":
    sys.exit(main())
