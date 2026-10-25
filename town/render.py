# -*- coding: utf-8 -*-
"""像素小镇 · 渲染

管线（顺序很重要）
------------------
1. 逻辑分辨率（352x240）画地面：这是像素画，所有地形都在这一层
2. 按 y 排序贴建筑/树/道具/庄稼/村民（保证前后遮挡正确）
3. 昼夜染色（Image.blend）
4. 天气（雨/雪，逻辑分辨率，保持像素感）
5. 最近邻放大到 scale 倍
6. 在放大后的图上画 UI —— 中文必须在这一层画才清晰
7. 最后统一用岛屿剪影做遮罩裁切

第 7 步是让"悬浮小岛"成立的关键：
UI 条会自然嵌进岛的轮廓里，而不是在透明区域糊出一块矩形。
同时也保证 #FF00FF 透明键在边缘绝对精确 —— 一旦被染色污染，抠图就废了。
"""

import math
import os

from PIL import Image, ImageDraw, ImageFont, ImageOps

from . import art
from .palette import (
    PAL, SKIN_TONES, SKIN_SHADOWS, HAIR_COLORS, CLOTH_COLORS, PANTS_COLORS,
    light_at, is_night, shade, mix,
)
from .world import COLS, ROWS, TILE, PW, PH

KEY = PAL["transparent"]
TILE_PX = TILE

# 每个季节的草地配色：(草、暗草、草叶高光)
SEASON_GRASS = {
    0: ((0x8C, 0xC1, 0x52), (0x74, 0xAB, 0x40), (0xA8, 0xD8, 0x6E)),
    1: ((0x74, 0xB2, 0x45), (0x5E, 0x99, 0x35), (0x92, 0xC9, 0x5C)),
    2: ((0xAE, 0xAE, 0x5C), (0x92, 0x94, 0x4A), (0xC8, 0xC2, 0x6E)),
    3: ((0xDC, 0xE6, 0xEE), (0xC4, 0xD2, 0xE0), (0xF4, 0xF8, 0xFC)),
}

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhl.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\Deng.ttf",
    r"C:\Windows\Fonts\msjh.ttc",
]


def find_font(size):
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _hash01(x, y, salt=0):
    h = (x * 374761393 + y * 668265263 + salt * 2246822519) & 0xFFFFFFFF
    h = (h ^ (h >> 13)) * 1274126177 & 0xFFFFFFFF
    return ((h ^ (h >> 16)) & 0xFFFFFFFF) / 4294967295.0


