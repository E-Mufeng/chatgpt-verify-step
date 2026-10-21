"""可靠地把窗口提到前台并拿到焦点。

Windows 有 foreground lock：非前台进程直接 SetForegroundWindow 会被静默忽略。
标准破解法是把本线程输入队列 attach 到当前前台窗口的线程上，
共享输入状态后再 SetForegroundWindow 就生效了。
"""
import ctypes
import sys
import time

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    ctypes.windll.user32.SetProcessDPIAware()

import win32api
import win32con
import win32gui
import win32process

user32 = ctypes.WinDLL("user32", use_last_error=True)


def force_foreground(hwnd, settle=0.8):
    """把 hwnd 强行提到前台，返回 (ok, 前台hwnd)。"""
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.25)

    fg = win32gui.GetForegroundWindow()
    if fg == hwnd:
        win32gui.BringWindowToTop(hwnd)
        return True, fg

    # 1) 经典 attach 技巧
    try:
        fg_thread, _ = win32process.GetWindowThreadProcessId(fg) if fg else (0, 0)
    except Exception:
        fg_thread = 0
    my_thread = win32api.GetCurrentThreadId()

    attached = False
    if fg_thread and fg_thread != my_thread:
        attached = bool(user32.AttachThreadInput(my_thread, fg_thread, True))
    try:
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
        # 2) 补一发 ALT 键解除前台锁后重试
        if win32gui.GetForegroundWindow() != hwnd:
            user32.keybd_event(win32con.VK_MENU, 0, 0, 0)
            user32.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
            win32gui.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(my_thread, fg_thread, False)

    if win32gui.GetForegroundWindow() != hwnd:
        # 3) TOPMOST 抖动兜底
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                              win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW)
        win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                              win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW)
        win32gui.SetForegroundWindow(hwnd)

    time.sleep(settle)
    return win32gui.GetForegroundWindow() == hwnd, win32gui.GetForegroundWindow()


def owner_at(x, y):
    h = win32gui.WindowFromPoint((int(x), int(y)))
    root = win32gui.GetAncestor(h, win32con.GA_ROOT)
    return root, win32gui.GetWindowText(root)


def main():
    if len(sys.argv) < 2:
        print("usage: foreground.py <hwnd> | --probe x,y")
        return 2
    if sys.argv[1] == "--probe":
        x, y = [float(v) for v in sys.argv[2].split(",")]
        root, title = owner_at(x, y)
        print({"owner_hwnd": root, "title": title,
               "foreground": win32gui.GetForegroundWindow()})
        return 0
    hwnd = int(sys.argv[1])
    ok, fg = force_foreground(hwnd)
    print({"target": hwnd, "ok": ok, "foreground": fg,
           "title": win32gui.GetWindowText(fg)})
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
