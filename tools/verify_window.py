# -*- coding: utf-8 -*-
r"""像素小镇 · 真窗口体检

确认 app.py 真的把窗口开出来了，而不是只"没崩"：
找窗口 → 量尺寸 → 截屏 → 检查画面上有没有我们独有的颜色。

截屏里透明区显示的是桌面（不是 #FF00FF），所以判断依据换成：
  * 羊皮纸 UI 色 (244,233,208) 只可能由本程序画出
  * 海水蓝 / 草地绿 必须出现在画面中心
  * 四角必须与中心明显不同（说明抠图生效，角落露出了桌面）

用法： python tools/verify_window.py [--shot out.png]
"""
import ctypes
import json
import os
import sys
import time
from ctypes import wintypes

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from town.palette import PAL                      # noqa: E402
from town.world import PW, PH                     # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
user32 = ctypes.windll.user32

# 体检程序也必须 DPI 感知，否则 GetWindowRect 返回虚拟坐标、
# 而 ImageGrab 抓的是物理像素，两者对不上，截屏会整体偏移。
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)


def find_windows(title_part):
    out = []

    def cb(hwnd, lparam):
        n = user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if title_part in buf.value:
                r = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                out.append({
                    "hwnd": hwnd, "title": buf.value,
                    "visible": bool(user32.IsWindowVisible(hwnd)),
                    "rect": (r.left, r.top, r.right, r.bottom),
                    "size": (r.right - r.left, r.bottom - r.top),
                })
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)
    return out


def near(c, target, tol=6):
    return all(abs(c[i] - target[i]) <= tol for i in range(3))


