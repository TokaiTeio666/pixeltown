# -*- coding: utf-8 -*-
"""像素小镇 · 世界生成

设计要点
--------
* 地形 = 一座浮在桌面上的小岛：陆地 → 沙滩 → 浅水 → 深水 → 完全透明。
  小岛用「超椭圆」而不是圆，这样能填满画布，只在四角留透明缺口，
  桌面上的观感才像一块悬浮的立体模型，而不是一张方图。
* 先开街道和广场，再沿街两侧放宅基地（前排 + 后排两排），
  合法性检查保证房屋全在陆地上、互不重叠、不压街道。
* 每块宅基地在生成时就分配好，但初始等级是 0（空地）；
  之后随着小镇成长一栋栋盖起来、再一级级翻新 —— 这是"养成"的可见回报。
* 入户小路用 BFS 真铺，而不是画死线，所以任何布局下门口都能通到街上。
"""

import math
from collections import deque

from . import art

TILE = 8
COLS = 44
ROWS = 30
PW = COLS * TILE          # 352
PH = ROWS * TILE          # 240
SUPER_P = 3.1             # 超椭圆指数：越大越接近圆角矩形，越小越接近椭圆

WATER = {"shallow", "water", "deep", "void"}
LAND = {"grass", "grass2", "sand", "path", "dirt", "farm", "plaza", "plot",
        "plaza2", "grass3", "snow", "grass_dry", "pier"}

# 宅基地规格：(类型, 优先级, 偏好侧, 是否民居)
LOT_SPECS = [
    ("hall",   0, "north", False),
    ("shop",   1, "north", False),
    ("smithy", 2, "south", False),
    ("barn",   3, "south", False),
] + [("house", 10 + i, "north" if i % 2 == 0 else "south", True) for i in range(9)]

ROW_STEP = 6              # 前排与后排之间的行距

# 开局就已经盖好的一级建筑；其余宅基地先是野草地，随着小镇成长才一栋栋出现
INITIAL_BUILT = {"hall", "shop", "barn", "smithy"}
# 开局就住人的民居数。这个数直接决定人口容量（4 个公共岗位 + 每栋民居 1 个位子），
# 只盖 1 栋的话容量是 5 < 初始 6 人，开局就超员，第一个人会被"挤走"。
INITIAL_HOUSES = 2


def _srgb_hash(a, b, salt=0):
    """稳定的整数哈希，用来做确定性随机（存档读档后布局必须一致）。"""
    h = (a * 374761393 + b * 668265263 + salt * 2246822519) & 0xFFFFFFFF
    h = (h ^ (h >> 13)) * 1274126177 & 0xFFFFFFFF
    return (h ^ (h >> 16)) & 0xFFFFFFFF


def _rand01(a, b, salt=0):
    return _srgb_hash(a, b, salt) / 4294967295.0


_SIZE_CACHE = {}


def _max_size(kind):
    """建筑按最高等级预留占地，缓存起来避免反复渲染。"""
    if kind not in _SIZE_CACHE:
        b = art.make_building(kind, 3, variant=0, seed=0)
        _SIZE_CACHE[kind] = (b["w"], b["h"])
    return _SIZE_CACHE[kind]


