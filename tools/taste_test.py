# -*- coding: utf-8 -*-
r"""像素小镇 · 台词试吃

把每种说话风格各采样几条打出来，直接看效果。
调风格的时候用这个，比反复启动小镇快得多。

用法：
    python tools/taste_test.py                 # 每种风格 3 条
    python tools/taste_test.py --per 5         # 每种 5 条
    python tools/taste_test.py --goofy 1       # 只看温和档
    python tools/taste_test.py --style scramble  # 只看某一种
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


def main():
    argv = sys.argv[1:]
    per = 3
    goofy = 2
    only = None
    if "--per" in argv:
        per = int(argv[argv.index("--per") + 1])
    if "--goofy" in argv:
        goofy = int(argv[argv.index("--goofy") + 1])
    if "--style" in argv:
        only = argv[argv.index("--style") + 1]

    from town import llm as L
    from town.sim import TownSim

    # 用一个真实的小镇状态来生成 persona，台词才有具体情境
    sim = TownSim(seed=20240607)
    sim.advance(24 * 4 + 9.0 - sim.t, detailed=True)      # 第 5 天上午 9 点
    sim.setup_llm(enabled=True, model="qwen3.5:9b")
    print("等待模型就绪…")
    t0 = time.time()
    while time.time() - t0 < 240 and sim.ai_status()["state"] != L.READY:
        sim.poll_llm()
        time.sleep(0.3)
    print("状态=%s  耗时 %.0fs\n" % (sim.ai_status()["state"], time.time() - t0))

    styles = L.STYLES if not only else [L.STYLES_BY_KEY[only]]
    styles = [s for s in styles if s["goofy"] <= goofy]
    vis = [v for v in sim.villagers if not v.hidden]
    if not vis:
        sim.advance(2.0)
        vis = [v for v in sim.villagers if not v.hidden]

    # 按 meta 里的令牌精确取回自己那条结果。
    # 不能"drain 到什么就用什么" —— setup_llm 会发一条 warmup（prompt 是"你好"），
    # 它还在飞行中，会被当成第一条结果，于是每种风格的第一句都变成客服腔。
    _tok = [0]

    def ask(kind, prompt, system, num_predict=64, timeout=90):
        _tok[0] += 1
        tok = _tok[0]
        sim.llm._q.queue.clear()
        if not sim.llm.submit(kind, prompt, meta={"tok": tok},
                              system=system, num_predict=num_predict):
            return None
        t1 = time.time()
        while time.time() - t1 < timeout:
            for r in sim.llm.drain():
                if (r.get("meta") or {}).get("tok") == tok:
                    return r["text"]
            time.sleep(0.1)
        return None

    print("=" * 78)
    print("村民台词   搞怪档位=%d   每种 %d 条" % (goofy, per))
    print("=" * 78)
    total = 0
    for si, style in enumerate(styles):
        print("\n【%s】  %s" % (style["name"], style["hint"]))
        seen = []
        for i in range(per):
            v = vis[(si + i) % len(vis)]
            got = ask("say", L.villager_persona(sim, v, rng=sim.rng),
                      L.talk_system(style))
            line = L.sanitize_line(got, 24)
            flag = ""
            if line is None:
                flag = "  ← 被清洗拒绝"
            elif line in seen:
                flag = "  ← 与前面重复"
            if line:
                seen.append(line)
            print("   %-4s %s%s" % (v.name, line if line else repr(got), flag))
            total += 1

    # 旁白
    if not only:
        print("\n" + "=" * 78)
        print("每日旁白")
        print("=" * 78)
        demo = ["盖起了一栋新民居", "铁匠铺扩建了，远远就能看见",
                "下雨了，大家都在屋檐下躲", "小满把地里的庄稼收了一茬"]
        for key, w, hint in L.NARRATE_STYLES:
            if goofy == 0 and key != "chronicle":
                continue
            got = ask("narrate", L.narrate_prompt(sim, demo),
                      L.narrate_system(hint, goofy), num_predict=120)
            print("  【%s】%s" % (key, L.sanitize_line(got, 60)))
            total += 1

    # 决策台词（会显示成对话框里的那句话）
    if not only:
        print("\n" + "=" * 78)
        print("决策时说的话（会冒成对话框）")
        print("=" * 78)
        for i in range(per):
            v = vis[i % len(vis)]
            opts = L.free_options(v.role)
            got = ask("decide", L.decide_prompt(sim, v, opts),
                      L.DECIDE_SYSTEM, num_predict=80)
            action, why = L.parse_decision(got, set(L.DECISION_OPTIONS),
                                           L.DECISION_OPTIONS)
            print("  %-4s（%s）-> %-8s %s" % (v.name, v.role, action, why))
            total += 1

    if sim.llm:
        sim.llm.stop()
    print("\n共采样 %d 条" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
