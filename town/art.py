# -*- coding: utf-8 -*-
"""像素小镇 · 精灵工厂

全部图形都是程序化生成的，不依赖任何外部素材文件。
- 村民：手工雕刻的 ASCII 像素模板，按配色重新烘焙
- 建筑 / 树 / 道具：参数化绘制，再统一自动描边，保证风格一致
"""

import math
import random

from PIL import Image, ImageDraw

from .palette import PAL, shade, mix

OUTLINE = PAL["outline"]


# ==========================================================================
# 通用工具
# ==========================================================================
def add_outline(img, color=OUTLINE):
    """给不透明区域自动包一圈 1px 描边。

    这是让程序化画出来的东西看起来像同一套像素素材的关键。
    """
    w, h = img.size
    px = img.load()
    src = img.copy().load()
    for y in range(h):
        for x in range(w):
            if src[x, y][3] != 0:
                continue
            hit = False
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h and src[nx, ny][3] != 0:
                    hit = True
                    break
            if hit:
                px[x, y] = (color[0], color[1], color[2], 255)
    return img


def _span_profile(h, wmax, jitter=0.0, rng=None):
    """生成一个圆润的冠层轮廓：返回每一行的半径。"""
    out = []
    for i in range(h):
        t = i / max(1, h - 1)
        # 上半部收得快，下半部接近全宽 —— 像树冠/灌木
        base = math.sin(math.pi * (0.10 + 0.90 * t)) ** 0.65
        r = base * wmax / 2.0
        if jitter and rng:
            r += rng.uniform(-jitter, jitter)
        out.append(max(1.0, r))
    return out


# ==========================================================================
# 村民
# ==========================================================================
# 调色板角色：
#   . 透明   O 描边   H 头发  h 头发暗部   S 皮肤  s 皮肤暗部
#   E 眼睛   C 衣服   c 衣服暗部   P 裤子  p 裤子暗部   B 鞋
_TPL = {
    ("down", 0): [
        "..OOOO..",
        ".OHHHHO.",
        ".HHHHHH.",
        ".HSSSSH.",
        ".SEssES.",
        "..SSSS..",
        "..CCCC..",
        ".CCCCCC.",
        ".SCCCCS.",
        "..PPPP..",
        "..PP.PP.",
        "..BB.BB.",
    ],
    ("down", 1): [
        "..OOOO..",
        ".OHHHHO.",
        ".HHHHHH.",
        ".HSSSSH.",
        ".SEssES.",
        "..SSSS..",
        "..CCCC..",
        ".CCCCCC.",
        ".SCCCCS.",
        "..PPPP..",
        "..PPPP..",
        "..BBBB..",
    ],
    ("up", 0): [
        "..OOOO..",
        ".OHHHHO.",
        ".HHHHHH.",
        ".HHHHHH.",
        ".HHHHHH.",
        "..SSSS..",
        "..CCCC..",
        ".CCCCCC.",
        ".SCCCCS.",
        "..PPPP..",
        "..PP.PP.",
        "..BB.BB.",
    ],
    ("up", 1): [
        "..OOOO..",
        ".OHHHHO.",
        ".HHHHHH.",
        ".HHHHHH.",
        ".HHHHHH.",
        "..SSSS..",
        "..CCCC..",
        ".CCCCCC.",
        ".SCCCCS.",
        "..PPPP..",
        "..PPPP..",
        "..BBBB..",
    ],
    ("side", 0): [
        "..OOOO..",
        ".OHHHHO.",
        ".OHHHHH.",
        ".OHSSSS.",
        ".OHSsES.",
        "..OSSS..",
        "..CCCC..",
        "..CCCC..",
        "..CCCS..",
        "..PPPP..",
        "..PPP...",
        "..BBB...",
    ],
    ("side", 1): [
        "..OOOO..",
        ".OHHHHO.",
        ".OHHHHH.",
        ".OHSSSS.",
        ".OHSsES.",
        "..OSSS..",
        "..CCCC..",
        "..CCCC..",
        "..CCCS..",
        "..PPPP..",
        "...PPP..",
        "...BBB..",
    ],
}