class Renderer(object):
    def __init__(self, sim, scale=2):
        self.sim = sim
        self.scale = scale
        self._terrain = None
        self._terrain_season = None
        self._mask = None
        self._vsprites = {}
        self._vmirror = {}
        self._trees = {}
        self._props = {}
        self._buildings = {}
        self._crops = {}
        self._plots = {}
        self._fish = {}
        self._jets = None
        self._ui_cache = {}
        self._smoke = []
        self._leaves = []
        self._flies = []
        self._drops = []
        self._font = None
        self._font_size = 0
        self._acc = 0.0

    # ==================================================================
    # 字体
    # ==================================================================
    def font(self, size=None):
        size = size or int(5.6 * self.scale)
        if self._font is None or self._font_size != size:
            self._font = find_font(size)
            self._font_size = size
        return self._font

    # ==================================================================
    # 地形
    # ==================================================================
    def terrain(self):
        season = self.sim.season
        if self._terrain is not None and self._terrain_season == season:
            return self._terrain, self._mask
        w = self.sim.world
        img = Image.new("RGB", (PW, PH), KEY)
        mask = Image.new("L", (PW, PH), 0)
        d = ImageDraw.Draw(img)
        dm = ImageDraw.Draw(mask)
        grass, grass2, blade = SEASON_GRASS[season]

        for ty in range(ROWS):
            for tx in range(COLS):
                kind = w.tiles[ty][tx]
                x0, y0 = tx * TILE, ty * TILE
                if kind == "void":
                    continue
                dm.rectangle([x0, y0, x0 + TILE - 1, y0 + TILE - 1], fill=255)
                r = _hash01(tx, ty, 5)
                if kind in ("grass", "grass2", "grass3"):
                    base = grass if kind == "grass" else grass2
                    d.rectangle([x0, y0, x0 + TILE - 1, y0 + TILE - 1], fill=base)
                    # 草叶：确定性抖动，不能每帧变
                    for k in range(3):
                        if _hash01(tx, ty, 10 + k) > 0.55:
                            px = x0 + int(_hash01(tx, ty, 20 + k) * 6)
                            py = y0 + int(_hash01(tx, ty, 30 + k) * 6)
                            d.point((px, py), fill=blade)
                        if _hash01(tx, ty, 40 + k) > 0.80:
                            px = x0 + int(_hash01(tx, ty, 50 + k) * 6)
                            py = y0 + int(_hash01(tx, ty, 60 + k) * 6)
                            d.point((px, py), fill=shade(base, 0.86))
                elif kind == "sand":
                    d.rectangle([x0, y0, x0 + TILE - 1, y0 + TILE - 1], fill=PAL["sand"])
                    for k in range(3):
                        if _hash01(tx, ty, 70 + k) > 0.62:
                            px = x0 + int(_hash01(tx, ty, 80 + k) * 7)
                            py = y0 + int(_hash01(tx, ty, 90 + k) * 7)
                            d.point((px, py), fill=PAL["sand_dark"])
                elif kind in ("shallow", "water", "deep"):
                    if kind == "shallow":
                        base = PAL["water_light"]
                    elif kind == "water":
                        base = PAL["water"]
                    else:
                        base = PAL["water_deep"]
                    d.rectangle([x0, y0, x0 + TILE - 1, y0 + TILE - 1], fill=base)
                    # 波纹：横向短线，行与行错开
                    if _hash01(tx, ty, 100) > 0.45:
                        ry = y0 + int(_hash01(tx, ty, 101) * 7)
                        d.line([x0 + 1, ry, x0 + 4, ry], fill=shade(base, 1.14))
                    if kind == "deep" and _hash01(tx, ty, 102) > 0.70:
                        ry = y0 + int(_hash01(tx, ty, 103) * 7)
                        d.line([x0 + 3, ry, x0 + 6, ry], fill=shade(base, 1.10))
                elif kind == "path":
                    d.rectangle([x0, y0, x0 + TILE - 1, y0 + TILE - 1], fill=PAL["path"])
                    for k in range(4):
                        if _hash01(tx, ty, 110 + k) > 0.60:
                            px = x0 + int(_hash01(tx, ty, 120 + k) * 7)
                            py = y0 + int(_hash01(tx, ty, 130 + k) * 7)
                            d.point((px, py), fill=PAL["path_dark"])
                    if season == 3 and _hash01(tx, ty, 135) > 0.5:
                        d.point((x0 + 2, y0 + 2), fill=PAL["snow"])
                elif kind == "plaza":
                    d.rectangle([x0, y0, x0 + TILE - 1, y0 + TILE - 1], fill=PAL["stone"])
                    d.line([x0, y0, x0 + TILE - 1, y0], fill=PAL["stone_dark"])
                    d.line([x0, y0, x0, y0 + TILE - 1], fill=PAL["stone_dark"])
                    d.point((x0 + 5, y0 + 5), fill=PAL["stone_light"])
                    if season == 3:
                        d.rectangle([x0 + 3, y0 + 3, x0 + 6, y0 + 6], fill=PAL["snow_dark"])
                elif kind == "farm":
                    # 犁沟
                    for k in range(0, TILE, 3):
                        d.line([x0, y0 + k, x0 + TILE - 1, y0 + k], fill=PAL["farm_wet"])
                    if season == 3:
                        d.rectangle([x0, y0, x0 + TILE - 1, y0 + 1], fill=PAL["snow_dark"])
                else:
                    d.rectangle([x0, y0, x0 + TILE - 1, y0 + TILE - 1], fill=grass)

        # 第二遍：岸线浪花 + 路缘，让地形有层次
        for ty in range(ROWS):
            for tx in range(COLS):
                kind = w.tiles[ty][tx]
                if kind == "void":
                    continue
                x0, y0 = tx * TILE, ty * TILE
                for dx, dy, horiz in ((0, -1, True), (0, 1, True), (-1, 0, False), (1, 0, False)):
                    nx, ny = tx + dx, ty + dy
                    nk = w.tiles[ny][nx] if (0 <= nx < COLS and 0 <= ny < ROWS) else "void"
                    if nk == "void":
                        nk = "deep"
                    # 陆地挨着水 → 浪花
                    if kind in ("grass", "grass2", "grass3", "sand") and nk in (
                            "shallow", "water", "deep"):
                        if horiz:
                            yy = y0 if dy < 0 else y0 + TILE - 1
                            d.line([x0, yy, x0 + TILE - 1, yy],
                                   fill=PAL["foam"] if nk == "shallow" else PAL["sand"])
                        else:
                            xx = x0 if dx < 0 else x0 + TILE - 1
                            d.line([xx, y0, xx, y0 + TILE - 1],
                                   fill=PAL["foam"] if nk == "shallow" else PAL["sand"])
                    # 小路的暗边
                    if kind == "path" and nk in ("grass", "grass2", "grass3", "farm"):
                        if horiz:
                            yy = y0 if dy < 0 else y0 + TILE - 1
                            d.line([x0, yy, x0 + TILE - 1, yy], fill=PAL["path_dark"])
                        else:
                            xx = x0 if dx < 0 else x0 + TILE - 1
                            d.line([xx, y0, xx, y0 + TILE - 1], fill=PAL["path_dark"])

        self._terrain = img
        self._terrain_season = season
        self._mask = mask
        return img, mask

    # ==================================================================
    # 精灵缓存
    # ==================================================================
    def villager_sprite(self, v):
        key = (v.skin, v.hair, v.cloth, v.pants)
        frames = self._vsprites.get(key)
        if frames is None:
            frames = art.bake_villager(
                SKIN_TONES[v.skin % len(SKIN_TONES)],
                SKIN_SHADOWS[v.skin % len(SKIN_SHADOWS)],
                HAIR_COLORS[v.hair % len(HAIR_COLORS)],
                CLOTH_COLORS[v.cloth % len(CLOTH_COLORS)],
                PANTS_COLORS[v.pants % len(PANTS_COLORS)],
            )
            self._vsprites[key] = frames
        if v.dir == "side" and getattr(v, "_flip", False):
            m = self._vmirror.get(key)
            if m is None:
                m = {k: ImageOps.mirror(im) for k, im in frames.items()}
                self._vmirror[key] = m
            return m.get((v.dir, v.frame)) or frames[(v.dir, v.frame)]
        return frames.get((v.dir, v.frame))

    def tree_sprite(self, t, season_name, sway):
        key = (season_name, t["variant"], t["seed"])
        frames = self._trees.get(key)
        if frames is None:
            frames = []
            for sw in (0, 1, 0, -1):
                frames.append(art.make_tree(season=season_name, variant=t["variant"],
                                            seed=t["seed"], sway=sw))
            self._trees[key] = frames
        return frames[sway % len(frames)]

    def prop_sprite(self, p, season_name):
        key = (p["kind"], p["variant"], p["seed"], season_name)
        img = self._props.get(key)
        if img is None:
            k = p["kind"]
            if k == "bush":
                img = art.make_bush(season=season_name, variant=p["variant"], seed=p["seed"])
            elif k == "rock":
                img = art.make_rock(variant=p["variant"], seed=p["seed"])
            elif k == "flower":
                img = art.make_flower(variant=p["variant"], seed=p["seed"])
            elif k == "fence":
                img = art.make_fence(horizontal=True)
            elif k == "lamp":
                img = art.make_lamp()
            elif k == "bench":
                img = art.make_bench()
            elif k == "well":
                img = art.make_well()
            elif k == "fountain":
                img = art.make_fountain()
            elif k == "plank":
                img = art.make_plank()
            elif k == "lily":
                img = art.make_lily(variant=p["variant"])
            elif k == "reed":
                img = art.make_reed(variant=p["variant"])
            elif k == "dock":
                img = art.make_dock(length=max(2, p["variant"]))
            else:
                img = art.make_rock(variant=0, seed=0)
            self._props[key] = img
        return img

    def fish_sprite(self, variant, facing_right=True):
        key = (variant, facing_right)
        img = self._fish.get(key)
        if img is None:
            img = art.make_fish(variant=variant)
            if not facing_right:
                img = ImageOps.mirror(img)
            self._fish[key] = img
        return img

    def fountain_jets(self):
        """喷泉的水柱：预生成 4 帧循环，别每帧现算。"""
        if self._jets is not None:
            return self._jets
        frames = []
        for f in range(4):
            im = Image.new("RGBA", (art.FOUNTAIN_W, 12), (0, 0, 0, 0))
            d = ImageDraw.Draw(im)
            cx = art.FOUNTAIN_W // 2
            # 中间的水柱，高度来回变
            h = 5 + (f % 2) * 2 + (1 if f == 3 else 0)
            for y in range(12 - 1, 12 - 1 - h, -1):
                if y < 0:
                    break
                d.point((cx - 1, y), fill=(0xB8, 0xDC, 0xF0))
                d.point((cx, y), fill=(0xE4, 0xF4, 0xFC))
            # 两侧散开的水珠，逐帧下落
            for i, (dx, ph) in enumerate(((-3, 0), (3, 2), (-5, 1), (5, 3),
                                          (-2, 3), (2, 1))):
                t = (f + ph) % 4
                y = 3 + t * 2
                x = cx + dx
                if 0 <= x < art.FOUNTAIN_W and y < 12:
                    d.point((x, y), fill=(0xA0, 0xCE, 0xE8))
            frames.append(im)
        self._jets = frames
        return frames

    def building_sprite(self, lot):
        key = (lot["kind"], lot["level"], lot["variant"], lot["seed"])
        b = self._buildings.get(key)
        if b is None:
            b = art.make_building(lot["kind"], lot["level"], lot["variant"], lot["seed"])
            self._buildings[key] = b
        return b

    def crop_sprite(self, stage):
        img = self._crops.get(stage)
        if img is None:
            img = art.make_crop(stage)
            self._crops[stage] = img
        return img

    def plot_sprite(self, w, h):
        """一块待建的空地：翻过的土 + 木栅栏 + 木桩。"""
        key = (w, h)
        img = self._plots.get(key)
        if img is None:
            img = Image.new("RGBA", (w + 2, h + 2), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            d.rectangle([1, 1, w, h], fill=PAL["dirt"])
            for yy in range(3, h, 4):
                d.line([2, 1 + yy, w - 1, 1 + yy], fill=PAL["dirt_dark"])
            # 四周围栏（只画上边和两侧，正面留空方便看进去）
            d.line([1, 1, w, 1], fill=PAL["trunk_dark"])
            d.line([1, 2, w, 2], fill=PAL["trunk"])
            for xx in range(1, w, 4):
                d.point((xx, 3), fill=PAL["trunk"])
            d.line([1, 1, 1, h], fill=PAL["trunk_dark"])
            d.line([w, 1, w, h], fill=PAL["trunk_dark"])
            # 中间一根木桩
            cx, cy = w // 2, h // 2
            d.rectangle([cx - 1, cy - 5, cx + 1, cy], fill=PAL["trunk"])
            d.line([cx - 3, cy - 6, cx + 3, cy - 6], fill=PAL["trunk_dark"])
            self._plots[key] = img
        return img

    # ==================================================================
    # 每帧
    # ==================================================================
    def draw_frame(self, dt, anim_t, selected=None, paused=False):
        sim = self.sim
        w = sim.world
        hour = sim.hour
        night = is_night(hour)
        season_name = ("spring", "summer", "autumn", "winter")[sim.season]
        terrain, mask = self.terrain()
        img = terrain.copy()

        # ---- 空地（已解锁但还没盖）----
        for lot in w.lots:
            if lot["unlocked"] and lot["level"] <= 0:
                x0, y0, x1, y1 = lot["rect"]
                sp = self.plot_sprite(x1 - x0, y1 - y0)
                img.paste(sp, (x0 - 1, y1 - sp.height + 1), sp)

        # ---- 收集所有需要 y 排序的东西 ----
        items = []
        for lot in w.lots:
            if lot["level"] > 0:
                b = self.building_sprite(lot)
                x0, y0, x1, y1 = lot["rect"]
                items.append((y1, 0, b["image"], x0 - 1, y1 - b["image"].height + 1, b, lot))
        for t in w.trees:
            sway = int((anim_t * 1.4 + t["phase"]) % 4)
            sp = self.tree_sprite(t, season_name, sway)
            items.append((t["y"], 1, sp, t["x"] - sp.width // 2, t["y"] - sp.height, None, t))
        for p in w.props:
            sp = self.prop_sprite(p, season_name)
            items.append((p["y"], 1, sp, p["x"] - sp.width // 2, p["y"] - sp.height, None, p))
        for f in w.farms:
            for c in f["cells"]:
                sp = self.crop_sprite(c["stage"])
                items.append((c["y"] + 2, 2, sp, c["x"] - sp.width // 2, c["y"] + 2 - sp.height,
                              None, c))
        for v in sim.villagers:
            if v.hidden:
                continue
            sp = self.villager_sprite(v)
            if sp is None:
                continue
            items.append((v.y, 1, sp, int(v.x) - sp.width // 2, int(v.y) - sp.height,
                          None, v))
        # ---- 鱼塘里的鱼：在水面下慢慢来回游 ----
        for f in getattr(w, "fish", []):
            t = anim_t * f["v"] + f["ph"]
            dx = math.sin(t) * 5.0
            dy = math.cos(t * 0.7) * 2.0
            fx, fy = f["x"] + dx, f["y"] + dy
            if (int(fx) // TILE, int(fy) // TILE) not in w.pond:
                continue
            sp = self.fish_sprite(f["variant"], facing_right=(math.cos(t) >= 0))
            items.append((fy, 2, sp, int(fx), int(fy), None, f))
        items.sort(key=lambda it: (it[0], it[1]))
        for _, _, sp, px, py, _extra, obj in items:
            img.paste(sp, (px, py), sp)
            # 喷泉的水柱跟着本体一起贴，这样它和喷泉是同一个 y 排序位置，
            # 站在喷泉前面的人照样会挡住它
            if isinstance(obj, dict) and obj.get("kind") == "fountain":
                jets = self.fountain_jets()
                jet = jets[int(anim_t * 6) % len(jets)]
                img.paste(jet, (px + (sp.width - jet.width) // 2,
                                py - jet.height), jet)
                # 下池的波光：两个点来回晃
                d = ImageDraw.Draw(img)
                for k in range(2):
                    wx = px + 5 + int((math.sin(anim_t * 1.6 + k * 2.1) * 0.5 + 0.5) * 8)
                    wy = py - 4 + k
                    if 0 <= wx < PW and 0 <= wy < PH:
                        d.point((wx, wy), fill=PAL["water_shine"])

        # ---- 天地之间的特效：动物迁徙、彩虹、雾、小动物、集市 ----
        # 放在染色之前，这样夜里的鸟群也会被夜色压暗
        self._draw_world_fx(img, anim_t)

        # ---- 昼夜染色 ----
        tint, strength = light_at(hour)
        if strength > 0.004:
            img = Image.blend(img, Image.new("RGB", img.size, tint), strength)

        # ---- 夜里亮灯（必须在染色之后画，否则会被压暗）----
        if night:
            glow = Image.new("RGB", img.size, (0, 0, 0))
            gd = ImageDraw.Draw(glow)
            gmask = Image.new("L", img.size, 0)
            gm = ImageDraw.Draw(gmask)
            for lot in w.lots:
                if lot["level"] <= 0:
                    continue
                b = self.building_sprite(lot)
                x0, y0, x1, y1 = lot["rect"]
                ox, oy = x0 - 1, y1 - b["image"].height + 1
                for (wx, wy, ww, wh) in b["windows"]:
                    gm.rectangle([ox + wx, oy + wy, ox + wx + ww - 1, oy + wy + wh - 1], fill=255)
            gd.rectangle([0, 0, img.size[0], img.size[1]], fill=PAL["window_lit"])
            img.paste(glow, (0, 0), gmask)

        # ---- 天气的整体色调（下雨压暗、下雪提亮），必须画在雨点之前 ----
        if sim.weather == "rain":
            img = Image.blend(img, Image.new("RGB", img.size, (0x3E, 0x4E, 0x66)), 0.20)
        elif sim.weather == "snow":
            img = Image.blend(img, Image.new("RGB", img.size, (0xCE, 0xDA, 0xEA)), 0.16)

        # ---- 天气 + 粒子（逻辑分辨率）----
        self._update_particles(dt, anim_t, night)
        self._draw_weather(img, dt, anim_t)
        self._draw_hearts(img)
        # ---- 节日灯光：必须画在染色之后，否则灯笼会被夜色压暗 ----
        self._draw_festival(img, anim_t)

        # ---- 放大 ----
        s = self.scale
        out = img.resize((PW * s, PH * s), Image.NEAREST)

        # ---- UI（放大后画，中文才清晰）----
        self._draw_ui(out, selected)

        # ---- 剪影裁切：UI 与天气都被裁进岛的形状里 ----
        if mask is not None:
            big_mask = mask.resize((PW * s, PH * s), Image.NEAREST)
            final = Image.new("RGB", out.size, KEY)
            final.paste(out, (0, 0), big_mask)
            out = final
        return out

    # ==================================================================
    # 粒子
    # ==================================================================
    def _update_particles(self, dt, anim_t, night):
        sim = self.sim
        season = sim.season
        day = not night
        # 炊烟：从有烟囱的房子冒出来
        smoking = [l for l in sim.world.lots
                   if l["level"] >= 2 and l["kind"] in ("house", "shop", "smithy", "hall")]
        self._acc += dt
        if self._acc > 0.45 and smoking:
            self._acc = 0.0
            lot = sim.rng.choice(smoking)
            b = self.building_sprite(lot)
            if b.get("chimney"):
                x0, y0, x1, y1 = lot["rect"]
                ox, oy = x0 - 1, y1 - b["image"].height + 1
                self._smoke.append({
                    "x": ox + b["chimney"][0] + 0.5, "y": oy + b["chimney"][1],
                    "age": 0.0, "life": 2.6,
                    "vx": (sim.rng.random() - 0.5) * 2.2, "vy": -3.4,
                })
        for p in self._smoke:
            p["age"] += dt
            p["x"] += p["vx"] * dt
            p["y"] += p["vy"] * dt
        self._smoke = [p for p in self._smoke if p["age"] < p["life"]][-40:]

        # 落叶 / 萤火虫
        if season == 2 and day:
            while len(self._leaves) < 18:
                self._leaves.append({
                    "x": sim.rng.uniform(0, PW), "y": sim.rng.uniform(-20, 0),
                    "vy": sim.rng.uniform(6, 12), "sw": sim.rng.uniform(0, 6.28),
                })
            for p in self._leaves:
                p["y"] += p["vy"] * dt
                p["x"] += 6.0 * dt * (0.4 + 0.6 * abs(__import__("math").sin(anim_t + p["sw"])))
                if p["y"] > PH:
                    p["y"] = sim.rng.uniform(-20, 0)
                    p["x"] = sim.rng.uniform(0, PW)
        else:
            self._leaves = []

        if night and season in (1, 2):
            while len(self._flies) < 14:
                tx, ty = sim.world.random_walkable(sim.rng)
                self._flies.append({"x": tx * TILE + TILE / 2.0, "y": ty * TILE,
                                    "ph": sim.rng.uniform(0, 6.28)})
            import math
            for p in self._flies:
                p["x"] += math.sin(anim_t * 0.7 + p["ph"]) * 0.30
                p["y"] += math.cos(anim_t * 0.5 + p["ph"] * 1.3) * 0.22
        else:
            self._flies = []

    def _draw_weather(self, img, dt, anim_t):
        d = ImageDraw.Draw(img)
        sim = self.sim
        # 烟
        for p in self._smoke:
            t = p["age"] / p["life"]
            r = 1 + int(t * 2)
            col = mix((0xD8, 0xD8, 0xD8), (0xF0, 0xF0, 0xF0), t)
            a = 1.0 - t
            if a > 0.55:
                d.ellipse([p["x"] - r, p["y"] - r, p["x"] + r, p["y"] + r], fill=col)
            elif a > 0.25:
                d.point((p["x"], p["y"]), fill=col)
        # 落叶
        for p in self._leaves:
            d.point((p["x"], p["y"]), fill=PAL["leaf_autumn"])
        # 萤火虫
        for p in self._flies:
            d.point((p["x"], p["y"]), fill=(0xE8, 0xF5, 0x9A))
        # 雨 / 雪：数量必须够多，90 个雨点在 352x240 上根本看不出在下雨
        if sim.weather in ("rain", "snow"):
            need = 460 if sim.weather == "rain" else 300
            while len(self._drops) < need:
                self._drops.append({
                    "x": sim.rng.uniform(-20, PW + 20), "y": sim.rng.uniform(0, PH),
                    "v": sim.rng.uniform(90, 170) if sim.weather == "rain"
                    else sim.rng.uniform(24, 52),
                })
            for p in self._drops:
                p["y"] += p["v"] * dt
                p["x"] += (p["v"] * (0.34 if sim.weather == "rain" else 0.10)) * dt
                if p["y"] > PH or p["x"] > PW + 20:
                    p["y"] = sim.rng.uniform(-16, -2)
                    p["x"] = sim.rng.uniform(-20, PW + 10)
                if sim.weather == "rain":
                    d.line([p["x"], p["y"], p["x"] - 1.4, p["y"] + 4],
                           fill=(0xAE, 0xCE, 0xE8))
                else:
                    d.point((p["x"], p["y"]), fill=(0xF4, 0xF8, 0xFC))
                    if _hash01(int(p["x"]), int(p["y"]), 3) > 0.45:
                        d.point((p["x"] + 1, p["y"]), fill=(0xE2, 0xEC, 0xF6))
                    if _hash01(int(p["x"]), int(p["y"]), 4) > 0.80:
                        d.point((p["x"], p["y"] + 1), fill=(0xE2, 0xEC, 0xF6))
        elif self._drops:
            self._drops = []

    # ==================================================================
    # 天地特效：动物迁徙 / 彩虹 / 雾 / 小动物 / 集市
    # 都在逻辑分辨率上画，保持像素感；并且画在昼夜染色之前。
    # ==================================================================
    def _prop_tile(self, kind):
        """找某个道具所在的格子（渲染层自己用的，模拟器上也有同名方法）。"""
        w = self.sim.world
        for p in w.props:
            if p["kind"] == kind:
                return (p["x"] // TILE, max(0, p["y"] // TILE - 1))
        return None

    def _draw_world_fx(self, img, anim_t):
        sim = self.sim
        fx = getattr(sim, "fx", None) or {}
        migs = getattr(sim, "migrations", None) or []
        if not fx and not migs:
            return
        d = ImageDraw.Draw(img)

        # ---- 动物迁徙 ----
        for m in migs:
            prog = m["t"] / max(0.1, m["life"])
            bx = -34.0 + prog * (PW + 68)
            kind = m["kind"]
            n = m.get("n", 6)
            if kind == "birds":
                for i in range(n):
                    side = -1 if i % 2 else 1
                    row = (i + 1) // 2
                    x = bx - row * 5
                    y = m["y"] + side * row * 3
                    flap = 1 if int(anim_t * 6 + i) % 2 else 0
                    col = (0x3B, 0x2E, 0x24)
                    d.point((x, y), fill=col)
                    d.point((x - 1, y - flap), fill=col)
                    d.point((x + 1, y - flap), fill=col)
            elif kind == "butterfly":
                for i in range(n * 2):
                    x = bx - i * 3
                    y = m["y"] + math.sin(anim_t * 3.0 + i * 0.9) * 5
                    col = (0xF0, 0xD8, 0x8A) if i % 2 else (0xE8, 0xA8, 0xC8)
                    d.point((x, y), fill=col)
                    d.point((x - 1, y - 1), fill=col)
                    d.point((x + 1, y - 1), fill=col)
            elif kind == "dragonfly":
                for i in range(n):
                    x = bx - i * 7
                    y = m["y"] + math.sin(anim_t * 4.0 + i) * 3
                    col = (0x6A, 0xA8, 0xC8)
                    d.point((x, y), fill=col)
                    d.line([x - 2, y - 1, x + 2, y - 1], fill=(0xC8, 0xE4, 0xF0))
            elif kind == "duck":
                for i in range(n // 2 + 2):
                    x = bx - i * 8
                    y = m["y"] + (1 if int(anim_t * 4 + i) % 2 else 0)
                    body = (0xF0, 0xEC, 0xE0)
                    d.rectangle([x, y - 3, x + 4, y - 1], fill=body)
                    d.point((x - 1, y - 4), fill=body)
                    d.point((x - 2, y - 4), fill=(0xE0, 0xA0, 0x40))
                    d.point((x, y), fill=(0xD0, 0xA0, 0x40))
                    d.point((x + 3, y), fill=(0xD0, 0xA0, 0x40))

        # ---- 彩虹：雨后横跨小镇 ----
        if "rainbow" in fx:
            cols = [(0xE0, 0x7A, 0x7A), (0xE0, 0xB0, 0x70), (0xE0, 0xDC, 0x90),
                    (0x8A, 0xC8, 0x90), (0x80, 0xA8, 0xD8), (0x9A, 0x8A, 0xD0)]
            cx, cy = PW // 2, int(PH * 0.96)
            for bi, col in enumerate(cols):
                r = 96 - bi * 3
                prev = None
                for a in range(14, 167, 3):
                    rad = math.radians(a)
                    x = cx + int(math.cos(rad) * r * 1.22)
                    y = cy - int(math.sin(rad) * r)
                    if 0 <= x < PW and 0 <= y < PH:
                        if prev is not None:
                            d.line([prev[0], prev[1], x, y], fill=col)
                        prev = (x, y)

        # ---- 流星 ----
        if "meteor" in fx:
            for i in range(4):
                t = (anim_t * 0.55 + i * 0.31) % 1.0
                x = int(24 + t * 300)
                y = int(12 + t * 52)
                d.line([x, y, x - 5, y + 3], fill=(0xF0, 0xF0, 0xC8))
                d.point((x, y), fill=(0xFF, 0xFF, 0xF0))

        # ---- 雾：贴一层由淡到浓的白 ----
        # 注意必须写 "mist" in fx。
        # 写成 if "mist": 的话字符串常量恒为真，只要 fx 非空就会
        # 把雾无条件糊满整个画面 —— 而且不报错，只是画面悄悄变亮。
        if "mist" in fx:
            band = Image.new("RGB", img.size, (0xE6, 0xEC, 0xF2))
            mask = Image.new("L", img.size, 0)
            md = ImageDraw.Draw(mask)
            for y in range(PH):
                v = int(max(0.0, 74.0 - abs(y - PH * 0.62) * 0.85))
                if v:
                    md.line([0, y, PW, y], fill=v)
            img.paste(band, (0, 0), mask)

        # ---- 小动物（猫 / 狗）：蹲在井边 ----
        for name, col in (("cat", (0x4A, 0x44, 0x3E)), ("dog", (0xA8, 0x84, 0x5C))):
            if name not in fx:
                continue
            t = self._prop_tile("well") or (COLS // 2, 15)
            x = t[0] * TILE + 7
            y = (t[1] + 1) * TILE
            bob = 1 if int(anim_t * 2) % 2 else 0
            d.rectangle([x, y - 2 - bob, x + 3, y - 1 - bob], fill=col)
            d.point((x - 1, y - 3 - bob), fill=col)
            d.point((x + 4, y - 3 - bob), fill=col)          # 尾巴
            d.point((x, y - bob), fill=col)
            d.point((x + 3, y - bob), fill=col)

        # ---- 集市：广场上摆两个摊 ----
        if "market" in fx:
            px, py = sim.plaza_tile
            for i, dx in enumerate((-4, 4)):
                x = (px + dx) * TILE
                y = (py + 1) * TILE
                d.rectangle([x, y - 4, x + 6, y - 1], fill=(0xB2, 0x8B, 0x62))
                d.line([x, y - 4, x + 6, y - 4], fill=(0xE0, 0x5A, 0x50))
                d.point((x + 1, y - 4), fill=(0xF0, 0xE0, 0xA0))
                d.point((x + 4, y - 4), fill=(0xF0, 0xE0, 0xA0))

        # ---- 鸭子过街：沿着主街摇摇摆摆 ----
        if "ducks" in fx:
            sr = sim.world.street_row
            for i in range(4):
                x = int(20 + ((anim_t * 7.0 + i * 9) % (PW + 24)) - 12)
                y = (sr + 1) * TILE
                bob = 1 if int(anim_t * 5 + i) % 2 else 0
                body = (0xF0, 0xEC, 0xE0)
                d.rectangle([x, y - 3 - bob, x + 4, y - 1 - bob], fill=body)
                d.point((x - 1, y - 4 - bob), fill=body)
                d.point((x - 2, y - 4 - bob), fill=(0xE0, 0xA0, 0x40))

        # ---- 生日 / 庆祝：撒彩屑 ----
        if "party" in fx:
            for i in range(26):
                x = int((i * 37 + anim_t * 12) % PW)
                y = int((i * 53 + anim_t * 22) % PH)
                col = ((0xE8, 0x6A, 0x8A) if i % 3 == 0 else
                       (0xF0, 0xD0, 0x6A) if i % 3 == 1 else (0x8A, 0xC8, 0xE8))
                d.point((x, y), fill=col)

    # ==================================================================
    # 节日：街上的灯笼 + 广场彩旗 + 季节彩屑
    # 画在染色之后，夜里才会亮
    # ==================================================================
    def _draw_festival(self, img, anim_t):
        sim = self.sim
        name = getattr(sim, "festival", None)
        if not name:
            return
        d = ImageDraw.Draw(img)
        w = sim.world
        sr = w.street_row
        night = is_night(sim.hour)
        bulb = (0xFF, 0xC8, 0x6E) if night else (0xE0, 0x5A, 0x50)
        glow = (0xFF, 0xF0, 0xC0)

        # 沿主街挂灯笼，每 3 格一串
        for tx in range(4, COLS - 4, 3):
            if not w._is_land(tx, sr - 1):
                continue
            x = tx * TILE + 4
            y = sr * TILE - 7
            d.line([x, y, x, y + 2], fill=(0x8A, 0x6A, 0x45))
            d.rectangle([x - 2, y + 3, x + 2, y + 7], fill=bulb)
            d.point((x, y + 5), fill=glow)
            if night:
                d.point((x - 1, y + 4), fill=glow)
                d.point((x + 1, y + 4), fill=glow)

        # 广场上拉一条彩旗
        px, py = sim.plaza_tile
        bx0, bx1 = (px - 5) * TILE, (px + 5) * TILE
        by = (py - 2) * TILE
        d.line([bx0, by, bx1, by], fill=(0x8A, 0x6A, 0x45))
        flag_cols = [(0xE0, 0x5A, 0x50), (0xF0, 0xC0, 0x50),
                     (0x6A, 0xA8, 0xD0), (0x7A, 0xC0, 0x70)]
        for i, x in enumerate(range(bx0 + 2, bx1 - 1, 4)):
            col = flag_cols[i % len(flag_cols)]
            sway = 1 if int(anim_t * 2 + i) % 2 else 0
            d.polygon([(x, by), (x + 4, by), (x + 2, by + 4 + sway)], fill=col)

        # 季节彩屑
        conf = {0: (0xF0, 0xB0, 0xD0), 1: (0xE8, 0xE0, 0x90),
                2: (0xE0, 0xA0, 0x60), 3: (0xF0, 0xF4, 0xFA)}
        col = conf.get(sim.season, (0xF0, 0xE0, 0xA0))
        for i in range(30):
            x = int((i * 41 + anim_t * 9) % PW)
            y = int((i * 29 + anim_t * 15) % PH)
            d.point((x, y), fill=col)

    def _draw_hearts(self, img):
        """关系升温 / 点击地面时冒出的像素小爱心。"""
        if not self.sim.highlights:
            return
        d = ImageDraw.Draw(img)
        for h in self.sim.highlights:
            hx, hy, kind, ttl = h[0], h[1], h[2], h[3]
            if kind != "heart":
                continue
            if ttl < 0.55 and int(ttl * 14) % 2 == 0:
                continue                       # 快消失时闪一下
            prog = max(0.0, min(1.0, 1.0 - ttl / 2.4))
            cx = int(hx)
            cy = int(hy - prog * 11)
            col = (0xE8, 0x62, 0x86)
            hi = (0xFF, 0xA8, 0xBC)
            d.point((cx - 2, cy), fill=col)
            d.point((cx + 2, cy), fill=col)
            d.line([cx - 3, cy + 1, cx + 3, cy + 1], fill=col)
            d.point((cx - 2, cy), fill=hi)
            d.line([cx - 2, cy + 2, cx + 2, cy + 2], fill=col)
            d.point((cx - 1, cy + 3), fill=col)
            d.point((cx + 1, cy + 3), fill=col)
            d.point((cx, cy + 4), fill=col)

    # ==================================================================
    # UI
    # ==================================================================
    def _panel(self, size, radius=None):
        key = size + (radius or 0,)
        img = self._ui_cache.get(key)
        if img is None:
            w, h = size
            img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            fill = PAL["ui_bg"]
            border = PAL["ui_border"]
            d.rectangle([0, 0, w - 1, h - 1], fill=fill)
            d.rectangle([0, 0, w - 1, h - 1], outline=border)
            d.rectangle([1, 1, w - 2, h - 2], outline=shade(fill, 1.06))
            self._ui_cache[key] = img
        return img

    def _fit(self, d, text, font, max_w):
        """把文字截到指定宽度内，超了就用省略号。"""
        if d.textlength(text, font=font) <= max_w:
            return text
        while text and d.textlength(text + "…", font=font) > max_w:
            text = text[:-1]
        return text + "…"

    def _draw_ui(self, out, selected):
        sim = self.sim
        s = self.scale
        font = self.font()
        small = self.font(max(9, int(4.6 * s)))
        d = ImageDraw.Draw(out)

        # 岛的剪影在上下两端会收窄，UI 必须落在够宽的那几行里，
        # 否则会被遮罩裁掉半截字。这些宽度是按地形实测取的。
        TOP_W = 210 * s        # 顶部第 2~5 行，岛宽约 212 逻辑像素
        BOT_W = 230 * s        # 底部第 24~26 行，岛宽约 248 逻辑像素

        # ---- 顶部居中：季节 / 日期 / 时刻 / 天气 / 人口 ----
        clock = "%s · 第%d天 %02d:%02d · %s" % (
            sim.season_name, sim.day, int(sim.hour), int((sim.hour % 1) * 60),
            events_name(sim.weather))
        pop = "%d 位居民" % len(sim.villagers)
        ai = sim.ai_status() if hasattr(sim, "ai_status") else None
        if ai is not None:
            ai_word = {"ready": "AI 已连接", "loading": "AI 加载中",
                       "error": "AI 连不上", "off": ""}.get(
                ai.get("state"), "")
            if ai_word:
                pop = "%s · %s" % (pop, ai_word)
        inner = TOP_W - 8 * s
        clock = self._fit(d, clock, small, inner)
        pop = self._fit(d, pop, small, inner)
        bw = int(max(d.textlength(clock, font=small), d.textlength(pop, font=small))
                 + 8 * s)
        bh = int(small.size * 2 + 9 * s)
        bx = (out.width - bw) // 2
        by = 2 * TILE * s
        panel = self._panel((bw, bh))
        out.paste(panel, (bx, by), panel)
        d.text((bx + 4 * s, by + 3 * s), clock, font=small, fill=PAL["ui_text"])
        d.text((bx + 4 * s, by + small.size + 5 * s), pop, font=small,
               fill=shade(PAL["ui_text"], 0.75))

        # ---- 底部居中：最新一条事件 ----
        lines = sim.recent_lines(1)
        if lines:
            text = self._fit(d, lines[-1][2], font, BOT_W - 12 * s)
            pad = 5 * s
            bw = int(min(BOT_W, d.textlength(text, font=font) + pad * 2 + 10 * s))
            bh = int(font.size + 7 * s)
            bx = (out.width - bw) // 2
            by = 24 * TILE * s
            if by + bh > out.height - 2 * s:
                by = out.height - bh - 2 * s
            panel = self._panel((bw, bh))
            out.paste(panel, (bx, by), panel)
            d.text((bx + pad + (bw - pad * 2 - d.textlength(text, font=font)) // 2,
                    by + (bh - font.size) // 2 - 1), text,
                   font=font, fill=PAL["ui_text"])

        # ---- 暂停标记 ----
        if getattr(self, "paused", False):
            t = "已暂停"
            tw = d.textlength(t, font=font)
            panel = self._panel((int(tw + 14 * s), int(font.size + 7 * s)))
            out.paste(panel, (int((out.width - tw) / 2 - 7 * s), int(6 * s)), panel)
            d.text(((out.width - tw) / 2, 9 * s), t, font=font, fill=PAL["ui_text"])

        # ---- 对话气泡 ----
        for v in sim.villagers:
            if v.hidden or not v.say:
                continue
            say = self._fit(d, v.say, small, 96 * s)
            tw = d.textlength(say, font=small)
            bw = int(tw + 8 * s)
            bh = int(small.size + 6 * s)
            bx = int(v.x * s) - bw // 2
            by = int((v.y - 15) * s) - bh
            bx = max(2, min(out.width - bw - 2, bx))
            by = max(2, by)
            d.rectangle([bx, by, bx + bw, by + bh], fill=(255, 252, 242),
                        outline=PAL["ui_border"])
            d.polygon([(bx + bw // 2 - 3 * s, by + bh),
                       (bx + bw // 2 + 3 * s, by + bh),
                       (bx + bw // 2, by + bh + 3 * s)], fill=(255, 252, 242))
            d.text((bx + 4 * s, by + 3 * s), say, font=small,
                   fill=PAL["ui_text"])

        # ---- 选中的村民卡片 ----
        if selected is not None:
            self._draw_card(out, selected)

    def _draw_card(self, out, v):
        sim = self.sim
        s = self.scale
        font = self.font()
        small = self.font(max(9, int(4.4 * s)))
        d = ImageDraw.Draw(out)
        pad = 6 * s
        by_id = {x.vid: x for x in sim.villagers}
        lines = [
            (v.name, font, PAL["ui_text"]),
            ("%s · %s" % (v.role, "大人" if v.adult else "孩子"), small,
             shade(PAL["ui_text"], 0.75)),
            ("心情 %s" % v.mood_word, small, PAL["ui_text"]),
        ]
        rel = sorted(v.rel.items(), key=lambda kv: -kv[1])[:3]
        for oid, val in rel:
            o = by_id.get(oid)
            if o:
                lines.append(("%s · %s %d" % (o.name, v.rel_level(o), int(val)),
                              small, shade(PAL["ui_text"], 0.8)))
        need_w = int(max(d.textlength(t, font=f) for t, f, _ in lines) + pad * 2 + 20 * s)
        cw = min(need_w, int(150 * s))
        ch = int(sum(f.size + 4 * s for _, f, _ in lines) + pad * 2
                 + 3 * (max(3, int(3.2 * s)) + 3 * s) + 6 * s)
        # 夹在岛内部的安全区里，避免卡片被剪影切掉
        cx = int(v.x * s) - cw // 2
        cy = int((v.y - 13) * s) - ch
        cx = max(4 * TILE * s, min(out.width - 4 * TILE * s - cw, cx))
        cy = max(2 * TILE * s, min(out.height - 5 * TILE * s - ch, cy))
        panel = self._panel((cw, ch))
        out.paste(panel, (cx, cy), panel)
        y = cy + pad
        for text, f, col in lines:
            d.text((cx + pad, y), self._fit(d, text, f, cw - pad * 2), font=f, fill=col)
            y += f.size + 4 * s
        # 三条需求条
        bx = cx + pad
        bw = cw - pad * 2
        y += 2 * s
        for key, label, col in (("mood", "心情", (0xD9, 0x9A, 0x4A)),
                                ("energy", "精力", (0x6A, 0x9C, 0x59)),
                                ("hunger", "饱腹", (0xC0, 0x50, 0x4D))):
            val = v.needs.get(key, 50.0) / 100.0
            d.text((bx, y), label, font=small, fill=shade(PAL["ui_text"], 0.7))
            tx = bx + int(small.size * 2.6)
            bh = max(3, int(3.2 * s))
            d.rectangle([tx, y + 1, bx + bw, y + bh], fill=shade(PAL["ui_bg"], 0.86))
            d.rectangle([tx, y + 1, tx + int((bx + bw - tx) * val), y + bh], fill=col)
            y += bh + 3 * s


def events_name(w):
    from .events import WEATHER_NAMES
    return WEATHER_NAMES.get(w, "晴")
