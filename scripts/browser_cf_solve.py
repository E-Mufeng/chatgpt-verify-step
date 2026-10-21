#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""browser_cf_solve.py — 战场 A：独立 / 普通浏览器的人机验证通过（patchright）。

技术路线（与战场 B 完全不同）：
    战场 B（客户端内置 WebView）无法注入 CDP / 指纹，只能从外部用「像素 + 真实鼠标」
    对抗行为评分（必须点框）。
    战场 A（你能用 Playwright 启动的独立浏览器）则用 patchright（Playwright stealth fork,
    Apache-2.0）**伪装指纹**，使 Cloudflare Turnstile 降低挑战或直接放行——是从
    「指纹层」化解，而不是去点验证框。因此战场 A 通常不交互，只等待验证自然消失。

用法:
    python browser_cf_solve.py --url "https://chatgpt.com" --wait 60
    python browser_cf_solve.py --url "https://example.com" --headless

依赖: patchright（由 bootstrap_venv.py 安装；首次还会下载其 Chromium 构建）。
跨平台: Windows / macOS / Linux。
"""
import argparse
import json
import sys
import time

try:
    from patchright.sync_api import sync_playwright
except ImportError:
    print(json.dumps({"result": "launch_failed",
                      "hint": "run bootstrap_venv.py to install patchright"},
                     ensure_ascii=True))
    sys.exit(2)


def _has_challenge(page):
    """检测页面当前是否仍处于 Cloudflare 挑战态。"""
    try:
        el = page.query_selector(
            "iframe[src*='challenges.cloudflare.com'], "
            "#cf-challenge-running, .cf-spinner, #challenge-form, "
            "iframe[title*='challenge'], div[class*='cf-challenge']"
        )
        return el is not None
    except Exception:
        return None  # 查询失败（页面正在跳转）视为"不确定"，继续等


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="目标 URL")
    ap.add_argument("--wait", type=float, default=60,
                    help="最多等待验证消失多少秒")
    ap.add_argument("--headless", action="store_true", default=False)
    a = ap.parse_args()

    out = {"url": a.url, "arena": "A-external-browser"}

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=a.headless)
        except Exception as e:
            print(json.dumps({"result": "launch_failed", "error": str(e),
                              "hint": "run bootstrap_venv.py (它会 patchright install chromium)"},
                             ensure_ascii=True))
            return 2

        try:
            page = browser.new_page()
            page.goto(a.url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            out["result"] = "nav_failed"
            out["error"] = str(e)
            browser.close()
            print(json.dumps(out, ensure_ascii=True))
            return 2

        passed = False
        deadline = time.time() + a.wait
        while time.time() < deadline:
            time.sleep(2.0)
            if not _has_challenge(page):
                passed = True
                break
        out["result"] = "passed" if passed else "timeout"
        try:
            out["final_url"] = page.url
            out["title"] = page.title()
        except Exception:
            pass
        browser.close()

    print(json.dumps(out, ensure_ascii=True))
    return 0 if out["result"] == "passed" else 2


if __name__ == "__main__":
    sys.exit(main())