VILLAGER_DIRS = ("down", "up", "side")
VILLAGER_FRAMES = (0, 1)
VILLAGER_W = 8
VILLAGER_H = 12


def bake_villager(skin, skin_shadow, hair, cloth, pants=None):
    """按配色烘焙一个村民的 6 张行走帧（正面/背面/侧面 × 2）。

    返回 {(dir, frame): Image}，以及 "side" 的左向由渲染层水平翻转得到。
    """
    if pants is None:
        pants = shade(cloth, 0.55)
    roles = {
        "O": OUTLINE,
        "H": hair,
        "h": shade(hair, 0.78),
        "S": skin,
        "s": skin_shadow,
        "E": OUTLINE,
        "C": cloth,
        "c": shade(cloth, 0.80),
        "P": pants,
        "p": shade(pants, 0.80),
        "B": shade(pants, 0.50),
    }
    out = {}
    for key, rows in _TPL.items():
        img = Image.new("RGBA", (VILLAGER_W, VILLAGER_H), (0, 0, 0, 0))
        px = img.load()
        for y, row in enumerate(rows):
            for x, ch in enumerate(row):
                if ch == ".":
                    continue
                col = roles.get(ch)
                if col is None:
                    continue
                px[x, y] = (col[0], col[1], col[2], 255)
        out[key] = img
    return out


# ==========================================================================
# 植被
# ==========================================================================
_SEASON_LEAF = {
    "spring": PAL["leaf_spring"],
    "summer": PAL["leaf_summer"],
    "autumn": PAL["leaf_autumn"],
    "winter": PAL["leaf_winter"],
}
_SEASON_LEAF2 = {
    "spring": mix(PAL["leaf_spring"], PAL["leaf_summer"], 0.45),
    "summer": PAL["leaf_shadow"],
    "autumn": mix(PAL["leaf_autumn"], PAL["roof_a"], 0.45),
    "winter": PAL["snow_dark"],
}


