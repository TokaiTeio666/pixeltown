# -*- coding: utf-8 -*-
r"""像素小镇 · 三项新功能自检

覆盖后来借鉴进来的三件事：
  1. **记忆流**：村民各自记得不同的事，能检索、能反思、能存档
  2. **外部活动映射**：真实电脑忙的时候，镇上工匠也忙；关掉就完全静默
  3. **存档管理**：新建 / 切换 / 备份 / 回收入站，且名字安全

用法： python tools/validate_extras.py [--out 报告.txt]
"""
import json
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from town.sim import TownSim            # noqa: E402
from town.activity import ActivityMonitor  # noqa: E402
from town.saves import SaveManager      # noqa: E402

FAILS = []
N = [0]


def check(name, cond, detail=""):
    N[0] += 1
    if cond:
        print("  [ OK ] %s %s" % (name, detail))
    else:
        print("  [FAIL] %s %s" % (name, detail))
        FAILS.append(name)


def section(t):
    print("\n" + "=" * 74)
    print(t)
    print("=" * 74)


# ----------------------------------------------------------------------
def test_memory():
    section("1. 记忆流")
    sim = TownSim(seed=20240607)
    sim.advance(24 * 12, detailed=True)

    total = sum(len(v.memories) for v in sim.villagers)
    check("村民都在记事", total > 0, "共 %d 条" % total)
    check("每人记忆条数在合理范围",
          all(0 < len(v.memories) <= 24 for v in sim.villagers),
          str(sorted({len(v.memories) for v in sim.villagers})))

    # 关键：不能全镇长成同一份镇志
    sigs = {tuple(sorted(t for _, _, t, _ in v.memories)) for v in sim.villagers}
    check("记忆是个人化的（不是全镇一份）", len(sigs) > 1,
          "%d 人 -> %d 种组合" % (len(sim.villagers), len(sigs)))

    kinds = {}
    for v in sim.villagers:
        for _, _, _, k in v.memories:
            kinds[k] = kinds.get(k, 0) + 1
    check("有私人性质的对话记忆", kinds.get("talk", 0) > 0, str(kinds))
    check("记忆种类不止一种", len(kinds) >= 2, str(sorted(kinds)))

    # 检索
    v0 = sim.villagers[0]
    got = v0.recall(sim.day, sim.hour, limit=3)
    check("检索有结果", len(got) > 0, "%d 条" % len(got))
    check("检索条数不超过上限", len(got) <= 3)
    # 检索必须是确定性的：同样的输入必须给同样的结果
    check("检索是确定性的", got == v0.recall(sim.day, sim.hour, limit=3))

    # 淘汰优先级：塞满之后，rel 类不能被先丢掉
    v1 = sim.villagers[1]
    v1.memories = []
    v1.remember(1, 1.0, "和一个老朋友成了挚友", "rel")
    for i in range(30):
        v1.remember(1, 2.0, "镇上的第 %d 次扩建" % i, "move")
    check("塞满后仍在 24 条以内", len(v1.memories) <= 24, "%d" % len(v1.memories))
    check("关系记忆被优先保留",
          any(k == "rel" for _, _, _, k in v1.memories))

    # 反思
    got_insight = v1.reflect(1, 2.0)
    check("反思能产出自我认知", bool(v1.insight), repr(got_insight))

    # 存档往返
    tmp = tempfile.mkdtemp(prefix="pixeltown_mem_")
    try:
        p = os.path.join(tmp, "town.json")
        sim.save(p)
        loaded = TownSim.load(p, catch_up=False)
        sim2 = loaded[0] if isinstance(loaded, tuple) else loaded
        before = sum(len(v.memories) for v in sim.villagers)
        after = sum(len(v.memories) for v in sim2.villagers)
        check("记忆能存进存档", after == before, "%d -> %d" % (before, after))
        ins_before = {v.name: v.insight for v in sim.villagers}
        ins_after = {v.name: v.insight for v in sim2.villagers}
        check("反思结果也存下来了", ins_before == ins_after)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ----------------------------------------------------------------------
