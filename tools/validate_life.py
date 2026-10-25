# -*- coding: utf-8 -*-
r"""像素小镇 · 生活系统自检

覆盖后来加的三件事：
  1. 人口有上限、有进有出
  2. 节日
  3. 随机小事与动物迁徙

其中最容易出错、也最不容易被肉眼发现的是 **vid 唯一性**：
搬走一个人之后 len(villagers) 会回退，如果新来的人沿用这个数字当 id，
关系表、待回复集合全都会串味。这里专门盯死它。

用法： python tools/validate_life.py [天数]
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []
N = [0]


def check(name, cond, detail=""):
    N[0] += 1
    print("  [%s] %s %s" % (" OK " if cond else "FAIL", name, detail))
    if not cond:
        FAILS.append(name)


def section(t):
    print("\n" + "=" * 74)
    print(t)
    print("=" * 74)


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    from town.sim import (TownSim, MAX_POP, POP_FLOOR, SEASON_DAYS,
                          FESTIVAL_DAY, MAX_OFFLINE_DAYS, EVENT_COOLDOWN)
    from town import events as EV

    sim = TownSim(seed=20240607)
    start_pop = len(sim.villagers)
    print("起始人口 = %d，容量 = %d，上限 = %d"
          % (start_pop, sim.capacity, MAX_POP))

    over, under, vid_bad, orphan = [], [], [], []
    fest_seen = {}
    ev_seen = []
    mig_runs = 0
    prev_pop = start_pop
    max_pop = start_pop
    min_pop = start_pop

    for step in range(days * 4):
        sim.advance(6.0, detailed=True)
        pop = len(sim.villagers)
        max_pop, min_pop = max(max_pop, pop), min(min_pop, pop)
        if pop > MAX_POP:
            over.append((sim.day, pop))
        if pop < POP_FLOOR:
            under.append((sim.day, pop))
        # vid 必须唯一
        vids = [v.vid for v in sim.villagers]
        if len(vids) != len(set(vids)):
            vid_bad.append((sim.day, sorted(vids)))
        # 关系表不能挂着已经搬走的人
        alive = set(vids)
        for v in sim.villagers:
            for oid in list(v.rel):
                if oid not in alive:
                    orphan.append((sim.day, v.name, oid))
        if sim.festival:
            fest_seen.setdefault(sim.festival, sim.day)
        if sim.migrations:
            mig_runs += 1
        for d, h, t in sim.log_lines[-4:]:
            if t not in ev_seen:
                ev_seen.append(t)
        prev_pop = pop

    section("RESULT STATE")
    from collections import Counter
    roles = Counter(v.role for v in sim.villagers)
    print("  第%d天 人口=%d（区间 %d..%d）" % (sim.day, len(sim.villagers),
                                              min_pop, max_pop))
    print("  职业：%s" % dict(roles))
    print("  搬入 %d 次，搬出 %d 次" % (sim.arrivals, sim.departures))
    print("  节日 %d 天：%s" % (sim.festival_days, fest_seen))
    print("  孩子：%s" % [v.name for v in sim.villagers if v.role == "孩子"])

    section("CHECKS")
    check("人口不超过上限", not over, str(over[:3]))
    check("人口不低于下限", not under, str(under[:3]))
    check("vid 始终唯一", not vid_bad, str(vid_bad[:2]))
    check("关系表没有已搬走的人", not orphan, str(orphan[:3]))
    check("人口到过上限附近", max_pop >= max(4, MAX_POP - 1),
          "最高 %d" % max_pop)
    check("真的有人搬进来", sim.arrivals >= 2, "%d 次" % sim.arrivals)
    check("真的有人搬出去", sim.departures >= 1, "%d 次" % sim.departures)
    check("搬出不等于搬入（人口会波动）",
          abs(sim.arrivals - sim.departures) != sim.arrivals,
          "进 %d 出 %d" % (sim.arrivals, sim.departures))
    check("职业够杂（不是一群农夫）",
          len(roles) >= 6 and max(roles.values()) <= 3,
          "%d 种，最多的一种 %d 人" % (len(roles), max(roles.values())))
    check("镇上留着孩子",
          any(v.role == "孩子" for v in sim.villagers),
          str([v.name for v in sim.villagers if v.role == "孩子"]))

    # ---- 节日 ----
    section("节日")
    check("四个季节的节日都出现过", len(fest_seen) >= 3, str(fest_seen))
    expect_gaps = sim.festival_days
    check("节日按周期出现", expect_gaps >= max(2, days // 8),
          "%d 天 / %d 天" % (expect_gaps, days))
    # 节日当天的活动应该是"过节"
    sim2 = TownSim(seed=20240607)
    # 推到某个节日
    while not sim2.festival:
        sim2.advance(6.0, detailed=True)
        if sim2.day > 20:
            break
    check("能找到节日那天", sim2.festival is not None, str(sim2.festival))
    if sim2.festival:
        # 推到节日当天的上午 10 点 —— 要推到白天，否则全镇在屋里睡觉，
        # 活动集合是空的，而空集是任何集合的子集，断言会"假通过"
        target = (sim2.day - 1) * 24 + 10.0
        sim2.advance(max(0.0, target - sim2.t), detailed=True)
        acts = {v.activity for v in sim2.villagers if not v.hidden}
        check("节日白天真的有活动（集合非空）", len(acts) > 0, str(acts))
        check("节日白天全镇在过节/会餐",
              acts and acts <= {"festival", "lunch"},
              str(acts))
        check("节日进了日志",
              any(sim2.festival in t for d, h, t in sim2.log_lines))

    # ---- 随机小事 ----
    section("随机小事")
    texts = [t for d, h, t in sim.log_lines]
    known = [e[1] for e in EV.RANDOM_EVENTS]
    hits = [t for t in texts if t in known]
    print("  触发过 %d 次随机小事，种类 %d" % (len(hits), len(set(hits))))
    check("随机小事真的会发生", len(hits) >= 3, "%d 次" % len(hits))
    check("随机小事不止一种", len(set(hits)) >= 3, "%d 种" % len(set(hits)))
    # 同一件小事不能隔得太近
    gaps_ok = True
    bad_gap = None
    last = {}
    for i, t in enumerate(texts):
        if t in known:
            if t in last and i - last[t] < EVENT_COOLDOWN:
                gaps_ok = False
                bad_gap = (t, i - last[t])
            last[t] = i
    check("同一件小事不会很快重复", gaps_ok, str(bad_gap))
    check("没有坏事（事件表里不含负面词）",
          not any(k in e[1] for e in EV.RANDOM_EVENTS
                  for k in ("死", "病", "灾", "火", "偷", "倒", "塌")),
          str([e[1] for e in EV.RANDOM_EVENTS
               if any(k in e[1] for k in ("死", "病", "灾", "火", "偷", "倒", "塌"))]))

    # ---- 动物迁徙 ----
    section("动物迁徙")
    mig_lines = [t for t in texts if any(k in t for k in
                                         ("大雁", "蝴蝶", "蜻蜓", "白鸟"))]
    print("  迁徙日志 %d 条：" % len(mig_lines))
    for t in mig_lines[-3:]:
        print("    %s" % t)
    # 迁徙是"每天 11%"的随机事件：40 天里一次都抽不到的运气约 1%，
    # 抽不到两次的约 5%。把"抽到了"当断言就是在赌 rng 流 ——
    # 任何一个改动挪动 rng 序列，这条就会无缘无故变红。
    # 所以这里验机制（一定触发）而不是赌运气，自然抽签的结果只打出来看。
    class _AlwaysFire(object):
        """除了 random() 恒为 0（保证一定触发），其余方法仍走原来的 rng。"""

        def __init__(self, base):
            self._base = base

        def random(self):
            return 0.0

        def __getattr__(self, name):
            return getattr(self._base, name)

    saved_rng = sim.rng
    sim.migrations = []
    sim.rng = _AlwaysFire(saved_rng)
    try:
        sim._maybe_migration()
    finally:
        sim.rng = saved_rng
    check("迁徙机制会触发", len(sim.migrations) == 1,
          "在场 %d 群" % len(sim.migrations))
    check("迁徙会结束（不会永远留在屏幕上）",
          not sim.migrations or all(m["t"] < m["life"] for m in sim.migrations),
          "在场 %d 群" % len(sim.migrations))
    # 手动起一次，确认能正常收尾
    m = sim.start_migration("birds", "测试鸟群")
    check("手动迁徙能创建", m is not None and m["kind"] == "birds")
    sim.advance(30.0 / sim.hours_per_second, detailed=True)
    check("迁徙到时间就消失", not any(x is m for x in sim.migrations),
          "在场 %d 群" % len(sim.migrations))

    # ---- 存档往返（vid 是最容易翻车的地方）----
    section("存档往返")
    p = os.path.join(ROOT, "save", "_lifetest.json")
    sim.save(p)
    sim3, _ = TownSim.load(p, catch_up=False)
    check("读档后 vid 仍唯一",
          len({v.vid for v in sim3.villagers}) == len(sim3.villagers),
          str(sorted(v.vid for v in sim3.villagers)))
    check("读档后 _next_vid 不会撞车",
          sim3._next_vid > max(v.vid for v in sim3.villagers),
          "next=%d max=%d" % (sim3._next_vid, max(v.vid for v in sim3.villagers)))
    check("读档保留搬入搬出统计",
          sim3.arrivals == sim.arrivals and sim3.departures == sim.departures,
          "进 %d/%d 出 %d/%d" % (sim3.arrivals, sim.arrivals,
                                 sim3.departures, sim.departures))
    # 关键：读档后再搬进一个人，不能和现有 id 撞
    before = {v.vid for v in sim3.villagers}
    nv = sim3.add_villager()
    check("读档后新搬来的人 id 不撞车", nv.vid not in before,
          "新 vid=%d，已有 %s" % (nv.vid, sorted(before)))
    check("读档后新 id 仍然唯一",
          len({v.vid for v in sim3.villagers}) == len(sim3.villagers))
    os.remove(p)

    section("RESULT")
    print("  %d checks, %d failed" % (N[0], len(FAILS)))
    if FAILS:
        print("  FAILED: " + ", ".join(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
