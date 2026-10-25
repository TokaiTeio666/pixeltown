# -*- coding: utf-8 -*-
r"""像素小镇 · 模拟器自检

推进若干游戏日，打印日志、成长进度、以及叠加了村民位置的 ASCII 地图，
再断言：村民不出界、站在可走的地面上、小镇只成长不倒退、存档往返一致、
离线补算有上限。

用法： python tools/validate_sim.py [days]
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:                                  # Windows 控制台默认不是 UTF-8，中文会变乱码
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from town.sim import TownSim, DAY_SECONDS, MAX_OFFLINE_DAYS  # noqa: E402
from town.world import COLS, ROWS, TILE  # noqa: E402

FAILS = []
N = [0]

CH = {"grass": ".", "grass2": ",", "grass3": "'", "sand": ":", "shallow": "~",
      "water": "w", "deep": "W", "void": " ", "path": "=", "plaza": "+",
      "farm": "f", "plot": "o"}

LETTERS = "abcdefghijklmnopqrstuvwxyz"


def check(name, cond, detail=""):
    N[0] += 1
    if cond:
        print("  [ OK ] %s %s" % (name, detail))
    else:
        print("  [FAIL] %s %s" % (name, detail))
        FAILS.append(name)


def villager_map(sim, title):
    grid = []
    for y in range(ROWS):
        grid.append([CH.get(sim.world.tiles[y][x], "?") for x in range(COLS)])
    present = []
    for i, v in enumerate(sim.villagers):
        if v.hidden:
            continue
        tx, ty = v.tile
        if 0 <= tx < COLS and 0 <= ty < ROWS:
            ch = LETTERS[i % len(LETTERS)]
            grid[ty][tx] = ch
            present.append((ch, v))
    print("\n" + "=" * 74)
    print("%s   第%d天 %02d:%02d  %s季 天气=%s  村民=%d(在场%d)" % (
        title, sim.day, int(sim.hour), int((sim.hour % 1) * 60),
        sim.season_name, sim.weather, len(sim.villagers), len(present)))
    print("  . , 草  : 沙  ~ 浅滩  w 水  = 路  + 广场  f 农田  (空)=透明")
    print("=" * 74)
    print("   " + "".join(str(i % 10) for i in range(COLS)))
    for y in range(ROWS):
        print("%2d " % y + "".join(grid[y]) + " %d" % y)
    print("-" * 74)
    for ch, v in present:
        print("  %s  %-4s %-4s %-6s @(%.0f,%.0f)  %s" % (
            ch, v.name, v.role, v.activity or v.state, v.x, v.y,
            "说：%s" % v.say if v.say else ""))
    return present


def lot_table(world):
    rows = []
    for l in sorted(world.lots, key=lambda L: (L["kind"], L["id"])):
        rows.append("    %-10s %-6s lvl=%d %s" % (
            l["id"], l["kind"], l["level"], "已解锁" if l["unlocked"] else "野草地"))
    return "\n".join(rows)


def main():
    argv = sys.argv[1:]
    out = None
    if "--out" in argv:
        i = argv.index("--out")
        out = argv[i + 1]
        del argv[i:i + 2]
    if out:
        real = sys.stdout
        fh = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), out),
                  "w", encoding="utf-8")

        class _Tee(object):
            def write(self, s):
                real.write(s)
                fh.write(s)

            def flush(self):
                real.flush()
                fh.flush()

        sys.stdout = _Tee()

    days = int(argv[0]) if argv else 12
    t0 = time.time()
    sim = TownSim(seed=20240607)

    print("=" * 74)
    print("INITIAL STATE")
    print("=" * 74)
    st = sim.world.stats()
    print("  land=%d lots=%d houses=%d farms=%d trees=%d dock=%s" % (
        st["land"], st["lots"], st["houses"], st["farms"], st["trees"], st["dock"]))
    print(lot_table(sim.world))
    print("  villagers=%d  %s" % (len(sim.villagers),
                                  ", ".join("%s(%s)" % (v.name, v.role) for v in sim.villagers)))
    print("  growth queue = %d 步" % len(sim.queue))
    villager_map(sim, "START")

    # ---- 按小时采样日程 ----
    print("\n" + "=" * 74)
    print("DAILY RHYTHM (activities by hour)")
    print("=" * 74)
    sample_hours = [2, 6.5, 9, 12.5, 15, 18, 20, 23]
    for h in sample_hours:
        sim2 = TownSim(seed=20240607)
        # 推进到当天的 h 点
        target = 24.0 + h
        sim2.advance(target - sim2.t, detailed=True)
        acts = {}
        hidden = 0
        for v in sim2.villagers:
            if v.hidden:
                hidden += 1
            else:
                # 刚搬来的人在下一个 tick 之前 activity 还是 None，
                # 直接拿它当字典键会让 sorted() 在 None 和 str 之间比较而崩掉
                key = v.activity or "未定"
                acts[key] = acts.get(key, 0) + 1
        print("  %5.1f时  在场%2d 屋内%2d  %s" % (
            h, len(sim2.villagers) - hidden, hidden,
            " ".join("%s=%d" % kv for kv in sorted(acts.items()))))

    # ---- 长跑 ----
    print("\n" + "=" * 74)
    print("RUN %d GAME DAYS" % days)
    print("=" * 74)
    prev_sum = sum(l["level"] for l in sim.world.lots)
    monotonic = True
    out_of_bounds = []
    off_walk = []
    stuck = {}

    steps = int(days * 24 / 0.25)
    for i in range(steps):
        sim.advance(0.25, detailed=True)
        s = sum(l["level"] for l in sim.world.lots)
        if s < prev_sum:
            monotonic = False
        prev_sum = s
        for v in sim.villagers:
            if not (-4 <= v.x <= COLS * TILE + 4 and -4 <= v.y <= ROWS * TILE + 4):
                out_of_bounds.append((v.name, round(v.x, 1), round(v.y, 1)))
            else:
                tx, ty = v.tile
                if not sim.world.walkable(tx, ty):
                    off_walk.append((v.name, tx, ty, sim.world.tiles[ty][tx]))
            key = v.vid
            pos = (round(v.x), round(v.y))
            last = stuck.get(key)
            if last == pos:
                stuck[key] = (pos, stuck.get(("n", key), 0) + 1)
            else:
                stuck[key] = (pos, 0)
            stuck[("n", key)] = stuck[key][1]

    print(lot_table(sim.world))
    print("\n  day=%d hour=%.1f season=%s weather=%s" % (
        sim.day, sim.hour, sim.season_name, sim.weather))
    print("  population=%d  total level=%d  queue %d/%d" % (
        len(sim.villagers), sum(l["level"] for l in sim.world.lots),
        sim.queue_index, len(sim.queue)))
    print("  elapsed %.1fs" % (time.time() - t0))

    print("\n" + "=" * 74)
    print("RECENT LOG")
    print("=" * 74)
    for d, h, txt in sim.log_lines[-22:]:
        print("  D%-3d %05.1f  %s" % (d, h, txt))

    villager_map(sim, "AFTER %d DAYS" % days)

    print("\n" + "=" * 74)
    print("CHECKS")
    print("=" * 74)
    check("no exception over %d days" % days, True)
    check("levels never decrease", monotonic)
    check("villagers stay in bounds", not out_of_bounds, str(out_of_bounds[:5]))
    check("villagers stand on walkable tiles", len(off_walk) <= 3, str(off_walk[:5]))
    check("growth queue advances", sim.queue_index > 0,
          "%d/%d" % (sim.queue_index, len(sim.queue)))
    check("population grew", len(sim.villagers) > 5, "n=%d" % len(sim.villagers))
    check("visible growth pace (~2 steps/day)",
          sim.queue_index >= min(len(sim.queue), days * 2 - 2),
          "%d/%d steps in %d days" % (sim.queue_index, len(sim.queue), days))

    # ---- 存档往返 ----
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "save", "_selftest.json")
    sim.save(path)
    sim3, _ = TownSim.load(path, seed=20240607)
    same_t = abs(sim3.t - sim.t) < 1e-6
    same_lv = [l["level"] for l in sim3.world.lots] == [l["level"] for l in sim.world.lots]
    same_pop = len(sim3.villagers) == len(sim.villagers)
    print("\n  save file = %d bytes" % os.path.getsize(path))
    check("save/load keeps time", same_t, "%.2f vs %.2f" % (sim3.t, sim.t))
    check("save/load keeps lot levels", same_lv)
    check("save/load keeps villagers", same_pop,
          "%d vs %d" % (len(sim3.villagers), len(sim.villagers)))
    check("save/load keeps names",
          [v.name for v in sim3.villagers] == [v.name for v in sim.villagers])
    os.remove(path)

    # ---- 离线补算 ----
    sim4 = TownSim(seed=20240607)
    t_before = sim4.t
    txt = sim4.catch_up(3600 * 8)     # 离开 8 小时
    gained_days = (sim4.t - t_before) / 24.0
    print("\n  offline summary: %s" % txt)
    check("offline catch-up capped", gained_days <= MAX_OFFLINE_DAYS + 0.05,
          "gained %.2f days (cap %.0f)" % (gained_days, MAX_OFFLINE_DAYS))
    check("offline still advanced", sim4.t > 24.0 + 1.0, "t=%.1f" % sim4.t)
    check("offline summary is text", isinstance(txt, str) and len(txt) > 4, txt)

    print("\n" + "=" * 74)
    print("RESULT: %d checks, %d failed" % (N[0], len(FAILS)))
    if FAILS:
        print("FAILED: " + ", ".join(FAILS))
    print("=" * 74)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