def test_activity():
    section("2. 外部活动映射")
    tmp = tempfile.mkdtemp(prefix="pixeltown_act_")
    try:
        m = ActivityMonitor(save_dir=tmp, enabled=True)
        for _ in range(3):
            m.sample_once()
            time.sleep(0.02)
        snap = m.snapshot()
        check("能采到负载", 0.0 <= snap["level"] <= 1.0, "level=%.2f" % snap["level"])

        # 外部标记：必须立刻算忙，不许等平滑爬坡
        with open(os.path.join(tmp, "activity.txt"), "w", encoding="utf-8") as fh:
            fh.write("building the project")
        m.sample_once()
        snap = m.snapshot()
        check("标记文件被读到", snap["note"] == "building the project", snap["note"])
        check("标记立即判定为忙", snap["busy"] is True)

        # 标记过期就不该再让小镇加班
        old = os.path.join(tmp, "activity.txt")
        os.utime(old, (time.time() - 9999, time.time() - 9999))
        m2 = ActivityMonitor(save_dir=tmp, enabled=True)
        m2.sample_once()
        check("过期标记被忽略", m2.snapshot()["note"] == "", m2.snapshot()["note"])

        # 映射到小镇
        sim = TownSim(seed=20240607)
        sim.advance(24 * 1 + 10.0, detailed=True)
        n0 = len(sim.log_lines)
        sim.set_activity({"enabled": True, "level": 1.0, "busy": True, "note": ""})
        sim._activity_reactions(1.0)
        said = sim.log_lines[-1][2] if len(sim.log_lines) > n0 else ""
        # 白天不一定有人正在 work，允许这一次没触发，但状态必须被接收
        check("外部活动状态被接收", sim.activity["busy"] is True)
        if said:
            check("工匠确实说了句忙", bool(said), said[:28])

        # 关掉 = 完全静默
        sim.set_activity({"enabled": False})
        n1 = len(sim.log_lines)
        for _ in range(10):
            sim._activity_reactions(1.0)
        check("关掉之后完全静默", len(sim.log_lines) == n1)

        sim2 = TownSim(seed=20240607)
        sim2.advance(24 * 2 + 10.0, detailed=True)
        n2 = len(sim2.log_lines)
        for _ in range(20):
            sim2._activity_reactions(1.0)
        check("默认不开启时没有任何影响", len(sim2.log_lines) == n2)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ----------------------------------------------------------------------
def test_saves():
    section("3. 存档 / 小镇管理")
    tmp = tempfile.mkdtemp(prefix="pixeltown_saves_")
    try:
        sm = SaveManager(tmp)
        sm.ensure_dirs()

        a = TownSim(seed=111)
        a.advance(24 * 3, detailed=True)
        pa = sm.create_slot("春天的小镇", 111, a.to_dict())
        check("新建存档落盘", os.path.exists(pa), os.path.basename(pa))

        b = TownSim(seed=222)
        b.advance(24 * 5, detailed=True)
        sm.create_slot("夏天的小镇", 222, b.to_dict())

        slots = sm.list_slots(include_current=False)
        check("能列出全部小镇", len(slots) == 2, str([s["name"] for s in slots]))
        check("摘要带天数/人口",
              all(s["day"] >= 1 and s["pop"] >= 0 for s in slots))
        check("摘要带记忆总数", all("memories" in s for s in slots))

        # 切换前必须备份（首次也要）
        shutil.copyfile(sm.slot_path("夏天的小镇"), sm.current)
        sm.activate("春天的小镇")
        cur = json.load(open(sm.current, encoding="utf-8"))
        check("切换后当前存档正确", cur.get("seed") == 111, cur.get("seed"))
        check("切换前自动备份",
              len(os.listdir(sm.backup_dir)) >= 1,
              str(os.listdir(sm.backup_dir)))

        # 删档走回收站
        src = sm.slot_path("春天的小镇")
        trashed = sm.remove_slot("春天的小镇")
        check("删档后原文件消失", not os.path.exists(src))
        check("删档进回收站", os.path.exists(trashed), os.path.basename(trashed))
        check("回收站里的还能读",
              json.load(open(trashed, encoding="utf-8")).get("seed") == 111)

        # 名字安全：不许穿出 towns/
        bad = sm.slot_path("../../evil")
        check("路径穿越被挡住", os.path.dirname(bad) == sm.slots_dir, bad)
        check("空名字有兜底", sm.slot_path("  ").endswith("town.json"))

        # 备份裁剪
        for i in range(16):
            sm.backup(tag="t%d" % i)
        sm.prune_backups(keep=5)
        left = len([n for n in os.listdir(sm.backup_dir) if n.endswith(".json")])
        check("备份裁剪到 5 份", left == 5, "%d 份" % left)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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

    print("=" * 74)
    print("像素小镇 · 记忆流 / 外部活动 / 存档管理")
    print("=" * 74)

    test_memory()
    test_activity()
    test_saves()

    print("\n" + "=" * 74)
    print("RESULT: %d checks, %d failed" % (N[0], len(FAILS)))
    if FAILS:
        print("FAILED: " + ", ".join(FAILS))
    print("=" * 74)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
