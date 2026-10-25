# -*- coding: utf-8 -*-
r"""像素小镇 · 美术自检

因为要保证"看不见图也能验收"，这里做两件事：
1) 统计断言：尺寸、非空、描边、轮廓单调性、季节差异
2) ASCII 转印：把精灵按颜色映射成字符，直接在终端里看轮廓

用法： python tools/validate_art.py
"""
import os
import sys
import warnings
from collections import Counter

# Pillow 12 起 getdata 被标记弃用，但这里只是自检工具，噪音盖住了有用输出
warnings.filterwarnings("ignore", message=".*getdata is deprecated.*")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image  # noqa: E402

from town import art  # noqa: E402
from town.palette import PAL, SKIN_TONES, SKIN_SHADOWS, HAIR_COLORS, CLOTH_COLORS, PANTS_COLORS  # noqa: E402

FAILS = []
CHECKS = [0]


def check(name, cond, detail=""):
    CHECKS[0] += 1
    if cond:
        print("  [ OK ] %s %s" % (name, detail))
    else:
        print("  [FAIL] %s %s" % (name, detail))
        FAILS.append(name)


def opaque_count(img):
    return sum(1 for p in img.getdata() if p[3] != 0)


def colors_of(img):
    return Counter(p[:3] for p in img.getdata() if p[3] != 0)


def ascii_dump(img, title, chars=" .:-=+*#%@", max_w=78):
    """把图缩到可读宽度，按亮度映射成 ASCII，用来在终端里看轮廓。"""
    print("\n--- %s  (%dx%d) ---" % (title, img.size[0], img.size[1]))
    w, h = img.size
    if w > max_w:
        ratio = max_w / w
        img = img.resize((max_w, max(1, int(h * ratio))), Image.NEAREST)
        w, h = img.size
    px = img.load()
    for y in range(h):
        line = []
        for x in range(w):
            r, g, b, a = px[x, y]
            if a == 0:
                line.append(" ")
                continue
            lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
            line.append(chars[min(len(chars) - 1, int(lum * len(chars)))])
        print("|" + "".join(line) + "|")


def color_map_dump(img, title, limit=12):
    """按主导色映射成字母，能看出结构分区。"""
    print("\n--- %s  (%dx%d) ---" % (title, img.size[0], img.size[1]))
    cnt = colors_of(img)
    top = [c for c, _ in cnt.most_common(limit)]
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    legend = []
    for i, col in enumerate(top):
        legend.append("%s=%02X%02X%02X" % (letters[i], col[0], col[1], col[2]))
    for chunk in range(0, len(legend), 6):
        print("   " + "  ".join(legend[chunk:chunk + 6]))
    px = img.load()
    for y in range(img.size[1]):
        line = []
        for x in range(img.size[0]):
            r, g, b, a = px[x, y]
            if a == 0:
                line.append(".")
                continue
            idx = top.index((r, g, b)) if (r, g, b) in top else -1
            line.append(letters[idx] if 0 <= idx < len(letters) else "?")
        print("|" + "".join(line) + "|")


