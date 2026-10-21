#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bootstrap_venv.py — 一键把本技能依赖装进技能目录内的隔离 venv。

为什么需要它：技能脚本依赖 pywin32 / opencv-python / numpy / pillow（战场 B）
以及 patchright（战场 A）。直接 pip install 到系统会污染环境；这里在技能目录建
独立 venv/，调用方改用 venv 里的 python 即可，零污染、可随技能一起携带。

用法:
    python bootstrap_venv.py                # 战场 B 依赖（Windows 必装）
    python bootstrap_venv.py --browser      # 再加战场 A 依赖（patchright + 其 Chromium）
    python bootstrap_venv.py --offline      # 使用 _wheels/ 离线包（需先 pip download 好）

离线打包（在能联网的机器上先执行）:
    pip download -r requirements.txt -d _wheels
    pip download -r requirements-browser.txt -d _wheels
"""
import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VENV = os.path.join(ROOT, "venv")


def venv_python():
    if os.name == "nt":
        return os.path.join(VENV, "Scripts", "python.exe")
    return os.path.join(VENV, "bin", "python")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--browser", action="store_true",
                    help="同时安装战场 A 依赖（patchright）")
    ap.add_argument("--offline", action="store_true",
                    help="使用 _wheels/ 离线包安装")
    a = ap.parse_args()

    out = {}

    if not os.path.isdir(VENV):
        subprocess.run([sys.executable, "-m", "venv", VENV], check=True)
        out["venv_created"] = True

    py = venv_python()
    wheels = os.path.join(ROOT, "_wheels")
    offline = a.offline and os.path.isdir(wheels)
    pip_base = [py, "-m", "pip", "install", "--upgrade", "pip"]
    if offline:
        pip_base += ["--no-index", "--find-links", wheels]
    subprocess.run(pip_base, check=True)

    reqs = [os.path.join(ROOT, "requirements.txt")]
    if a.browser:
        reqs.append(os.path.join(ROOT, "requirements-browser.txt"))
    for r in reqs:
        cmd = [py, "-m", "pip", "install", "-r", r]
        if offline:
            cmd += ["--no-index", "--find-links", wheels]
        subprocess.run(cmd, check=True)

    out["venv_python"] = py
    out["ok"] = True

    if a.browser:
        try:
            subprocess.run([py, "-m", "patchright", "install", "chromium"], check=True)
            out["patchright_browser"] = "installed"
        except Exception as e:
            out["patchright_browser"] = "warn:%s" % e

    print(json.dumps(out, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