def make_tree(season="summer", variant=0, seed=0, sway=0):
    """一棵树。sway = -1/0/1，冠层整体左右微移，用来做风吹动画。"""
    rng = random.Random((seed * 7919 + variant * 131 + 17) & 0xFFFFFF)
    leaf = _SEASON_LEAF[season]
    leaf_d = _SEASON_LEAF2[season]
    leaf_l = shade(leaf, 1.16)

    cw = 13 + (variant % 2) * 2
    ch = 9 + (variant % 3)
    trunk_h = 5 + (variant % 2)
    total_w = cw + 2
    total_h = ch + trunk_h + 2

    img = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    cx = total_w // 2
    trunk_top = ch
    # 树干
    tw = 3 if variant % 2 == 0 else 2
    tx = cx - tw // 2
    d.rectangle([tx, trunk_top, tx + tw - 1, total_h - 2], fill=PAL["trunk"])
    d.rectangle([tx + tw - 1, trunk_top, tx + tw - 1, total_h - 2], fill=PAL["trunk_dark"])
    d.rectangle([tx, total_h - 2, tx + tw - 1, total_h - 2], fill=PAL["trunk_dark"])

    # 冬季：只画枝干 + 积雪
    if season == "winter":
        for i in range(3):
            yy = trunk_top - 1 - i * 2
            if yy < 1:
                break
            d.point((tx - 1 - i, yy), fill=PAL["trunk_dark"])
            d.point((tx + tw + i, yy), fill=PAL["trunk_dark"])
        d.rectangle([tx - 1, trunk_top - 2, tx + tw, trunk_top - 1], fill=PAL["snow"])
        d.rectangle([tx - 2, total_h - 3, tx + tw + 1, total_h - 3], fill=PAL["snow"])
        return add_outline(img)

    # 冠层
    radii = _span_profile(ch, cw, jitter=0.6, rng=rng)
    for i in range(ch):
        r = radii[i]
        yy = 1 + i
        xa = int(round(cx - r))
        xb = int(round(cx + r))
        if xb < xa:
            xb = xa
        d.rectangle([xa, yy, xb, yy], fill=leaf)
    # 明暗：左下暗、左上亮，再加点躁点
    for i in range(ch):
        r = radii[i]
        yy = 1 + i
        xa = int(round(cx - r))
        xb = int(round(cx + r))
        if i >= ch - 3:
            d.rectangle([xa, yy, xb, yy], fill=leaf_d)
        if i <= 1:
            d.rectangle([max(xa, cx - 2), yy, min(xb, cx + 2), yy], fill=leaf_l)
        for x in range(xa, xb + 1):
            px = img.getpixel((x, yy))
            if px[3] == 0:
                continue
            roll = rng.random()
            if roll < 0.10:
                img.putpixel((x, yy), (leaf_d[0], leaf_d[1], leaf_d[2], 255))
            elif roll < 0.17:
                img.putpixel((x, yy), (leaf_l[0], leaf_l[1], leaf_l[2], 255))
    # 春天开花
    if season == "spring":
        for _ in range(4 + variant % 3):
            x = rng.randint(max(0, cx - cw // 2), min(total_w - 1, cx + cw // 2))
            y = rng.randint(1, ch)
            if img.getpixel((x, y))[3]:
                img.putpixel((x, y), (*PAL["blossom"], 255))

    if sway:
        img = _shift_canopy(img, ch, sway)
    return add_outline(img)


def _shift_canopy(img, canopy_h, sway):
    """把冠层部分整体平移 1px，制造轻微摇摆。"""
    w, h = img.size
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(img.crop((0, canopy_h, w, h)), (0, canopy_h))
    out.paste(img.crop((0, 0, w, canopy_h)), (sway, 0))
    return out


def make_bush(season="summer", variant=0, seed=0):
    rng = random.Random((seed * 3301 + variant * 97 + 5) & 0xFFFFFF)
    leaf = _SEASON_LEAF[season]
    leaf_d = _SEASON_LEAF2[season]
    w, h = 9, 7
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = w // 2
    radii = _span_profile(h - 1, w - 2, jitter=0.7, rng=rng)
    for i in range(h - 1):
        r = radii[i]
        xa, xb = int(round(cx - r)), int(round(cx + r))
        d.rectangle([xa, 1 + i, xb, 1 + i], fill=leaf)
    d.rectangle([1, h - 2, w - 2, h - 2], fill=leaf_d)
    if season == "autumn":
        for _ in range(3):
            img.putpixel((rng.randint(1, w - 2), rng.randint(1, h - 2)), (*PAL["leaf_autumn"], 255))
    if season == "winter":
        d.rectangle([2, 1, w - 3, 1], fill=PAL["snow"])
    return add_outline(img)


# ==========================================================================
# 小道具
# ==========================================================================
def make_rock(variant=0, seed=0):
    rng = random.Random((seed * 1237 + variant * 31) & 0xFFFFFF)
    w = 7 + variant % 3
    h = 5 + variant % 2
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([1, 2, w - 2, h - 2], fill=PAL["stone"])
    d.rectangle([2, 1, w - 3, h - 2], fill=PAL["stone"])
    d.rectangle([2, 1, w - 3, 1], fill=PAL["stone_light"])
    d.rectangle([1, h - 2, w - 2, h - 2], fill=PAL["stone_dark"])
    for _ in range(2):
        d.point((rng.randint(2, max(2, w - 3)), rng.randint(2, max(2, h - 3))),
                fill=PAL["stone_dark"])
    return add_outline(img)


_FLOWER_COLS = ("flower_a", "flower_b", "flower_c", "flower_d")


def make_flower(variant=0, seed=0):
    col = PAL[_FLOWER_COLS[variant % len(_FLOWER_COLS)]]
    img = Image.new("RGBA", (3, 4), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.point((1, 1), fill=col)
    d.point((0, 1), fill=col)
    d.point((2, 1), fill=col)
    d.point((1, 0), fill=col)
    d.point((1, 2), fill=PAL["grass_deep"])
    d.point((1, 3), fill=PAL["grass_dark"])
    return img


def make_fence(horizontal=True):
    if horizontal:
        img = Image.new("RGBA", (8, 7), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 2, 7, 3], fill=PAL["beam"])
        d.rectangle([1, 0, 2, 6], fill=PAL["trunk"])
        d.rectangle([5, 0, 6, 6], fill=PAL["trunk"])
        d.rectangle([1, 0, 1, 6], fill=PAL["trunk_dark"])
        return add_outline(img)
    img = Image.new("RGBA", (3, 8), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 1, 3], fill=PAL["trunk"])
    d.rectangle([0, 4, 1, 7], fill=PAL["trunk"])
    return add_outline(img)


def make_lamp(level=1):
    """路灯：夜里渲染层会在灯头位置加光晕。"""
    h = 15
    img = Image.new("RGBA", (7, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([3, 5, 3, h - 2], fill=PAL["trunk_dark"])
    d.rectangle([2, h - 2, 4, h - 2], fill=PAL["stone_dark"])
    d.rectangle([1, 2, 5, 5], fill=PAL["stone_dark"])
    d.rectangle([2, 3, 4, 4], fill=PAL["window_lit"])
    d.rectangle([1, 1, 5, 1], fill=PAL["stone"])
    return add_outline(img)


def make_bench():
    img = Image.new("RGBA", (11, 8), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 3, 10, 4], fill=PAL["beam"])
    d.rectangle([0, 5, 10, 5], fill=PAL["trunk_dark"])
    d.rectangle([1, 6, 2, 7], fill=PAL["trunk"])
    d.rectangle([8, 6, 9, 7], fill=PAL["trunk"])
    return add_outline(img)


def make_well():
    img = Image.new("RGBA", (12, 13), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([1, 7, 10, 11], fill=PAL["stone"])
    d.rectangle([1, 7, 10, 7], fill=PAL["stone_light"])
    d.rectangle([1, 11, 10, 11], fill=PAL["stone_dark"])
    d.rectangle([3, 8, 8, 10], fill=PAL["water_deep"])
    d.rectangle([2, 1, 2, 6], fill=PAL["trunk"])
    d.rectangle([9, 1, 9, 6], fill=PAL["trunk"])
    d.rectangle([1, 0, 10, 1], fill=PAL["roof_thatch"])
    d.rectangle([1, 2, 10, 2], fill=PAL["roof_thatch_d"])
    return add_outline(img)


def make_dock(length=4):
    img = Image.new("RGBA", (length * 8, 10), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i in range(length * 2):
        x = i * 4
        col = PAL["beam"] if i % 2 == 0 else PAL["trunk"]
        d.rectangle([x, 3, x + 3, 7], fill=col)
    d.rectangle([0, 3, length * 8 - 1, 3], fill=PAL["roof_thatch_d"])
    d.rectangle([1, 7, 1, 9], fill=PAL["trunk_dark"])
    d.rectangle([length * 8 - 2, 7, length * 8 - 2, 9], fill=PAL["trunk_dark"])
    return add_outline(img)


# ----------------------------------------------------------------------
# 喷泉：小镇广场正中心的主体
#
# 手写 ASCII 而不是用图元拼 —— 上下两层水池的收口、
# 中间柱子的比例，用图元调起来很痛苦，手写一遍就定死了。
# ----------------------------------------------------------------------
_FOUNTAIN = [
    ".........OO.........",
    "........OwwO........",
    ".....OOOOOOOOOO.....",
    "....OSSSSSSSSSSO....",
    "....OSWWWWWWWWSO....",
    "....OSWwwwwwwWSO....",
    "....OSSSSSSSSSSO....",
    ".....OOOOOOOOOO.....",
    ".......OSSSSO.......",
    ".......OSSSSO.......",
    ".......OSSSSO.......",
    "..OOOOOOOOOOOOOOOO..",
    ".OLLLLLLLLLLLLLLLLO.",
    ".OSSSSSSSSSSSSSSSSO.",
    ".OSWWWWWWWWWWWWWWSO.",
    ".OSWww~~ww~~wwwwWSO.",
    ".OSWWWWWWWWWWWWWWSO.",
    ".OOOOOOOOOOOOOOOOOO.",
]

_FOUNTAIN_COLORS = {
    "O": PAL["outline"],
    "S": PAL["stone"],
    "L": PAL["stone_light"],
    "W": PAL["water"],
    "w": PAL["water_light"],
    "~": PAL["water_shine"],
}


def make_fountain():
    """广场中心的喷泉。水柱和涟漪由渲染层动态画，这里只出静态主体。"""
    h = len(_FOUNTAIN)
    w = len(_FOUNTAIN[0])
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = img.load()
    for y, row in enumerate(_FOUNTAIN):
        if len(row) != w:
            raise ValueError("喷泉第 %d 行长度 %d，应为 %d" % (y, len(row), w))
        for x, ch in enumerate(row):
            if ch in _FOUNTAIN_COLORS:
                px[x, y] = _FOUNTAIN_COLORS[ch] + (255,)
    return img


FOUNTAIN_W = 20
FOUNTAIN_H = 18


def make_fish(variant=0):
    """鱼塘里的鱼，6x4，朝右。"""
    cols = [(0xE8, 0x9A, 0x5A), (0x86, 0xC4, 0xDE), (0xD8, 0x8A, 0x9A),
            (0xC8, 0xC0, 0x6A)]
    body = cols[variant % len(cols)]
    dark = PAL["outline"]
    img = Image.new("RGBA", (6, 4), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([1, 1, 4, 2], fill=body)
    d.point((5, 1), fill=body)                  # 吻
    d.point((0, 0), fill=body)                  # 尾
    d.point((0, 3), fill=body)
    d.point((4, 1), fill=dark)                  # 眼
    return img


def make_lily(variant=0):
    """荷叶：贴着水面的小圆片，带一个缺口。"""
    c = (0x5F, 0xA3, 0x3F) if variant % 2 == 0 else (0x74, 0xB8, 0x4C)
    img = Image.new("RGBA", (7, 5), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([0, 1, 6, 4], fill=c)
    d.point((3, 3), fill=PAL["outline"])        # 缺口
    d.point((2, 1), fill=shade(c, 1.22))
    if variant % 3 == 0:                        # 偶尔开一朵
        d.point((3, 0), fill=(0xF0, 0xC8, 0xE0))
    return img


def make_plank():
    """鱼塘栈桥的一块木板，8x8，平铺成一条小路。"""
    img = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 2, 7, 6], fill=PAL["beam"])
    d.line([0, 2, 7, 2], fill=PAL["roof_thatch_d"])
    for x in (2, 5):
        d.line([x, 2, x, 6], fill=PAL["trunk_dark"])
    d.point((1, 4), fill=PAL["trunk"])
    d.point((6, 4), fill=PAL["trunk"])
    return add_outline(img)


def make_reed(variant=0):
    """水边的芦苇。"""
    img = Image.new("RGBA", (7, 14), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    stem = (0x7A, 0xA0, 0x50)
    top = (0xB8, 0x9A, 0x62) if variant % 2 == 0 else (0x9A, 0x86, 0x5C)
    for i, x in enumerate((1, 4, 6)):
        h = 9 + ((variant + i) % 3) * 2
        d.line([x, 13, x, 13 - h], fill=stem)
        d.rectangle([x - 1, 13 - h - 3, x + 1, 13 - h], fill=top)
    return add_outline(img)


def make_crop(stage):
    """农田作物：0 刚播种，4 成熟。"""
    img = Image.new("RGBA", (6, 7), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if stage <= 0:
        d.point((2, 5), fill=PAL["grass_deep"])
        d.point((3, 5), fill=PAL["grass_deep"])
        return img
    hgt = 1 + stage
    col = PAL["grass_dark"] if stage < 4 else PAL["flower_b"]
    for x in (2, 3):
        d.rectangle([x, 6 - hgt, x, 6], fill=col)
    if stage >= 3:
        d.point((1, 5 - hgt), fill=col)
        d.point((4, 5 - hgt), fill=col)
    if stage >= 4:
        d.point((2, 6 - hgt - 1), fill=PAL["flower_a"])
        d.point((3, 6 - hgt - 1), fill=PAL["flower_a"])
    return add_outline(img)


# ==========================================================================
# 建筑
# ==========================================================================
_KIND_SIZES = {
    "house":  [(20, 17), (24, 21), (28, 25)],
    "shop":   [(26, 20), (30, 23), (34, 26)],
    "smithy": [(24, 19), (28, 22), (32, 25)],
    "barn":   [(28, 20), (32, 23), (36, 26)],
    "hall":   [(30, 24), (36, 28), (42, 32)],
}

_ROOF_SETS = {
    "house":  [("thatch", "wall_wood"), ("roof_a", "wall_plaster"), ("roof_b", "wall_stone")],
    "shop":   [("roof_c", "wall_wood"), ("roof_c", "wall_plaster"), ("roof_a", "wall_plaster")],
    "smithy": [("roof_a_dark", "wall_stone"), ("roof_a", "wall_stone"), ("roof_b", "wall_stone")],
    "barn":   [("roof_thatch", "wall_wood"), ("roof_a", "wall_wood"), ("roof_b", "wall_wood")],
    "hall":   [("roof_b", "wall_stone"), ("roof_b", "wall_plaster"), ("roof_a", "wall_plaster")],
}


def make_building(kind="house", level=1, variant=0, seed=0):
    """造一栋房子。返回 dict：image / door / windows / chimney / w / h。

    等级 1→3 会明显变大、换屋顶材料、多开窗，让小镇成长看得见。
    """
    rng = random.Random((seed * 7717 + variant * 313 + level * 29 + len(kind)) & 0xFFFFFF)
    level = max(1, min(3, int(level)))
    w, h = _KIND_SIZES[kind][level - 1]
    roof_key, wall_key = _ROOF_SETS[kind][level - 1]

    if roof_key == "thatch":
        roof, roof_d = PAL["roof_thatch"], PAL["roof_thatch_d"]
    else:
        roof = PAL.get(roof_key, PAL["roof_a"])
        roof_d = PAL.get(roof_key + "_d", shade(roof, 0.78))
    wall = PAL.get(wall_key, PAL["wall_wood"])
    wall_d = PAL.get(wall_key + "_d", shade(wall, 0.8))

    img = Image.new("RGBA", (w + 2, h + 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    ox, oy = 1, 1

    roof_h = max(5, int(h * 0.46))
    wall_top = oy + roof_h
    wall_bot = oy + h - 1
    wx0, wx1 = ox + 2, ox + w - 3

    # ---- 屋顶（人字形） ----
    cx = ox + w // 2
    for i in range(roof_h):
        t = i / max(1, roof_h - 1)
        half = int(round(1.0 + (w / 2.0 - 1.5) * t))
        xa, xb = cx - half, cx + half - 1
        col = roof if (i % 2 == 0) else mix(roof, roof_d, 0.35)
        d.rectangle([xa, oy + i, xb, oy + i], fill=col)
        d.point((xa, oy + i), fill=roof_d)
        d.point((xb, oy + i), fill=roof_d)
    # 屋脊高光
    d.rectangle([cx - 1, oy, cx, oy], fill=shade(roof, 1.25))
    # 屋檐外沿
    d.rectangle([ox, wall_top - 1, ox + w - 1, wall_top - 1], fill=roof_d)
    d.rectangle([ox, wall_top, ox + w - 1, wall_top], fill=shade(roof_d, 0.85))

    # ---- 墙 ----
    d.rectangle([wx0, wall_top + 1, wx1, wall_bot], fill=wall)
    d.rectangle([wx0, wall_bot, wx1, wall_bot], fill=wall_d)
    d.rectangle([wx0, wall_top + 1, wx0, wall_bot], fill=wall_d)
    d.rectangle([wx1, wall_top + 1, wx1, wall_bot], fill=shade(wall_d, 0.9))
    if wall_key == "wall_wood":
        for x in range(wx0 + 3, wx1, 4):
            d.rectangle([x, wall_top + 2, x, wall_bot - 1], fill=wall_d)

    # ---- 门 ----
    door_w = 4 + (1 if level >= 2 else 0)
    door_h = 6 + (1 if level >= 2 else 0)
    door_x = cx - door_w // 2 + (1 if variant % 2 else 0)
    door_y = wall_bot - door_h + 1
    door_x = max(wx0 + 1, min(wx1 - door_w, door_x))
    d.rectangle([door_x - 1, door_y - 1, door_x + door_w, wall_bot], fill=PAL["door_dark"])
    d.rectangle([door_x, door_y, door_x + door_w - 1, wall_bot - 1], fill=PAL["door"])
    d.point((door_x + door_w - 2, door_y + door_h // 2), fill=PAL["flower_b"])

    # ---- 窗 ----
    win_w = 4
    win_h = 4 if level < 3 else 5
    win_y = wall_top + 2 + (1 if level >= 3 else 0)
    windows = []
    slots = []
    if level == 1:
        slots = [wx0 + 2] if variant % 2 == 0 else [wx1 - win_w - 1]
    elif level == 2:
        slots = [wx0 + 2, wx1 - win_w - 2]
    else:
        slots = [wx0 + 1, wx1 - win_w - 1]
        if level >= 3 and w >= 28:
            slots.append(cx - win_w // 2)
    for sx in slots:
        if sx - 1 < door_x - 1 and sx + win_w > door_x - 1:
            continue
        if sx < wx0 or sx + win_w > wx1:
            continue
        d.rectangle([sx - 1, win_y - 1, sx + win_w, win_y + win_h], fill=wall_d)
        d.rectangle([sx, win_y, sx + win_w - 1, win_y + win_h - 1], fill=PAL["window_dark"])
        d.rectangle([sx, win_y, sx + win_w - 1, win_y], fill=shade(PAL["window_dark"], 1.3))
        d.rectangle([sx + win_w // 2, win_y, sx + win_w // 2, win_y + win_h - 1], fill=wall_d)
        windows.append((sx, win_y, win_w, win_h))

    # ---- 烟囱（穿出屋顶斜面） ----
    chimney = None
    if level >= 2:
        chx = cx + w // 4 if variant % 2 == 0 else cx - w // 4 - 2
        chx = max(ox + 2, min(ox + w - 5, chx))
        # 找到该列第一次被屋顶盖住的行，从那一行往上露 4px
        exit_row = oy + 1
        for i in range(roof_h):
            t = i / max(1, roof_h - 1)
            half = int(round(1.0 + (w / 2.0 - 1.5) * t))
            if cx - half <= chx <= cx + half:
                exit_row = oy + i
                break
        cy0 = max(oy + 1, exit_row - 4)
        d.rectangle([chx, cy0, chx + 2, wall_top], fill=PAL["stone"])
        d.rectangle([chx, cy0, chx + 2, cy0], fill=PAL["stone_light"])
        d.rectangle([chx + 2, cy0, chx + 2, wall_top], fill=PAL["stone_dark"])
        d.rectangle([chx - 1, cy0 - 1, chx + 3, cy0 - 1], fill=PAL["stone_dark"])
        chimney = (chx + 1, cy0 - 2)

    # ---- 招牌 / 特征 ----
    sign = None
    if kind == "shop":
        sx = cx - 3
        sy = wall_top - 2
        d.rectangle([sx - 1, sy - 1, sx + 6, sy + 2], fill=PAL["beam"])
        d.rectangle([sx, sy, sx + 5, sy + 1], fill=PAL["ui_bg"])
        d.point((sx + 2, sy), fill=PAL["ui_accent"])
        d.point((sx + 3, sy + 1), fill=PAL["ui_accent"])
        sign = (sx, sy)
    if kind == "smithy":
        # 门口的铁砧
        ax = cx + door_w
        if ax + 3 < wx1:
            d.rectangle([ax, wall_bot - 2, ax + 3, wall_bot - 2], fill=PAL["stone_dark"])
            d.rectangle([ax + 1, wall_bot - 1, ax + 2, wall_bot - 1], fill=PAL["stone_dark"])
    if kind == "hall":
        # 钟
        tx = cx
        d.rectangle([tx - 1, wall_top - 3, tx + 1, wall_top - 2], fill=PAL["flower_b"])
        d.point((tx, wall_top - 1), fill=PAL["flower_b"])

    img = add_outline(img)
    return {
        "image": img,
        "w": img.size[0],
        "h": img.size[1],
        "door": (door_x + door_w // 2, wall_bot + 1),
        "windows": windows,
        "chimney": chimney,
        "sign": sign,
        "kind": kind,
        "level": level,
    }