def main():
    print("=" * 70)
    print("1. VILLAGERS")
    print("=" * 70)
    frames = art.bake_villager(SKIN_TONES[0], SKIN_SHADOWS[0], HAIR_COLORS[1],
                              CLOTH_COLORS[0], PANTS_COLORS[0])
    check("frame count == 6", len(frames) == 6, "got %d" % len(frames))
    for key, im in frames.items():
        check("size %s" % (key,), im.size == (art.VILLAGER_W, art.VILLAGER_H), str(im.size))
        n = opaque_count(im)
        check("opaque %s" % (key,), 40 <= n <= 96, "n=%d" % n)
        check("has outline %s" % (key,), (PAL["outline"] + (255,)) in set(im.getdata()))
    # 不同配色必须真的产出不同结果
    f2 = art.bake_villager(SKIN_TONES[3], SKIN_SHADOWS[3], HAIR_COLORS[0],
                           CLOTH_COLORS[3], PANTS_COLORS[2])
    check("recolor differs", list(frames[("down", 0)].getdata()) != list(f2[("down", 0)].getdata()))
    # 两帧必须真的不一样（有走路动画）
    check("walk frame differs", list(frames[("down", 0)].getdata()) != list(frames[("down", 1)].getdata()))
    check("up has no eyes", list(frames[("up", 0)].getdata()) != list(frames[("down", 0)].getdata()))
    ascii_dump(frames[("down", 0)], "villager down f0")
    ascii_dump(frames[("up", 0)], "villager up f0")
    ascii_dump(frames[("side", 0)], "villager side f0")

    print("\n" + "=" * 70)
    print("2. TREES")
    print("=" * 70)
    trees = {s: art.make_tree(s, 0, seed=1) for s in ("spring", "summer", "autumn", "winter")}
    for s, im in trees.items():
        check("tree %s non-empty" % s, opaque_count(im) > 30, "n=%d" % opaque_count(im))
    check("winter sparser than summer",
          opaque_count(trees["winter"]) < opaque_count(trees["summer"]),
          "win=%d sum=%d" % (opaque_count(trees["winter"]), opaque_count(trees["summer"])))
    check("season palettes differ",
          len({colors_of(trees[s]).most_common(1)[0][0] for s in trees}) >= 3,
          str([colors_of(trees[s]).most_common(1)[0][0] for s in trees]))
    # 冠层宽度应当是"中间宽、两头窄"
    row_w = []
    for y in range(trees["summer"].size[1]):
        xs = [x for x in range(trees["summer"].size[0]) if trees["summer"].getpixel((x, y))[3]]
        row_w.append(len(xs))
    canopy = row_w[:max(1, len(row_w) - 6)]
    check("canopy widest in middle", canopy.index(max(canopy)) > 0
          and canopy.index(max(canopy)) < len(canopy) - 1, str(canopy))
    ascii_dump(trees["summer"], "tree summer")
    ascii_dump(trees["winter"], "tree winter")
    # 摇摆帧必须不同
    check("sway changes pixels",
          list(art.make_tree("summer", 0, seed=1, sway=1).getdata())
          != list(art.make_tree("summer", 0, seed=1, sway=0).getdata()))

    print("\n" + "=" * 70)
    print("3. BUILDINGS")
    print("=" * 70)
    for kind in ("house", "shop", "smithy", "barn", "hall"):
        prev_area = -1
        for lvl in (1, 2, 3):
            b = art.make_building(kind, lvl, variant=lvl, seed=lvl)
            im = b["image"]
            area = im.size[0] * im.size[1]
            check("%s L%d non-empty" % (kind, lvl), opaque_count(im) > area * 0.35,
                  "n=%d/%d" % (opaque_count(im), area))
            check("%s L%d grows" % (kind, lvl), area > prev_area, "%d > %d" % (area, prev_area))
            prev_area = area
            dx, dy = b["door"]
            check("%s L%d door inside" % (kind, lvl), 0 < dx < im.size[0] and 0 < dy <= im.size[1],
                  "door=%s size=%s" % ((dx, dy), im.size))
            for (wx, wy, ww, wh) in b["windows"]:
                check("%s L%d win inside" % (kind, lvl),
                      0 <= wx and wx + ww <= im.size[0] and 0 <= wy and wy + wh <= im.size[1],
                      "win=%s" % ((wx, wy, ww, wh),))
            if lvl >= 2:
                check("%s L%d has chimney" % (kind, lvl), b["chimney"] is not None)
            else:
                check("%s L%d no chimney" % (kind, lvl), b["chimney"] is None)
    ascii_dump(art.make_building("house", 1, 0, 1)["image"], "house L1")
    ascii_dump(art.make_building("house", 3, 0, 3)["image"], "house L3")
    color_map_dump(art.make_building("shop", 2, 1, 2)["image"], "shop L2 color map")

    print("\n" + "=" * 70)
    print("4. PROPS / CROPS")
    print("=" * 70)
    areas = [opaque_count(art.make_crop(s)) for s in range(5)]
    check("crop grows monotonically", all(areas[i] <= areas[i + 1] for i in range(4)), str(areas))
    check("crop stage0 minimal", areas[0] <= 2, str(areas))
    for nm, im in (("rock", art.make_rock(1, 1)), ("flower", art.make_flower(0)),
                   ("fence_h", art.make_fence(True)), ("lamp", art.make_lamp()),
                   ("bench", art.make_bench()), ("well", art.make_well()),
                   ("dock", art.make_dock(3)), ("bush", art.make_bush("summer", 0, 1))):
        check("%s non-empty" % nm, opaque_count(im) > 3, "n=%d" % opaque_count(im))

    # 喷泉是广场正中心的地标，挨个部位都要在：
    # 两层水池（水色）、中间柱子（石色）、整圈描边。
    fount = art.make_fountain()
    check("fountain size", fount.size == (art.FOUNTAIN_W, art.FOUNTAIN_H), str(fount.size))
    n_f = opaque_count(fount)
    check("fountain non-empty", n_f > 200, "n=%d" % n_f)
    fcols = colors_of(fount)
    check("fountain has water", PAL["water"] in fcols and PAL["water_light"] in fcols,
          str([("%02X%02X%02X" % c) for c in list(fcols)[:6]]))
    check("fountain has stone", PAL["stone"] in fcols and PAL["stone_light"] in fcols)
    check("fountain has outline", (PAL["outline"] + (255,)) in set(fount.getdata()))
    # 水必须在下面那一层：水色像素的重心要明显低于整幅重心
    ys_all, ys_water = [], []
    for y in range(fount.size[1]):
        for x in range(fount.size[0]):
            r, g, b, a = fount.getpixel((x, y))
            if a == 0:
                continue
            ys_all.append(y)
            if (r, g, b) in (PAL["water"], PAL["water_light"], PAL["water_shine"]):
                ys_water.append(y)
    check("fountain water sits low", ys_water and (sum(ys_water) / len(ys_water)
                                                   > sum(ys_all) / len(ys_all)),
          "water=%.1f all=%.1f" % (sum(ys_water) / max(1, len(ys_water)),
                                   sum(ys_all) / max(1, len(ys_all))))
    # 左右必须基本对称（手写 ASCII 最容易写歪）
    skew = []
    w_f = fount.size[0]
    for y in range(fount.size[1]):
        xs = [x for x in range(w_f) if fount.getpixel((x, y))[3]]
        if xs and min(xs) != (w_f - 1 - max(xs)):
            skew.append((y, min(xs), w_f - 1 - max(xs)))
    check("fountain roughly symmetric", not skew, str(skew[:4]))
    ascii_dump(fount, "fountain")

    print("\n" + "=" * 70)
    print("5. TRANSPARENT KEY SAFETY")
    print("=" * 70)
    key = PAL["transparent"]
    bad = []
    for name, im in list(trees.items()) + [("house", art.make_building("house", 3, 0, 3)["image"]),
                                           ("villager", frames[("down", 0)]),
                                           ("fountain", fount)]:
        if any(p[:3] == key and p[3] != 0 for p in im.getdata()):
            bad.append(name)
    check("no sprite uses the transparent key", not bad, str(bad))

    print("\n" + "=" * 70)
    print("RESULT: %d checks, %d failed" % (CHECKS[0], len(FAILS)))
    if FAILS:
        print("FAILED: " + ", ".join(FAILS))
    print("=" * 70)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