class World(object):
    def __init__(self, seed=12345):
        self.seed = int(seed)
        self.tiles = []
        self.walk = []
        self.lots = []
        self.trees = []
        self.props = []
        self.farms = []
        self.pond = set()
        self._lotset = set()
        self.pond_center = None
        self.pond_pier = []
        self.lilies = []
        self.reeds = []
        self.fish = []
        self.fountain_px = None
        self.dock = None
        self.street_row = 15
        self._noise_phases = None
        self.generate()

    # ------------------------------------------------------------------
    # 地形
    # ------------------------------------------------------------------
    def _init_noise(self):
        r = _rand01
        s = self.seed
        self._noise_phases = [
            (3.0, 0.030, r(s, 1) * 6.283),
            (5.0, 0.018, r(s, 2) * 6.283),
            (8.0, 0.010, r(s, 3) * 6.283),
            (13.0, 0.006, r(s, 4) * 6.283),
        ]

    def _field(self, tx, ty):
        """返回 r - edge：<0 在陆地内部，越大越朝外（沙→浅水→深水→透明）。"""
        cx, cy = (COLS - 1) / 2.0, (ROWS - 1) / 2.0
        ax, ay = COLS / 2.0 - 1.0, ROWS / 2.0 - 1.0
        nx = (tx - cx) / ax
        ny = (ty - cy) / ay
        ang = math.atan2(ny, nx)
        r = (abs(nx) ** SUPER_P + abs(ny) ** SUPER_P) ** (1.0 / SUPER_P)
        edge = 0.870
        for f, amp, ph in self._noise_phases:
            edge += amp * math.sin(ang * f + ph)
        return r - edge

    def _terrain_at(self, tx, ty):
        f = self._field(tx, ty)
        if f < -0.012:
            return "grass" if _rand01(tx, ty, 11) > 0.30 else "grass2"
        if f < 0.004:
            # 草地→沙地的过渡，用抖动避免出现生硬的环线
            d = _rand01(tx, ty, 12)
            if d < 0.45:
                return "sand"
            return "grass2" if d < 0.75 else "grass"
        if f < 0.024:
            return "sand"
        if f < 0.052:
            return "shallow"
        if f < 0.078:
            return "water"
        if f < 0.100:
            return "deep"
        return "void"

    def _is_land(self, tx, ty):
        return 0 <= tx < COLS and 0 <= ty < ROWS and self.tiles[ty][tx] in LAND

    # ------------------------------------------------------------------
    # 生成主流程
    # ------------------------------------------------------------------
    def generate(self):
        self._init_noise()
        self.tiles = [[self._terrain_at(x, y) for x in range(COLS)] for y in range(ROWS)]
        self.walk = [[False] * COLS for _ in range(ROWS)]

        self._choose_street_row()
        self._carve_street()          # 先开街 + 广场
        # 鱼塘必须在宅基地之前挖。
        # 小镇密起来之后，一整块 9x7 的纯草地就再也找不到了 ——
        # 实测放在后面时一个鱼塘都挖不出来。先挖塘，房子和小路会绕开它。
        self._place_pond()
        self._place_lots()            # 房屋再避开街道落位（顺序很关键）
        self._carve_connectors()      # 入户小路：BFS 真铺，且绕开宅基地
        self._place_dock()
        self._place_farms()
        self._scatter_decor()
        self._rebuild_walk()

    # --- 街道行：选一条陆地最宽、且靠近竖向中心的横带 ---
    def _choose_street_row(self):
        best, best_score = ROWS // 2, -1e9
        for y in range(5, ROWS - 6):
            width = sum(1 for x in range(COLS) if self._is_land(x, y))
            score = width * 2.0 - abs(y - (ROWS - 1) / 2.0) * 1.2
            if score > best_score:
                best_score, best = score, y
        self.street_row = best

    # --- 街道 + 中心广场 ---
    def _carve_street(self):
        lo, hi = self._land_span()
        sr = self.street_row
        for tx in range(lo, hi + 1):
            for ty in (sr, sr + 1):
                if self._is_land(tx, ty):
                    self.tiles[ty][tx] = "path"
        mid = (lo + hi) // 2
        for tx in range(mid - 2, mid + 3):
            for ty in range(sr - 1, sr + 3):
                if self._is_land(tx, ty):
                    self.tiles[ty][tx] = "plaza"

    def _land_span(self, row=None):
        row = self.street_row if row is None else row
        xs = [x for x in range(COLS) if self._is_land(x, row)]
        return (min(xs), max(xs)) if xs else (4, COLS - 5)

    # ------------------------------------------------------------------
    # 宅基地
    # ------------------------------------------------------------------
    def _footprint(self, kind, ty, side, tx):
        """算出建筑占用的大地格矩形（按最高等级预留，避免升级后穿模）。"""
        w, h = _max_size(kind)
        center_px = tx * TILE + TILE // 2
        if side == "north":
            bottom_px = ty * TILE - 1
            top_px = bottom_px - h
        else:
            top_px = ty * TILE
            bottom_px = top_px + h
        x0 = center_px - w // 2
        return (x0, top_px, x0 + w, bottom_px)

    def _ref_row(self, side, depth):
        sr = self.street_row
        return sr - 1 - ROW_STEP * depth if side == "north" else sr + 2 + ROW_STEP * depth

    def _rect_ok(self, rect, placed, gap=1):
        x0, y0, x1, y1 = rect
        fx0, fy0 = x0 // TILE, y0 // TILE
        fx1, fy1 = (x1 - 1) // TILE, (y1 - 1) // TILE
        if fx0 < 1 or fy0 < 1 or fx1 > COLS - 2 or fy1 > ROWS - 2:
            return False
        # 建筑本体必须全是陆地
        for ty in range(fy0, fy1 + 1):
            for tx in range(fx0, fx1 + 1):
                if not self._is_land(tx, ty):
                    return False
                if self.tiles[ty][tx] in ("path", "plaza"):
                    return False
        # 与已有建筑之间留出空隙
        for (ax0, ay0, ax1, ay1) in placed:
            if not (fx1 + gap < ax0 // TILE or fx0 > (ax1 - 1) // TILE + gap or
                    fy1 + gap < ay0 // TILE or fy0 > (ay1 - 1) // TILE + gap):
                return False
        return True

    def _place_lots(self):
        lo, hi = self._land_span()
        center = (lo + hi) / 2.0
        placed = []
        self.lots = []

        for idx, (kind, prio, pref_side, is_house) in enumerate(LOT_SPECS):
            best, best_score = None, None
            for depth in (0, 1):
                for side in ("north", "south"):
                    ty = self._ref_row(side, depth)
                    if ty < 2 or ty > ROWS - 3:
                        continue
                    for tx in range(lo + 1, hi):
                        rect = self._footprint(kind, ty, side, tx)
                        if not self._rect_ok(rect, placed):
                            continue
                        if is_house and placed:
                            # 民居喜欢和已有邻居保持一个舒服的距离
                            d = min(abs(tx - (r[0] + r[2]) / 2.0 / TILE) for r in placed)
                            s = -abs(d - 5.0) * 3.0
                        else:
                            # 公共建筑聚在中心
                            s = -abs(tx - center) * 3.0
                        if side != pref_side:
                            s -= 6.0
                        s -= depth * 7.0            # 优先靠近街道
                        s += _rand01(tx + depth * 100, idx, 77) * 2.2
                        if best_score is None or s > best_score:
                            best_score, best = s, (side, ty, rect, depth)
            if best is None:
                continue
            side, ty, rect, depth = best
            placed.append(rect)
            # 开局就住人的民居多盖几栋：民宅数直接决定人口容量。
            # 只盖一栋的话（容量 5 < 初始 6 人）开局就超员，
            # 第一个人会被"挤走"，读起来莫名其妙。
            placed_houses = sum(1 for l in self.lots if l["is_house"])
            built = (kind in INITIAL_BUILT) or (
                is_house and placed_houses < INITIAL_HOUSES)
            self.lots.append({
                "id": "%s_%d" % (kind, idx),
                "kind": kind,
                "side": side,
                "depth": depth,
                "tx": (rect[0] + rect[2]) // 2 // TILE,
                "ty": ty,
                "rect": rect,
                "level": 1 if built else 0,
                "unlocked": built,
                "is_house": is_house,
                "variant": _srgb_hash(idx, self.seed, 5) % 2,
                "seed": _srgb_hash(idx, self.seed, 9) % 100000,
                "order": prio,
            })
        # 宅基地不改写地形：开局是一片自然草地，
        # 空地/地基由渲染层按 unlocked / level 决定要不要画。
        self._lotset = self._lot_tile_set()

    def _lot_tile_set(self):
        s = set()
        for lot in self.lots:
            x0, y0, x1, y1 = lot["rect"]
            for ty in range(max(0, y0 // TILE), min(ROWS, (y1 - 1) // TILE + 1)):
                for tx in range(max(0, x0 // TILE), min(COLS, (x1 - 1) // TILE + 1)):
                    s.add((tx, ty))
        return s

    # ------------------------------------------------------------------
    # 入户小路：BFS 铺路
    # ------------------------------------------------------------------
    @staticmethod
    def _bfs_route(start, goals, passable):
        if not passable(*start):
            for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                if passable(start[0] + dx, start[1] + dy):
                    start = (start[0] + dx, start[1] + dy)
                    break
            else:
                return None
        if start in goals:
            return [start]
        prev = {start: None}
        q = deque([start])
        while q:
            cur = q.popleft()
            if cur in goals:
                path = []
                node = cur
                while node is not None:
                    path.append(node)
                    node = prev[node]
                return path
            cx, cy = cur
            for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                nxt = (cx + dx, cy + dy)
                if nxt in prev or not passable(*nxt):
                    continue
                prev[nxt] = cur
                q.append(nxt)
        return None

    def _carve_connectors(self):
        sr = self.street_row
        street = set()
        for x in range(COLS):
            for y in (sr - 1, sr, sr + 1, sr + 2):
                if 0 <= y < ROWS and self.tiles[y][x] in ("path", "plaza"):
                    street.add((x, y))
        lotset = self._lot_tile_set()

        def passable(tx, ty):
            if not (0 <= tx < COLS and 0 <= ty < ROWS):
                return False
            if (tx, ty) in lotset:
                return False
            return self._is_land(tx, ty)

        # 前排先铺，后排就能借用已铺好的小路
        kept = []
        for lot in sorted(self.lots, key=lambda L: L.get("depth", 0)):
            x0, y0, x1, y1 = lot["rect"]
            # 门口一律取建筑底边（门画在墙的下半部）；
            # 南侧房子门朝背街，BFS 自然会绕过房子接到街上。
            start = (lot["tx"], (y1 - 1) // TILE + 1)
            route = self._bfs_route(start, street, passable)
            if route is None:
                continue
            for (tx, ty) in route:
                if self.tiles[ty][tx] in ("grass", "grass2", "grass3", "sand", "plot"):
                    self.tiles[ty][tx] = "path"
            kept.append(lot)

        # 铺不到路的地块直接放弃，并把空地还原成草地
        for lot in self.lots:
            if lot in kept:
                continue
            x0, y0, x1, y1 = lot["rect"]
            for ty in range(max(0, y0 // TILE), min(ROWS, (y1 - 1) // TILE + 1)):
                for tx in range(max(0, x0 // TILE), min(COLS, (x1 - 1) // TILE + 1)):
                    if self.tiles[ty][tx] == "plot":
                        self.tiles[ty][tx] = "grass"
        self.lots = kept

    # ------------------------------------------------------------------
    # 内陆小池塘
    # ------------------------------------------------------------------
    def _place_pond(self):
        """挖一个鱼塘。

        比原来的小水坑大一圈，而且带木栈桥、荷叶和芦苇 ——
        它要能当景点看，也要能当渔夫的第二个作业点。

        搜索必须是**全岛扫描**：原来只在街道下方一条固定的横带里找，
        而那条带子正好压在成排的南侧民居上，结果一个鱼塘都挖不出来。
        """
        sr = self.street_row
        HW, HH = 4, 3              # 半宽/半高（格）；实际占 9x7
        PAD = 1                    # 外圈留一格，别贴着路或房子
        mid = (COLS - 1) / 2.0

        def area_ok(cx, cy):
            for dy in range(-HH - PAD, HH + PAD + 1):
                for dx in range(-HW - PAD, HW + PAD + 1):
                    tx, ty = cx + dx, cy + dy
                    if not (0 <= tx < COLS and 0 <= ty < ROWS):
                        return False
                    if (tx, ty) in self._lotset:
                        return False
                    if self.tiles[ty][tx] not in ("grass", "grass2", "grass3"):
                        return False
            return True

        cands = []
        for cy in range(HH + PAD + 1, ROWS - HH - PAD - 1):
            for cx in range(HW + PAD + 1, COLS - HW - PAD - 1):
                if not area_ok(cx, cy):
                    continue
                # 打分：离街道近一点方便看，但别压着广场；也别太靠海
                score = -abs(cx - mid) * 1.0 - abs(cy - (sr + 4)) * 1.6
                score += _rand01(cx, cy, 21) * 3.0
                cands.append((score, cx, cy))
        if not cands:
            return
        cands.sort(key=lambda t: -t[0])
        _, tx, ty = cands[0]

        RX, RY = 3.7, 2.8          # 水面半径（格）
        for dy in range(-HH, HH + 1):
            for dx in range(-HW, HW + 1):
                d = math.hypot(dx / RX, dy / RY)
                if d < 0.62:
                    self.tiles[ty + dy][tx + dx] = "water"
                    self.pond.add((tx + dx, ty + dy))
                elif d < 0.96:
                    self.tiles[ty + dy][tx + dx] = "shallow"
                    self.pond.add((tx + dx, ty + dy))
        self.pond_center = (tx, ty)
        # 木栈桥：从南岸往北伸进水里。
        # 地形直接改成 pier —— 它属于 LAND，所以是可走的，
        # 渔夫才能真的站到栈桥上去钓鱼。
        self.pond_pier = [(tx, ty + HH - k) for k in range(3)]
        for (ptx, pty) in self.pond_pier:
            if 0 <= ptx < COLS and 0 <= pty < ROWS:
                self.tiles[pty][ptx] = "pier"
        # 荷叶
        self.lilies = []
        for i in range(6):
            lx = tx - 3 + int(_rand01(self.seed, i, 23) * 7)
            ly = ty - 2 + int(_rand01(self.seed, i, 24) * 5)
            if (lx, ly) in self.pond and ly < ty + HH - 1:
                self.lilies.append({"tx": lx, "ty": ly, "variant": i % 3})
        # 芦苇：沿岸立几丛
        self.reeds = []
        for i in range(7):
            ang = 6.283 * i / 7.0
            rx = tx + int(round(math.cos(ang) * (RX + 0.6)))
            ry = ty + int(round(math.sin(ang) * (RY + 0.6)))
            if (rx, ry) not in self.pond and self._is_land(rx, ry) \
                    and self.tiles[ry][rx] in ("grass", "grass2", "grass3"):
                self.reeds.append({"x": rx * TILE + 3, "y": (ry + 1) * TILE,
                                   "variant": i % 3})
        # 鱼
        self.fish = []
        for i in range(8):
            fx = tx - 3 + _rand01(self.seed, i, 26) * 7
            fy = ty - 2 + _rand01(self.seed, i, 27) * 4
            self.fish.append({"x": fx * TILE, "y": fy * TILE + 4,
                              "v": 0.7 + _rand01(self.seed, i, 28) * 0.9,
                              "ph": _rand01(self.seed, i, 29) * 6.283,
                              "variant": i % 4})

    # ------------------------------------------------------------------
    # 海边码头
    # ------------------------------------------------------------------
    def _place_dock(self):
        sr = self.street_row
        best = None
        for ty in range(4, ROWS - 4):
            for tx in range(COLS - 3, 2, -1):
                if self.tiles[ty][tx] == "sand":
                    run = 0
                    for k in range(1, 8):
                        nx = tx + k
                        if nx < COLS and self.tiles[ty][nx] in ("shallow", "water", "deep"):
                            run += 1
                        else:
                            break
                    if run >= 2:
                        score = run * 2 - abs(ty - (sr + 4)) * 0.5
                        if best is None or score > best[0]:
                            best = (score, tx, ty, run)
        if best:
            _, tx, ty, run = best
            self.dock = {"tx": tx, "ty": ty, "len": min(run, 5)}

    # ------------------------------------------------------------------
    # 农田
    # ------------------------------------------------------------------
    def _place_farms(self):
        FW, FH = 5, 3
        barn = next((l for l in self.lots if l["kind"] == "barn"), None)
        cands = []
        for oy in range(2, ROWS - FH - 1):
            for ox in range(2, COLS - FW - 1):
                ok = True
                for ty in range(oy, oy + FH):
                    for tx in range(ox, ox + FW):
                        if (tx, ty) in self._lotset:
                            ok = False
                            break
                        if self.tiles[ty][tx] not in ("grass", "grass2", "grass3"):
                            ok = False
                            break
                    if not ok:
                        break
                if not ok:
                    continue
                if barn:
                    bx = barn["tx"]
                    by = barn["side"] == "north" and (barn["rect"][3] // TILE) or (barn["rect"][1] // TILE)
                    d = abs(ox + FW / 2.0 - bx) + abs(oy + FH / 2.0 - by) * 1.6
                else:
                    d = abs(ox - COLS / 2.0) + abs(oy - ROWS / 2.0)
                cands.append((d, ox, oy))
        cands.sort()
        self.farms = []
        for _, ox, oy in cands:
            if len(self.farms) >= 3:
                break
            clash = False
            for f in self.farms:
                if not (ox + FW + 2 < f["tx"] or ox > f["tx"] + f["w"] + 2 or
                        oy + FH + 2 < f["ty"] or oy > f["ty"] + f["h"] + 2):
                    clash = True
                    break
            if clash:
                continue
            cells = []
            for cy in range(FH):
                for cx in range(FW):
                    self.tiles[oy + cy][ox + cx] = "farm"
                    for sy in range(2):
                        for sx in range(2):
                            cells.append({
                                "x": (ox + cx) * TILE + sx * 4,
                                "y": (oy + cy) * TILE + 1 + sy * 4,
                                "stage": 0,
                                "grow": _rand01(cx * 3 + sx, cy * 5 + sy + len(self.farms), 41),
                            })
            self.farms.append({"tx": ox, "ty": oy, "w": FW, "h": FH, "cells": cells})

    # ------------------------------------------------------------------
    # 装饰散布
    # ------------------------------------------------------------------
    def _scatter_decor(self):
        self.trees = []
        self.props = []
        blocked = [[False] * COLS for _ in range(ROWS)]
        for lot in self.lots:
            x0, y0, x1, y1 = lot["rect"]
            for ty in range(max(0, y0 // TILE - 1), min(ROWS, (y1 - 1) // TILE + 2)):
                for tx in range(max(0, x0 // TILE - 1), min(COLS, (x1 - 1) // TILE + 2)):
                    blocked[ty][tx] = True
        for farm in self.farms:
            for ty in range(farm["ty"] - 1, farm["ty"] + farm["h"] + 1):
                for tx in range(farm["tx"] - 1, farm["tx"] + farm["w"] + 1):
                    if 0 <= tx < COLS and 0 <= ty < ROWS:
                        blocked[ty][tx] = True
        if self.dock:
            for k in range(self.dock["len"] + 1):
                tx = self.dock["tx"] + k
                if tx < COLS:
                    blocked[self.dock["ty"]][tx] = True
                    if self.dock["ty"] + 1 < ROWS:
                        blocked[self.dock["ty"] + 1][tx] = True

        def grove(tx, ty):
            v = 0.0
            for f, amp, ph in ((0.9, 0.5, 1.1), (1.7, 0.3, 3.3), (3.1, 0.2, 5.0)):
                v += amp * math.sin(tx * f * 0.55 + ph) * math.cos(ty * f * 0.62 - ph)
            return v

        for ty in range(2, ROWS - 2):
            for tx in range(2, COLS - 2):
                t = self.tiles[ty][tx]
                if t not in ("grass", "grass2", "grass3") or blocked[ty][tx]:
                    continue
                g = grove(tx, ty)
                roll = _rand01(tx, ty, 61)
                if g > 0.28 and roll > 0.50:
                    self.trees.append({
                        "x": tx * TILE + TILE // 2,
                        "y": ty * TILE + TILE,
                        "variant": _srgb_hash(tx, ty, 62) % 3,
                        "seed": _srgb_hash(tx, ty, 63) % 100000,
                        "phase": _rand01(tx, ty, 64) * 6.283,
                    })
                    blocked[ty][tx] = True
                elif 0.05 < g <= 0.28 and roll > 0.78:
                    self.props.append({"kind": "bush", "x": tx * TILE + 4, "y": ty * TILE + 8,
                                       "variant": _srgb_hash(tx, ty, 65) % 3,
                                       "seed": _srgb_hash(tx, ty, 66) % 100000})
                    blocked[ty][tx] = True
                elif roll > 0.955:
                    self.props.append({"kind": "rock", "x": tx * TILE + 4, "y": ty * TILE + 7,
                                       "variant": _srgb_hash(tx, ty, 67) % 3,
                                       "seed": _srgb_hash(tx, ty, 68) % 100000})
                elif roll > 0.90:
                    self.props.append({"kind": "flower", "x": tx * TILE + 3, "y": ty * TILE + 5,
                                       "variant": _srgb_hash(tx, ty, 69) % 4, "seed": 0})

        # 街边路灯与长椅
        lo, hi = self._land_span()
        sr = self.street_row
        for tx in range(lo + 1, hi, 5):
            if self.tiles[sr][tx] in ("path", "plaza"):
                ty = sr - 1
                if self.tiles[ty][tx] in ("grass", "grass2", "sand", "plot"):
                    self.props.append({"kind": "lamp", "x": tx * TILE + 4, "y": (ty + 1) * TILE,
                                       "variant": 0, "seed": 0})
                    blocked[ty][tx] = True
            if self.tiles[sr + 1][tx] in ("path", "plaza"):
                ty = sr + 2
                if self.tiles[ty][tx] in ("grass", "grass2", "sand", "plot"):
                    self.props.append({"kind": "bench", "x": tx * TILE + 4, "y": (ty + 1) * TILE,
                                       "variant": 0, "seed": 0})
                    blocked[ty][tx] = True
        # ---- 喷泉：广场正中心 ----
        # 这是小镇的地标，放在广场几何中心，底座落在 plaza 最下一行。
        mid = (lo + hi) // 2
        fx = mid * TILE + TILE // 2
        fy = (sr + 2) * TILE + TILE
        self.props.append({"kind": "fountain", "x": fx, "y": fy,
                           "variant": 0, "seed": 0})
        self.fountain_px = (fx, fy)
        # 把底座周围两格标记成已占用，免得树长到喷泉里
        for ty in range(sr, sr + 3):
            for tx in range(mid - 2, mid + 3):
                if 0 <= tx < COLS and 0 <= ty < ROWS:
                    blocked[ty][tx] = True
        # 水井挪到民居边上，不再占广场正中
        for tx in range(mid + 6, min(COLS - 2, mid + 16)):
            if self.tiles[sr + 2][tx] in ("grass", "grass2", "path") and not blocked[sr + 2][tx]:
                self.props.append({"kind": "well", "x": tx * TILE + 4,
                                   "y": (sr + 3) * TILE, "variant": 0, "seed": 0})
                blocked[sr + 2][tx] = True
                break
        # ---- 鱼塘配套：荷叶 / 芦苇 ----
        # 栈桥已经写进地形了（pier 属于 LAND，可走）
        for l in getattr(self, "lilies", []):
            self.props.append({"kind": "lily", "x": l["tx"] * TILE + 4,
                               "y": l["ty"] * TILE + 4, "variant": l["variant"],
                               "seed": 0})
        for r in getattr(self, "reeds", []):
            self.props.append({"kind": "reed", "x": r["x"], "y": r["y"],
                               "variant": r["variant"], "seed": 0})
        # 码头
        if self.dock:
            self.props.append({"kind": "dock", "x": self.dock["tx"] * TILE,
                               "y": (self.dock["ty"] + 1) * TILE,
                               "variant": self.dock["len"], "seed": 0})

    # ------------------------------------------------------------------
    # 可通行
    # ------------------------------------------------------------------
    def _rebuild_walk(self):
        for ty in range(ROWS):
            for tx in range(COLS):
                self.walk[ty][tx] = self.tiles[ty][tx] in LAND
        for lot in self.lots:
            if lot["level"] <= 0:
                continue
            x0, y0, x1, y1 = lot["rect"]
            for ty in range(max(0, y0 // TILE), min(ROWS, (y1 - 1) // TILE + 1)):
                for tx in range(max(0, x0 // TILE), min(COLS, (x1 - 1) // TILE + 1)):
                    self.walk[ty][tx] = False
        for t in self.trees:
            tx, ty = t["x"] // TILE, (t["y"] - 1) // TILE
            if 0 <= tx < COLS and 0 <= ty < ROWS:
                self.walk[ty][tx] = False
        # 喷泉的水池不能站人。广场正中心正好就是村民的社交集结点，
        # 不封的话到了傍晚大家会一排站在水池里。
        # 只封水池那一行（本体最下一行）：主街的 street_row / street_row+1
        # 两行都保持贯通，绕行和聚会都不受影响，人也自然聚到喷泉边上。
        for tx, ty in self.fountain_pool_tiles():
            if 0 <= tx < COLS and 0 <= ty < ROWS:
                self.walk[ty][tx] = False

    def fountain_box(self):
        """喷泉贴图占据的像素框 (x0, y0, x1, y1)，右侧/下侧是开区间。

        渲染时贴图落在 (x - W/2, y - H)，这里按同一套算法反推，
        免得工具和渲染各写一份、哪天改了贴图尺寸就对不上。
        """
        if not self.fountain_px:
            return None
        fx, fy = self.fountain_px
        return (fx - art.FOUNTAIN_W // 2, fy - art.FOUNTAIN_H,
                fx + art.FOUNTAIN_W // 2, fy)

    def fountain_pool_tiles(self):
        """水池占的格子 = 本体最下面那一行。"""
        box = self.fountain_box()
        if box is None:
            return []
        ty = (box[3] - 1) // TILE
        return [(tx, ty) for tx in range(box[0] // TILE, (box[2] - 1) // TILE + 1)]

    def door_px(self, lot):
        """建筑门口的像素坐标。

        注意：门前的落脚点必须是占地格「下面那一格」，
        用 (y1+3)//TILE 会算进建筑内部（南侧房子尤其明显），
        必须和 _carve_connectors 用同一套算法。
        """
        x0, y0, x1, y1 = lot["rect"]
        tx = ((x0 + x1) // 2) // TILE
        ty = (y1 - 1) // TILE + 1
        return (tx * TILE + 4, ty * TILE + 4)

    def walkable(self, tx, ty):
        return 0 <= tx < COLS and 0 <= ty < ROWS and self.walk[ty][tx]

    def walkable_px(self, x, y):
        return self.walkable(int(x) // TILE, int(y) // TILE)

    def nearest_walkable(self, tx, ty, max_r=10):
        if self.walkable(tx, ty):
            return tx, ty
        for r in range(1, max_r + 1):
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    if max(abs(dx), abs(dy)) != r:
                        continue
                    if self.walkable(tx + dx, ty + dy):
                        return tx + dx, ty + dy
        return tx, ty

    def random_walkable(self, rng, tries=300):
        for _ in range(tries):
            tx = rng.randrange(1, COLS - 1)
            ty = rng.randrange(1, ROWS - 1)
            if self.walkable(tx, ty):
                return tx, ty
        return COLS // 2, self.street_row

    def stats(self):
        from collections import Counter
        cnt = Counter(t for row in self.tiles for t in row)
        return {
            "tiles": dict(cnt),
            "land": sum(1 for row in self.walk for v in row if v),
            "lots": len(self.lots),
            "houses": sum(1 for l in self.lots if l["is_house"]),
            "trees": len(self.trees),
            "props": len(self.props),
            "farms": len(self.farms),
            "farm_cells": sum(len(f["cells"]) for f in self.farms),
            "street_row": self.street_row,
            "dock": self.dock,
        }