def main():
    argv = sys.argv[1:]
    shot = None
    if "--shot" in argv:
        i = argv.index("--shot")
        shot = argv[i + 1]

    fails = []
    report = []

    def check(name, cond, detail=""):
        report.append("  [%s] %s %s" % (" OK " if cond else "FAIL", name, detail))
        if not cond:
            fails.append(name)

    wins = find_windows("像素小镇")
    print("=" * 74)
    print("WINDOW SEARCH")
    print("=" * 74)
    for w in wins:
        print("  hwnd=%s title=%r visible=%s size=%s rect=%s" % (
            w["hwnd"], w["title"], w["visible"], w["size"], w["rect"]))
    if not wins:
        print("  没找到窗口。app.py 可能启动失败或已经退出。")
        print("RESULT: FAIL (no window)")
        return 1

    main_win = None
    for w in wins:
        # 主窗是最大的那个；把手窗口只有 34x34
        if main_win is None or (w["size"][0] * w["size"][1]) > (
                main_win["size"][0] * main_win["size"][1]):
            main_win = w
    print("  选中主窗：size=%s rect=%s" % (main_win["size"], main_win["rect"]))

    print("\n" + "=" * 74)
    print("CHECKS")
    print("=" * 74)
    check("main window visible", main_win["visible"])
    ratio_ok = False
    for s in (2, 3, 4):
        if main_win["size"] == (PW * s, PH * s):
            ratio_ok = True
            check("window matches logical %dx%d at scale %d" % (PW, PH, s), True,
                  str(main_win["size"]))
            break
    if not ratio_ok:
        check("window size matches PWxPH * scale", False, str(main_win["size"]))

    if shot:
        try:
            from PIL import ImageGrab
            l, t, r, b = main_win["rect"]
            img = ImageGrab.grab(bbox=(l, t, r, b))
            # 相对路径按项目根解析；父目录不存在就建出来，
            # 否则从工作区外面调用时会撞 FileNotFoundError
            shot_path = shot if os.path.isabs(shot) else os.path.join(ROOT, shot)
            parent = os.path.dirname(shot_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            img.save(shot_path)
            print("  截屏已保存：%s  %s" % (shot_path, img.size))
            check("screenshot size == window size", img.size == main_win["size"],
                  "%s vs %s" % (img.size, main_win["size"]))

            px = img.load()
            w, h = img.size
            # 中心区域取样
            cx0, cy0 = int(w * 0.35), int(h * 0.30)
            cx1, cy1 = int(w * 0.65), int(h * 0.70)
            center = {}
            for y in range(cy0, cy1, 2):
                for x in range(cx0, cx1, 2):
                    c = px[x, y]
                    center[c] = center.get(c, 0) + 1
            top = sorted(center.items(), key=lambda kv: -kv[1])[:12]

            ui_bg = PAL["ui_bg"]
            ui_border = PAL["ui_border"]
            water = PAL["water"]
            found_ui = sum(n for c, n in center.items()
                           if near(c, ui_bg, 10) or near(c, ui_border, 10))
            found_water = sum(n for c, n in center.items() if near(c, water, 26))
            # 草地：各个季节都算（春夏绿、秋黄、冬雪白）
            greens = [(0x8C, 0xC1, 0x52), (0x74, 0xB2, 0x45), (0xAE, 0xAE, 0x5C),
                      (0xDC, 0xE6, 0xEE)]
            found_grass = sum(n for c, n in center.items()
                              if any(near(c, g, 30) for g in greens))

            print("  中心区域最常见颜色：%s" % top[:6])
            print("  命中：羊皮纸=%d  海水=%d  草地=%d" % (
                found_ui, found_water, found_grass))
            # 画面可能是白天/夜晚、晴天/雨天、四季任一，所以不能只看"绿占优"：
            # 秋天的草是黄褐色，雨天还会整体压暗，两者叠加既不绿也不亮。
            # 判据放宽成：色调合理、或命中足够多的地形色、或有 UI 羊皮纸。
            c0 = top[0][0]
            greenish = c0[1] > c0[0] + 12 and c0[1] > c0[2] + 12
            bright = sum(c0) / 3.0 > 148
            earthy = (c0[0] > c0[2] + 8 and c0[1] > c0[2] + 6)   # 偏黄褐/土色
            terrain = (found_water + found_grass) > 200
            check("scene shows terrain", greenish or bright or earthy or terrain,
                  "中心主色=%s 绿=%s 亮=%s 土=%s 地形色命中=%d"
                  % (c0, greenish, bright, earthy, found_water + found_grass))
            check("scene has water or grass", found_water > 0 or found_grass > 0,
                  "水=%d 草=%d" % (found_water, found_grass))

            # UI 在顶部/底部中央，取样那里找羊皮纸色
            ui_hits = 0
            for (yy0, yy1) in ((int(h * 0.02), int(h * 0.10)),
                               (int(h * 0.78), int(h * 0.94))):
                for y in range(yy0, yy1, 2):
                    for x in range(int(w * 0.30), int(w * 0.70), 2):
                        if near(px[x, y], ui_bg, 12):
                            ui_hits += 1
            print("  UI 区域羊皮纸像素 = %d" % ui_hits)
            check("parchment UI is drawn", ui_hits > 20, "n=%d" % ui_hits)

            # 四角应该是桌面（抠图生效），与中心差别必须大
            corner_mean = [0, 0, 0]
            n = 0
            for (x0, y0) in ((0, 0), (w - 30, 0), (0, h - 30), (w - 30, h - 30)):
                for y in range(y0, y0 + 30, 3):
                    for x in range(x0, x0 + 30, 3):
                        c = px[x, y]
                        corner_mean[0] += c[0]
                        corner_mean[1] += c[1]
                        corner_mean[2] += c[2]
                        n += 1
            cm = tuple(v // max(1, n) for v in corner_mean)
            ctr = top[0][0]
            dist = sum(abs(cm[i] - ctr[i]) for i in range(3))
            print("  角落均色=%s  中心主色=%s  差值=%d" % (cm, ctr, dist))
            check("corners are transparent (desktop shows)", dist > 40, "差距=%d" % dist)
        except Exception as exc:
            print("  截屏/分析失败：%r" % (exc,))
            check("screenshot analysis", False, repr(exc))

    print("\n" + "=" * 74)
    for line in report:
        print(line)
    print("RESULT: %d problems" % len(fails))
    if fails:
        print("FAILED: " + ", ".join(fails))
    print("=" * 74)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
