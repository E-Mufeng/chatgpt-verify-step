#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""find_target_window.py — 稳健定位 ChatGPT / Codex 客户端主窗口。

为什么单独成一个脚本（而不是让 Agent 自己写枚举代码）：
    弱模型自己写 win32 窗口枚举极易出错；且「按标题 + 面积最大」是脆弱启发式
    （多开一个窗口就抓错，标题也会随会话名变化）。本脚本把「发现窗口」这件事
    做成确定性工具，Agent 只需读取它的 JSON 输出，不需要任何推理或自写代码。

定位策略（稳健，按优先级）：
    1. 进程优先：找客户端进程（默认 codex.exe）。这是最可靠的锚点，不依赖标题。
    2. 标题兜底：进程没命中时，按标题含 "ChatGPT" / "Codex" 匹配。
    3. 只枚举【顶层窗口】：内置浏览器是 Electron 进程内的子 WebView，不是顶层窗口，
       永远不会被本脚本选中——所以 Agent 拿到的就是「客户端主窗口」，
       不要用浏览器标签页去单独定位（那会锁错对象）。

输出 JSON（Agent 直接 parse）：
    {"status":"FOUND","hwnd":...,"title":...,"pid":...,"proc":...,"rect":[l,t,r,b],"note":...}
    {"status":"AMBIGUOUS","recommended":<hwnd>,"candidates":[...],"note":...}
    {"status":"NOT_RUNNING","candidates":[],"note":"未找到客户端窗口，客户端可能未启动"}
"""
import sys
import os
import json

# 让脚本无论从哪个 cwd 被调用，都能 import 兄弟模块、都能自解析所在目录
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import win32gui
import win32process
import win32con
import win32api

PROC_HINTS = ("codex.exe", "chatgpt.exe")  # 客户端进程名（大小写不敏感）
TITLE_HINTS = ("ChatGPT", "Codex")    # 标题兜底关键词


def _enum_top_windows():
    wins = []

    def cb(hwnd, _):
        # 只收顶层、可见、尺寸有效的窗口
        if not win32gui.IsWindowVisible(hwnd):
            return True
        r = win32gui.GetWindowRect(hwnd)
        if r[2] - r[0] <= 0 or r[3] - r[1] <= 0:
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        proc = ""
        try:
            h = win32api.OpenProcess(
                win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
                False, pid)
            proc = os.path.basename(win32process.GetModuleFileNameEx(h, 0))
            win32api.CloseHandle(h)
        except Exception:
            proc = ""
        title = win32gui.GetWindowText(hwnd)
        wins.append({
            "hwnd": hwnd,
            "title": title,
            "pid": pid,
            "proc": proc,
            "rect": list(r),
        })
        return True

    win32gui.EnumWindows(cb, 0)
    return wins


def find():
    wins = _enum_top_windows()

    # 1) 进程优先
    cands = [w for w in wins if w["proc"].lower() in PROC_HINTS]
    # 2) 标题兜底
    if not cands:
        cands = [w for w in wins
                 if any(h.lower() in w["title"].lower() for h in TITLE_HINTS)]

    if not cands:
        return {
            "status": "NOT_RUNNING",
            "candidates": [],
            "note": "未找到 ChatGPT/Codex 客户端窗口，客户端可能未启动或已退出",
        }

    # 面积最大者排第一（顶层窗口里通常是客户端主窗口）
    cands.sort(key=lambda w: -((w["rect"][2] - w["rect"][0]) *
                               (w["rect"][3] - w["rect"][1])))

    if len(cands) == 1:
        w = cands[0]
        return {
            "status": "FOUND",
            "hwnd": w["hwnd"],
            "title": w["title"],
            "pid": w["pid"],
            "proc": w["proc"],
            "rect": w["rect"],
            "note": "客户端主窗口（顶层窗口）。锁定此窗口即可；"
                    "内置浏览器是子 WebView，不要单独去定位标签页",
        }

    # 多个候选：默认推荐面积最大的，但把列表全给 Agent 自己再判
    return {
        "status": "AMBIGUOUS",
        "recommended": cands[0]["hwnd"],
        "candidates": cands,
        "note": "发现多个候选，默认推荐面积最大的顶层窗口（recommended）。"
                "若它不对，从 candidates 中选 proc=codex.exe、"
                "且 rect 覆盖屏幕中心的那个作为 hwnd",
    }


def get_client_window():
    """返回 (hwnd, title)；找不到返回 (None, None)。供 codex_cf_solve 调用。"""
    data = find()
    if data.get("status") in ("FOUND", "AMBIGUOUS"):
        return data["hwnd"], data.get("title", "")
    return None, None


def main():
    data = find()
    print(json.dumps(data, ensure_ascii=True))
    return 0 if data["status"] in ("FOUND", "AMBIGUOUS") else 1


if __name__ == "__main__":
    sys.exit(main())
