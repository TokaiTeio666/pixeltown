# -*- coding: utf-8 -*-
r"""像素小镇 · 世界生成自检

把 44x26 的地图直接打成 ASCII，在终端里看小镇布局；
再用断言检查：房屋是否都在陆地上、是否互相重叠、门口能否走到街上、
广场正中心的喷泉有没有放正、有没有把主街堵死。

用法： python tools/validate_world.py [seed]
"""
import os
import sys
from collections import Counter, deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from town.world import World, COLS, ROWS, TILE, LAND  # noqa: E402
from town import art  # noqa: E402

FAILS = []
N = [0]

CH = {
    "grass": ".", "grass2": ",", "grass3": "'", "sand": ":", "shallow": "~",
    "water": "w", "deep": "W", "void": " ", "path": "=", "plaza": "+",
    "plot": "o", "farm": "f",
}
KIND_CH = {"hall": "H", "shop": "S", "smithy": "M", "barn": "B", "house": "h"}


def check(name, cond, detail=""):
    N[0] += 1
    if cond:
        print("  [ OK ] %s %s" % (name, detail))
    else:
        print("  [FAIL] %s %s" % (name, detail))
        FAILS.append(name)


def draw(world, mode="terrain"):
    """mode: terrain | lots | walk"""
    grid = []
    for y in range(ROWS):
        row = []
        for x in range(COLS):
            if mode == "terrain":
                row.append(CH.get(world.tiles[y][x], "?"))
            elif mode == "walk":
                row.append("." if world.walk[y][x] else "#")
            else:
                row.append(CH.get(world.tiles[y][x], "?"))
        grid.append(row)
    if mode == "lots":
        for lot in world.lots:
            x0, y0, x1, y1 = lot["rect"]
            ch = KIND_CH.get(lot["kind"], "?")
            for ty in range(max(0, y0 // TILE), min(ROWS, (y1 - 1) // TILE + 1)):
                for tx in range(max(0, x0 // TILE), min(COLS, (x1 - 1) // TILE + 1)):
                    grid[ty][tx] = ch
        for farm in world.farms:
            for ty in range(farm["ty"], farm["ty"] + farm["h"]):
                for tx in range(farm["tx"], farm["tx"] + farm["w"]):
                    if grid[ty][tx] not in KIND_CH.values():
                        grid[ty][tx] = "f"
        if world.dock:
            for k in range(world.dock["len"]):
                tx = world.dock["tx"] + k
                if tx < COLS:
                    grid[world.dock["ty"]][tx] = "d"
    print("   " + "".join(str(i % 10) for i in range(COLS)))
    for y in range(ROWS):
        print("%2d " % y + "".join(grid[y]) + " %d" % y)


def reachable(world, start, goals, blocked_ok_px=True):
    """在可通行格上 BFS，看能否到达 goals 中任意一格。"""
    if not world.walkable(*start):
        return False
    seen = {start}
    q = deque([start])
    gset = set(goals)
    while q:
        cx, cy = q.popleft()
        if (cx, cy) in gset:
            return True
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = cx + dx, cy + dy
            if (nx, ny) in seen:
                continue
            if world.walkable(nx, ny):
                seen.add((nx, ny))
                q.append((nx, ny))
    return False


def main():
    argv = sys.argv[1:]
    out = None
    if "--out" in argv:
        i = argv.index("--out")
        out = argv[i + 1]
        del argv[i:i + 2]
    if out:
        real = sys.stdout
        # 名字别和后面 with open(...) as fh 撞车：闭包捕获的是变量不是值
        logfh = open(os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), out), "w", encoding="utf-8")

        class Tee(object):
            def write(self, s):
                real.write(s)
                logfh.write(s)

            def flush(self):
                real.flush()
                logfh.flush()

        sys.stdout = Tee()

    seed = int(argv[0]) if argv else 20240607
    w = World(seed)

    print("=" * 74)
    print("TERRAIN MAP  (seed=%d)" % seed)
    print("  . , grass   : sand   ~ shallow   w water   W deep   (space)=transparent")
    print("  = path   + plaza   o plot   f farm")
    print("=" * 74)
    draw(w, "terrain")

    print("\n" + "=" * 74)
    print("LOT MAP   H=hall S=shop M=smithy B=barn h=house d=dock")
    print("=" * 74)
    draw(w, "lots")

    st = w.stats()
    print("\n" + "=" * 74)
    print("STATS")
    print("=" * 74)
    for k in ("land", "lots", "houses", "trees", "props", "farms", "farm_cells", "street_row", "dock"):
        print("  %-12s %s" % (k, st[k]))
    print("  %-12s %s" % ("tiles", st["tiles"]))
    land_tiles = st["tiles"].get("grass", 0) + st["tiles"].get("grass2", 0) + st["tiles"].get("grass3", 0)
    print("  grass-only  %d" % land_tiles)

    print("\n" + "=" * 74)
    print("CHECKS")
    print("=" * 74)
    check("has lots", st["lots"] >= 8, "n=%d" % st["lots"])
    check("has hall/shop/barn/smithy",
          {"hall", "shop", "barn", "smithy"} <= {l["kind"] for l in w.lots},
          str(sorted({l["kind"] for l in w.lots})))
    check("has houses", st["houses"] >= 5, "n=%d" % st["houses"])
    check("has farms", st["farms"] >= 1, "n=%d" % st["farms"])
    check("has trees", st["trees"] >= 10, "n=%d" % st["trees"])
    check("has dock", w.dock is not None, str(w.dock))

    # --- 房屋占地必须全在陆地 ---
    bad = []
    for lot in w.lots:
        x0, y0, x1, y1 = lot["rect"]
        for ty in range(max(0, y0 // TILE), min(ROWS, (y1 - 1) // TILE + 1)):
            for tx in range(max(0, x0 // TILE), min(COLS, (x1 - 1) // TILE + 1)):
                if w.tiles[ty][tx] not in LAND:
                    bad.append((lot["id"], tx, ty, w.tiles[ty][tx]))
    check("all lots on land", not bad, str(bad[:6]))

    # --- 房屋互不重叠 ---
    overlaps = []
    for i in range(len(w.lots)):
        for j in range(i + 1, len(w.lots)):
            a, b = w.lots[i]["rect"], w.lots[j]["rect"]
            if not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]):
                overlaps.append((w.lots[i]["id"], w.lots[j]["id"]))
    check("no lot overlaps", not overlaps, str(overlaps[:6]))

    # --- 建筑不能压到街道 ---
    on_street = []
    for lot in w.lots:
        x0, y0, x1, y1 = lot["rect"]
        for ty in range(max(0, y0 // TILE), min(ROWS, (y1 - 1) // TILE + 1)):
            for tx in range(max(0, x0 // TILE), min(COLS, (x1 - 1) // TILE + 1)):
                if w.tiles[ty][tx] in ("path", "plaza"):
                    on_street.append((lot["id"], tx, ty))
    check("no lot sits on street/plaza", not on_street, str(on_street[:6]))

    # --- 门口连通性（先假设全部建成，再看还能不能走到街上）---
    for lot in w.lots:
        lot["level"] = 3
    w._rebuild_walk()
    sr = w.street_row
    street_tiles = [(x, sr) for x in range(COLS) if w.tiles[sr][x] == "path"]
    unreachable = []
    for lot in w.lots:
        x0, y0, x1, y1 = lot["rect"]
        if lot["side"] == "north":
            front = (lot["tx"], max(0, (y1 - 1) // TILE))
        else:
            front = (lot["tx"], min(ROWS - 1, y0 // TILE))
        fx, fy = w.nearest_walkable(*front)
        if not reachable(w, (fx, fy), street_tiles):
            unreachable.append(lot["id"])
    check("every door reaches the street", not unreachable, str(unreachable))

    # --- 喷泉：小镇广场正中心的地标 ---
    fount = [p for p in w.props if p["kind"] == "fountain"]
    check("has exactly one fountain", len(fount) == 1, "n=%d" % len(fount))
    plaza_pts = [(x, y) for y in range(ROWS) for x in range(COLS) if w.tiles[y][x] == "plaza"]
    check("has a plaza", len(plaza_pts) >= 8, "tiles=%d" % len(plaza_pts))
    if fount and plaza_pts:
        f = fount[0]
        pxs = [p[0] for p in plaza_pts]
        mid = (min(pxs) + max(pxs)) // 2
        check("fountain at plaza centre", f["x"] == mid * TILE + TILE // 2,
              "x=%d centre=%d" % (f["x"], mid * TILE + TILE // 2))
        box = w.fountain_box()
        print("  fountain box px=%s  tiles x %d..%d  y %d..%d"
              % (box, box[0] // TILE, (box[2] - 1) // TILE,
                 box[1] // TILE, (box[3] - 1) // TILE))
        # 本体必须整个落在广场上，歪到水里就成了"岛边上的喷泉"而不是广场喷泉
        off = []
        for ty in range(box[1] // TILE, (box[3] - 1) // TILE + 1):
            for tx in range(box[0] // TILE, (box[2] - 1) // TILE + 1):
                if w.tiles[ty][tx] not in ("plaza", "path"):
                    off.append((tx, ty, w.tiles[ty][tx]))
        check("fountain fully on the square", not off, str(off[:6]))
        # 树和别的道具不许插进喷泉框里（长椅穿进水池最难看）
        clash = []
        for t in w.trees:
            if box[0] <= t["x"] < box[2] and box[1] <= t["y"] <= box[3]:
                clash.append("tree@%d,%d" % (t["x"], t["y"]))
        for p in w.props:
            if p is f:
                continue
            if box[0] <= p["x"] < box[2] and box[1] <= p["y"] <= box[3]:
                clash.append("%s@%d,%d" % (p["kind"], p["x"], p["y"]))
        check("nothing overlaps the fountain", not clash, str(clash[:6]))
        # 水池格子不能站人
        wet = [t for t in w.fountain_pool_tiles() if w.walkable(*t)]
        check("pool is not walkable", not wet, str(wet[:6]))
        # 但主街不能被喷泉切断：西头必须还能走到东头
        sr = w.street_row
        street = [(x, sr) for x in range(COLS) if w.tiles[sr][x] in ("path", "plaza")]
        crossable = bool(street) and reachable(w, street[0], [street[-1]])
        check("main street still crosses the square", crossable,
              "%s -> %s" % (street[0] if street else None, street[-1] if street else None))
        # 人还得能贴着喷泉站（否则社交点被喷泉自己封死了）
        around = [(x, y) for y in range(max(0, box[1] // TILE - 1), (box[3] - 1) // TILE + 2)
                  for x in range(max(0, box[0] // TILE - 1), (box[2] - 1) // TILE + 2)
                  if w.walkable(x, y)]
        check("can stand around the fountain", len(around) >= 8, "n=%d" % len(around))

    # --- 重新打开全部建成时的行走图，看陆地是否被切碎 ---
    seen = set()
    start = w.nearest_walkable(COLS // 2, sr + 1)
    q = deque([start])
    seen.add(start)
    while q:
        cx, cy = q.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = cx + dx, cy + dy
            if (nx, ny) not in seen and w.walkable(nx, ny):
                seen.add((nx, ny))
                q.append((nx, ny))
    total_walk = sum(1 for y in range(ROWS) for x in range(COLS) if w.walk[y][x])
    print("  walkable=%d  one-connected-component=%d (%.0f%%)"
          % (total_walk, len(seen), 100.0 * len(seen) / max(1, total_walk)))
    check("walkable area mostly connected", len(seen) >= total_walk * 0.90,
          "%d / %d" % (len(seen), total_walk))

    # --- 地形比例 ---
    tot = COLS * ROWS
    void = st["tiles"].get("void", 0)
    water = sum(st["tiles"].get(k, 0) for k in ("shallow", "water", "deep"))
    print("  void=%.0f%%  water=%.0f%%  land=%.0f%%"
          % (100.0 * void / tot, 100.0 * water / tot, 100.0 * (tot - void - water) / tot))
    check("transparent margin exists", void > tot * 0.06, "%.0f%%" % (100.0 * void / tot))
    check("water ring exists", water > tot * 0.10, "%.0f%%" % (100.0 * water / tot))

    print("\n" + "=" * 74)
    print("RESULT: %d checks, %d failed" % (N[0], len(FAILS)))
    if FAILS:
        print("FAILED: " + ", ".join(FAILS))
    print("=" * 74)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
