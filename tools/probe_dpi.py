# -*- coding: utf-8 -*-
r"""DPI 探针：搞清楚这台机器上窗口坐标到底是怎么回事。

像素画最怕被系统按非整数比例缩放糊掉，
所以必须确认：进程的 DPI 感知级别、真实 DPI、以及窗口实际拿到多少物理像素。
"""
import ctypes
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

user32 = ctypes.windll.user32
try:
    shcore = ctypes.windll.shcore
except Exception:
    shcore = None

AWARENESS = {0: "UNAWARE（会被系统缩放，像素画会糊）",
             1: "SYSTEM_AWARE",
             2: "PER_MONITOR_AWARE"}

print("=" * 70)
print("PROCESS DPI STATE（声明之前）")
print("=" * 70)
aw = ctypes.c_int(0)
try:
    shcore.GetProcessDpiAwareness(None, ctypes.byref(aw))
    print("  GetProcessDpiAwareness = %d  %s" % (aw.value, AWARENESS.get(aw.value, "?")))
except Exception as e:
    print("  GetProcessDpiAwareness 失败: %r" % (e,))

try:
    print("  GetDpiForSystem        = %d" % user32.GetDpiForSystem())
except Exception as e:
    print("  GetDpiForSystem 失败: %r" % (e,))
print("  SM_CXSCREEN x SM_CYSCREEN = %d x %d" % (
    user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)))
try:
    print("  SM_CXSCREEN(physical, 0x00) = %d" % user32.GetSystemMetrics(0))
except Exception:
    pass

print("\n试着声明 PER_MONITOR_AWARE ...")
rc = None
if shcore is not None:
    rc = shcore.SetProcessDpiAwareness(2)
    print("  SetProcessDpiAwareness(2) 返回 HRESULT = %s" % (rc,))
    if rc not in (0, None):
        print("  -> 失败（0x%08X）。若已是 0x80070005 表示早已被设定过。" % (rc & 0xFFFFFFFF))
else:
    print("  shcore 不可用")

try:
    aw2 = ctypes.c_int(0)
    shcore.GetProcessDpiAwareness(None, ctypes.byref(aw2))
    print("  声明后 GetProcessDpiAwareness = %d  %s" % (
        aw2.value, AWARENESS.get(aw2.value, "?")))
except Exception as e:
    print("  查询失败: %r" % (e,))

print("\n" + "=" * 70)
print("TK 实际拿到什么（建一个 352x240 的窗口量一量）")
print("=" * 70)
import tkinter as tk
r = tk.Tk()
r.overrideredirect(True)
r.geometry("352x240+50+50")
r.update_idletasks()
r.update()
print("  tk scaling           = %s" % r.tk.call("tk", "scaling"))
print("  winfo_screenwidth/height = %d x %d" % (
    r.winfo_screenwidth(), r.winfo_screenheight()))
print("  winfo_reqwidth/height    = %d x %d" % (r.winfo_reqwidth(), r.winfo_reqheight()))
print("  winfo_width/height       = %d x %d" % (r.winfo_width(), r.winfo_height()))
hwnd = r.winfo_id()
parent = user32.GetParent(hwnd)
print("  winfo_id=%s  GetParent=%s" % (hwnd, parent))

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

for label, h in (("child(winfo_id)", hwnd), ("parent", parent)):
    if not h:
        continue
    rc2 = RECT()
    user32.GetWindowRect(h, ctypes.byref(rc2))
    print("  %-16s rect=(%d,%d)-(%d,%d) size=%dx%d" % (
        label, rc2.left, rc2.top, rc2.right, rc2.bottom,
        rc2.right - rc2.left, rc2.bottom - rc2.top))
    try:
        print("      GetDpiForWindow = %d" % user32.GetDpiForWindow(h))
    except Exception:
        pass

r.destroy()
print("\n如果 size 不是 352x240，说明进程不是 DPI 感知的，像素画会被系统缩放。")
