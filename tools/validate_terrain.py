# -*- coding: utf-8 -*-
r"""像素小镇 · 地形配色自检

为什么需要它
------------
地形绘制是一长串 if/elif，谁漏了"先铺底色"这一步，那个格子就会保留
画布初始色 —— 也就是透明键 #FF00FF。它平时是"透明"的，
但昼夜染色会把它推成紫红，于是整片农田看上去是紫的。
这个问题**不会有任何报错**，所有别的自检也照过，只有真看画面才发现。

所以这里做两件事：
  1. 每种地形的主色必须"像它自己"（草是绿的、田是褐的、水是蓝的）
  2. 任何地形都不许出现品红家族的颜色（那就是漏铺底色的症状）

用法： python tools/validate_terrain.py [--out 报告.txt]
"""
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from town.sim import TownSim              # noqa: E402
from town.render import Renderer          # noqa: E402
from town.world import TILE               # noqa: E402

FAILS = []
N = [0]


def check(name, cond, detail=""):
    N[0] += 1
    if cond:
        print("  [ OK ] %s %s" % (name, detail))
    else:
        print("  [FAIL] %s %s" % (name, detail))
        FAILS.append(name)


def is_magenta(c):
    """品红家族：红蓝都高、绿很低，且红蓝接近。

    正常的地形配色（草绿/土褐/水蓝/石灰）没有一个能落进这个条件。
    透明键本身更极端（255,0,255），它只该出现在岛外。
    """
    r, g, b = c
    return g < 90 and r > 150 and b > 150 and abs(r - b) < 70


# 每种地形：期望描述 + 主色判定
EXPECT = [
    ("grass",   "草绿",   lambda r, g, b: g > r and g > b),
    ("grass2",  "草绿",   lambda r, g, b: g > r and g > b),
    ("farm",    "土褐",   lambda r, g, b: r > b and g > b),
    ("path",    "路土",   lambda r, g, b: r > b and g > b),
    ("sand",    "沙黄",   lambda r, g, b: r > b and g >= b),
    ("plaza",   "石灰",   lambda r, g, b: abs(r - g) < 30 and abs(g - b) < 30),
    ("pier",    "木褐",   lambda r, g, b: r > b and r > g),
    ("water",   "水蓝",   lambda r, g, b: b > r and b > g),
    ("shallow", "浅水蓝", lambda r, g, b: b > r and b > g),
    ("deep",    "深水蓝", lambda r, g, b: b > r and b > g),
]


def sample(sim, img, kind):
    """把某种地形所有格子的像素收集起来。"""
    w = sim.world
    px = img.load()
    cnt = Counter()
    tiles = 0
    for y in range(len(w.tiles)):
        for x in range(len(w.tiles[0])):
            if w.tiles[y][x] != kind:
                continue
            tiles += 1
            for dy in range(TILE):
                for dx in range(TILE):
                    cnt[px[x * TILE + dx, y * TILE + dy]] += 1
    return cnt, tiles


def main():
    argv = sys.argv[1:]
    out = None
    if "--out" in argv:
        i = argv.index("--out")
        out = argv[i + 1]
    if out:
        real = sys.stdout
        fh = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), out), "w", encoding="utf-8")

        class Tee(object):
            def write(self, s):
                real.write(s)
                fh.write(s)

            def flush(self):
                real.flush()
                fh.flush()

        sys.stdout = Tee()

    sim = TownSim(seed=20240607)
    sim.advance(24 * 3, detailed=True)
    # 正午：昼夜染色最弱，颜色最能代表调色板本身
    sim.t = int(sim.t // 24) * 24.0 + 12.0
    sim.weather = "clear"
    img = Renderer(sim, scale=1).draw_frame(1 / 30.0, 12.0).convert("RGB")

    print("=" * 74)
    print("地形配色（正午、无天气特效）")
    print("=" * 74)
    print("%-9s %-8s %-22s %8s %8s" % ("地形", "期望", "主色", "占比", "品红"))

    missing = []
    for kind, desc, ok in EXPECT:
        cnt, tiles = sample(sim, img, kind)
        if not cnt:
            missing.append(kind)
            continue
        total = sum(cnt.values())
        main, main_n = cnt.most_common(1)[0]
        magenta = sum(n for c, n in cnt.items() if is_magenta(c))
        print("%-9s %-8s RGB%-18s %7.1f%% %8d"
              % (kind, desc, str(main), 100.0 * main_n / total, magenta))
        check("%s 主色符合" % kind, ok(*main),
              "RGB%s %s" % (main, desc))
        # 品红超过 1% 就说明有格子没铺底色
        check("%s 没有品红泄漏" % kind, magenta <= total * 0.01,
              "%d / %d 像素" % (magenta, total))

    if missing:
        print("\n  （本 seed 没有出现：%s）" % ", ".join(missing))

    # 特别盯一下农田：它是"漏铺底色"最典型的受害者
    cnt, tiles = sample(sim, img, "farm")
    if cnt:
        total = sum(cnt.values())
        earth = sum(n for (r, g, b), n in cnt.items() if r > g and r > b)
        check("农田整体是土色系为主", earth > total * 0.5,
              "%.1f%%" % (100.0 * earth / total))
        check("农田没有残留透明键", not any(
            c == (255, 0, 255) for c in cnt), "精确键色 0 个")

    # 整幅画面：岛内除了岛外透明区，不许有品红
    from town.render import KEY
    px = img.load()
    leaks = 0
    for y in range(0, img.height, 2):
        for x in range(0, img.width, 2):
            c = px[x, y]
            if c == (KEY[0], KEY[1], KEY[2]):
                continue
            if is_magenta(c):
                leaks += 1
    check("全画面没有染色后的透明键", leaks == 0, "%d 处" % leaks)

    print("\n" + "=" * 74)
    print("RESULT: %d checks, %d failed" % (N[0], len(FAILS)))
    if FAILS:
        print("FAILED: " + ", ".join(FAILS))
    print("=" * 74)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
