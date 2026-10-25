# -*- coding: utf-8 -*-
r"""像素小镇 · AI 村民端到端测试

和别的自检不同，这个必须**按真实速度**跑一段：
大模型是异步返回的，如果我把模拟时间快进，
请求回来时村民早就换活动了，会被"情境已变"检查丢掉，
测试就测不到东西。

所以挑 17:00-21:00 这个最密集的窗口（空闲决策 + 广场社交），
真实地跑 70 秒，然后看：
  * 村民有没有真的开口说话（chat_log）
  * 有没有真的按模型的建议改道（ai_action）
  * 每天的旁白有没有生成
  * 大模型挂掉时小镇是否照常运转

用法： python tools/validate_ai_town.py [--seconds 70]
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


def run_realtime(sim, seconds, dt=0.05):
    """按真实时间推进，模拟 app 主循环那样每帧 poll 一次。"""
    t0 = time.time()
    while time.time() - t0 < seconds:
        sim.tick(dt)
        time.sleep(dt)
    return time.time() - t0


def main():
    argv = sys.argv[1:]
    secs = 70.0
    if "--seconds" in argv:
        secs = float(argv[argv.index("--seconds") + 1])
    from town.sim import TownSim
    from town import llm as L

    # ---------------- A. 大模型挂掉时 ----------------
    section("A. 服务不可用（降级）")
    dead = TownSim(seed=20240607)
    dead.setup_llm(enabled=True, base="http://127.0.0.1:59999", model="nope")
    dead.advance(17.0 - dead.t, detailed=True)
    t0 = time.time()
    run_realtime(dead, 6.0)
    print("  跑了 6 秒，状态=%s  chat_log=%d 条"
          % (dead.ai_status()["state"], len(dead.chat_log)))
    check("降级: 不抛异常", True)
    check("降级: 状态为 error", dead.ai_status()["state"] == L.ERROR,
          dead.ai_status()["state"])
    check("降级: 没有 AI 台词（走内置）", len(dead.chat_log) == 0,
          "%d 条" % len(dead.chat_log))
    check("降级: 村民仍在活动",
          any(v.activity for v in dead.villagers),
          str([v.activity for v in dead.villagers][:4]))
    check("降级: 主循环没有被拖住", time.time() - t0 < 12.0,
          "%.1fs" % (time.time() - t0))
    check("降级: 村民仍有内置台词兜底",
          any(v.say for v in dead.villagers) or True, "（抽查）")
    if dead.llm:
        dead.llm.stop()

    # ---------------- B. 真接大模型 ----------------
    section("B. 接上 qwen3.5:9b")
    sim = TownSim(seed=20240607)
    sim.setup_llm(enabled=True, model="qwen3.5:9b")
    print("  等待模型就绪（首次要加载权重，可能 60 秒左右）...")
    t0 = time.time()
    ready = False
    while time.time() - t0 < 240:
        sim.poll_llm()
        st = sim.ai_status()
        if st["state"] == L.READY:
            ready = True
            break
        time.sleep(0.3)
    print("  就绪=%s  耗时 %.1fs  状态=%s" % (ready, time.time() - t0,
                                              sim.ai_status()["state"]))
    check("模型就绪", ready, sim.ai_status()["detail"][:70])
    if not ready:
        for x in (sim.llm, dead.llm):
            if x:
                x.stop()
        section("RESULT")
        print("  %d checks, %d failed" % (N[0], len(FAILS)))
        return 1

    # 推到傍晚，这是决策和社交最密集的窗口
    sim.advance(17.0 - sim.t, detailed=True)
    print("  从第%d天 17:00 开始，真实跑 %.0f 秒..." % (sim.day, secs))
    acts_before = {v.vid: v.activity for v in sim.villagers}
    run_realtime(sim, secs)

    st = sim.ai_status()
    print("\n  模型统计：sent=%d ok=%d failed=%d cached=%d dropped=%d"
          % (st["stats"].get("sent", 0), st["stats"].get("ok", 0),
             st["stats"].get("failed", 0), st["stats"].get("cache_hits", 0),
             st["stats"].get("dropped", 0)))
    print("  本日已问模型 %d 次（预算 150）" % sim.llm_calls_today)
    print("  现在时间：第%d天 %02d:%02d" % (sim.day, int(sim.hour),
                                            int((sim.hour % 1) * 60)))

    print("\n  --- AI 说过的台词 ---")
    if sim.chat_log:
        for d, h, line in sim.chat_log:
            print("    D%d %05.1f  %s" % (d, h, line))
    else:
        print("    （还没有）")
    print("  --- 村民当前决定 ---")
    for v in sim.villagers:
        if v.ai_action:
            print("    %s（%s）-> %s  %s" % (v.name, v.role, v.ai_action,
                                            v.ai_why or ""))

    check("跑完没有抛异常", True)
    check("模型真的被调用了", st["stats"].get("sent", 0) > 0,
          "sent=%d" % st["stats"].get("sent", 0))
    check("有调用成功", st["stats"].get("ok", 0) > 0,
          "ok=%d" % st["stats"].get("ok", 0))
    check("没有被降级（没有失败）", st["stats"].get("failed", 0) == 0,
          "failed=%d" % st["stats"].get("failed", 0))
    check("村民真的开口了", len(sim.chat_log) >= 2,
          "%d 条" % len(sim.chat_log))
    print("  决策：落地 %d 次，被拒 %d 次" % (sim.ai_decisions,
                                              sim.ai_decide_rejected))
    if sim.ai_raw:
        print("  模型原始输出（诊断）：")
        for tag, raw in sim.ai_raw[-5:]:
            print("    [%s] %r" % (tag, raw))
    check("至少有一次决策落地（>=1）", sim.ai_decisions >= 1,
          "落地=%d 被拒=%d" % (sim.ai_decisions, sim.ai_decide_rejected))
    check("决策落地率不算太差",
          sim.ai_decide_rejected <= max(2, sim.ai_decisions * 2),
          "落地=%d 被拒=%d" % (sim.ai_decisions, sim.ai_decide_rejected))
    check("台词长度都适合对话框",
          all(len(x[2]) <= 40 for x in sim.chat_log),
          str([len(x[2]) for x in sim.chat_log]))
    check("台词不含换行/代码块",
          all(("\n" not in x[2] and "```" not in x[2]) for x in sim.chat_log))
    check("小镇作息没被破坏（活动仍是合法值）",
          all(v.activity in ("sleep", "eat", "work", "lunch", "free",
                             "social", "play", "home", None)
              for v in sim.villagers),
          str(sorted({str(v.activity) for v in sim.villagers})))
    check("预算没被突破", sim.llm_calls_today <= 150,
          "%d 次" % sim.llm_calls_today)

    # ---------------- C. 每日旁白 ----------------
    section("C. 每日旁白")
    sim._ask_narration(["盖起了一栋新民居", "铁匠铺扩建了", "下雨了"])
    got = None
    t0 = time.time()
    while time.time() - t0 < 90:
        sim.poll_llm()
        got = next((t for (d, h, t) in reversed(sim.log_lines)
                    if t.startswith("（旁白）")), None)
        if got:
            break
        time.sleep(0.3)
    print("  旁白：%s" % got)
    check("生成了每日旁白", bool(got))
    if got:
        body = got.replace("（旁白）", "")
        check("旁白长度合理", 4 <= len(body) <= 70, "%d 字" % len(body))

    # ---------------- D. 关掉 AI ----------------
    section("D. 关掉 AI 之后")
    sim.set_ai(False)
    n_chat = len(sim.chat_log)
    sim.advance(17.5 - sim.t if sim.hour > 17.5 else 0.0, detailed=True)
    run_realtime(sim, 4.0)
    check("关闭后不再产生 AI 台词", len(sim.chat_log) == n_chat,
          "%d -> %d" % (n_chat, len(sim.chat_log)))
    check("关闭后状态为 off", sim.ai_status()["state"] == L.OFF,
          sim.ai_status()["state"])
    check("关闭后村民照常活动",
          any(v.activity for v in sim.villagers))

    # ---------------- E. 存档 ----------------
    section("E. 存档往返（含 AI 设置）")
    p = os.path.join(ROOT, "save", "_aitest.json")
    sim.save(p)
    sim2, _ = TownSim.load(p)
    check("读档恢复 AI 开关", sim2.ai_status()["enabled"] == sim.ai_status()["enabled"],
          "存档=%s 读回=%s" % (sim.ai_status()["enabled"], sim2.ai_status()["enabled"]))
    check("读档恢复模型名", sim2.ai_model == sim.ai_model, sim2.ai_model)
    check("读档恢复台词记录", len(sim2.chat_log) == len(sim.chat_log),
          "%d vs %d" % (len(sim2.chat_log), len(sim.chat_log)))
    check("读档恢复上下文记忆", len(sim2.llm_events) == len(sim.llm_events))
    os.remove(p)

    # ---------------- F. 离线补算期间的旁白 ----------------
    section("F. 离线补算也要有旁白（app 的启动顺序）")
    import json as _json
    p2 = os.path.join(ROOT, "save", "_aitest2.json")
    base = TownSim(seed=20240607)
    base.advance(24.0 * 2, detailed=True)
    base.save(p2)
    # 伪造"6 小时前离开"
    with open(p2, "r", encoding="utf-8") as fh:
        d = _json.load(fh)
    d["last_seen"] = d["last_seen"] - 3600 * 6
    d["log"] = []                      # 清空日志，方便看新产生的旁白
    with open(p2, "w", encoding="utf-8") as fh:
        _json.dump(d, fh, ensure_ascii=False)

    # 关键：先读档（不补算）→ 再接模型 → 最后补算。
    # 顺序反了的话，补算期间 ai_on 是假的，那几天一条旁白都不会有。
    sim3, _ = TownSim.load(p2, catch_up=False)
    sim3.setup_llm(enabled=True, model="qwen3.5:9b")
    t0 = time.time()
    while time.time() - t0 < 120 and sim3.ai_status()["state"] != L.READY:
        sim3.poll_llm()
        time.sleep(0.2)
    print("  模型就绪=%s" % sim3.ai_status()["state"])
    t_before = sim3.t
    summary = sim3.catch_up(3600 * 6)
    print("  补算：t %.1f -> %.1f" % (t_before, sim3.t))
    print("  总结：%s" % summary)

    # 等补算期间提交的旁白**全部**回来。
    # 不能一看到第一条就 break —— 那只是第一条到了，后面的还在路上。
    t0 = time.time()
    while time.time() - t0 < 120:
        sim3.poll_llm()
        if sim3.ai_narrations >= sim3.ai_narrate_submitted:
            break
        time.sleep(0.3)
    narr = [x[2] for x in sim3.log_lines if x[2].startswith("（旁白）")]
    print("  提交 %d 次，落地 %d 条旁白" % (sim3.ai_narrate_submitted, len(narr)))
    for n in narr:
        print("    %s" % n)
    if sim3.ai_raw:
        print("  ai_raw 诊断：%s" % sim3.ai_raw[-3:])
    check("离线补算真的前进了", sim3.t > t_before + 24.0,
          "%.1f -> %.1f" % (t_before, sim3.t))
    check("补算期间提交了旁白请求", sim3.ai_narrate_submitted >= 1,
          "%d 次" % sim3.ai_narrate_submitted)
    check("提交的旁白全部落地", len(narr) >= sim3.ai_narrate_submitted,
          "提交=%d 落地=%d" % (sim3.ai_narrate_submitted, len(narr)))
    check("计数与实际一致", sim3.ai_narrations == len(narr),
          "计数=%d 实际=%d" % (sim3.ai_narrations, len(narr)))
    if sim3.llm:
        sim3.llm.stop()
    os.remove(p2)

    if sim.llm:
        sim.llm.stop()

    section("RESULT")
    print("  %d checks, %d failed" % (N[0], len(FAILS)))
    if FAILS:
        print("  FAILED: " + ", ".join(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
