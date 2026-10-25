# -*- coding: utf-8 -*-
r"""本地大模型接入自检

分四块：
  1. 输出清洗 —— 拿一堆模型真实会吐的脏输出喂进去
  2. JSON 决策解析 —— 必须拒绝不在允许集合里的行动
  3. 真连 Ollama —— 提交若干请求，看能否按时收回来
  4. 降级 —— 指向一个死端口，必须安静失败而不是卡住或抛异常

用法： python tools/validate_llm.py [--live]
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
    live = "--live" in sys.argv
    from town import llm as L

    # ---------------- 1. 输出清洗 ----------------
    section("1. SANITIZE 输出清洗")
    cases = [
        ('"春光正好，我该去田里看看了。"', True, "去引号"),
        ("好的，今天天气真不错。", True, "去客套前缀"),
        ("```\n今天真安静\n```", True, "去代码块"),
        ("今天\n真安静", True, "压掉换行"),
        ("「小镇的早晨真好。」", True, "去中文引号"),
        ("作为一个人工智能，我无法回答。", None, "应当被拒或截断"),
        ("", False, "空串"),
        (None, False, "None"),
        ("Hello world this is English", False, "没有中文必须拒"),
        ("ok", False, "太短必须拒"),
    ]
    for raw, want_ok, why in cases:
        out = L.sanitize_line(raw)
        ok = (out is not None) if want_ok is not False else (out is None)
        if want_ok is None:
            ok = True                      # 这类只观察，不判定
        check("sanitize: %s" % why, ok, "输入=%r -> %r" % (raw, out))
    long_out = L.sanitize_line("这是一句特别特别特别特别特别特别特别特别长的句子用来测试截断行为")
    check("sanitize: 超长会被截断", long_out is not None and len(long_out) <= 26,
          "%r (len=%d)" % (long_out, len(long_out) if long_out else -1))

    # ---------------- 2. 决策解析 ----------------
    section("2. PARSE_DECISION JSON 解析")
    allowed = {"work", "social", "rest", "walk", "eat"}
    dcases = [
        ('{"action":"social","why":"想找人聊聊"}', "social", True),
        ('```json\n{"action":"work","why":"该干活了"}\n```', "work", True),
        ('当然可以！{"action":"rest","why":"累了"}', "rest", True),
        ('{"action":"fly","why":"想飞"}', None, False),       # 不在集合里
        ('{"action":"work"}', "work", True),
        ('我不知道该选什么', None, False),
        ('', None, False),
        ('{"action": "SOCIAL", "why":"x"}', "social", True),   # 大小写应归一
    ]
    for raw, want, _ in dcases:
        got, why = L.parse_decision(raw, allowed)
        check("decision: %r" % (raw[:34],), got == want, "-> %r (%r)" % (got, why))

    # ---------------- 3. 说话风格 ----------------
    section("3. STYLES 说话风格")
    import random as _rnd
    keys = [s["key"] for s in L.STYLES]
    check("风格 key 唯一", len(keys) == len(set(keys)), "%d 个" % len(keys))
    check("至少有 6 种风格", len(L.STYLES) >= 6, "%d 种" % len(L.STYLES))
    check("每种风格都有 hint",
          all(s.get("hint") and len(s["hint"]) > 4 for s in L.STYLES))
    check("存在正经档风格", any(s["goofy"] == 0 for s in L.STYLES))
    check("搞怪风格不少于 4 种",
          sum(1 for s in L.STYLES if s["goofy"] >= 2) >= 4,
          "%d 种" % sum(1 for s in L.STYLES if s["goofy"] >= 2))

    # 乱序这类必须给例子，否则模型只会写正常句子（实测过）
    need_examples = {"scramble", "deadpan", "kid", "epic", "philo", "mishear"}
    for s in L.STYLES:
        if s["key"] in need_examples:
            check("风格「%s」带了例子" % s["name"], len(s.get("examples") or []) >= 2,
                  "%d 条" % len(s.get("examples") or []))

    # 档位过滤
    rng = _rnd.Random(1)
    lv0 = {L.pick_style(rng, 0)["name"] for _ in range(300)}
    check("档位0 只会出现正经风格", lv0 == {"平常"}, str(lv0))
    lv2 = {L.pick_style(rng, 2)["name"] for _ in range(3000)}
    check("档位2 会用到所有风格", len(lv2) == len(L.STYLES),
          "%d/%d 种" % (len(lv2), len(L.STYLES)))

    # 拼出来的 system prompt 有几个硬要求
    for s in L.STYLES:
        sp = L.talk_system(s)
        ok_nl = (not s.get("examples")) or ("\n" in sp)
        check("「%s」的 prompt 换行正确" % s["name"], ok_nl)
        check("「%s」的 prompt 不含 markdown 星号" % s["name"], "*" not in sp,
              repr(sp[:40]))
        check("「%s」的 prompt 禁止客服腔" % s["name"],
              "客套话" in sp or "自己" in sp)

    nst = {k for k, w, h in L.NARRATE_STYLES}
    check("旁白有多种风格", len(nst) >= 2, str(nst))
    check("档位0 的旁白只有正经版",
          L.pick_narrate_style(_rnd.Random(2), 0)[0] == "chronicle")

    # ---------------- 4. 真连 Ollama ----------------
    section("4. LIVE 真连 Ollama")
    c = L.LLMClient(enabled=True, model="qwen3.5:9b", min_interval=0.0,
                    timeout=300)
    print("  提交 4 个请求（首个含加载权重，可能很久）...")
    t0 = time.time()
    n_queued = 0
    prompts = [
        ("t1", "你叫阿禾，是镇长。现在是春天早上七点，天气晴。你刚走出家门。"
               "请用不超过 18 个字说一句你现在想说的话。"),
        ("t2", "你叫小满，是农夫。正午，多云，夏天。你在田里拔草。"
               "请用不超过 18 个字说一句你现在想说的话。"),
        ("t3", "你叫石头，是店主。傍晚，小雨，秋天。你在整理货架。"
               "请用不超过 18 个字说一句你现在想说的话。"),
        ("t4", "你叫豆豆，是孩子。晚上八点，晴，春天。你在广场上玩。"
               "请用不超过 18 个字说一句你现在想说的话。"),
    ]
    for k, p in prompts:
        if c.submit("say", p, meta={"k": k}, system=L.TALK_SYSTEM.format(limit=18)):
            n_queued += 1
    check("subscribe: 全部进队", n_queued == 4, "queued=%d" % n_queued)

    got = {}
    deadline = time.time() + 300
    while len(got) < 4 and time.time() < deadline:
        for r in c.drain():
            got[r["meta"]["k"]] = r["text"]
        time.sleep(0.2)
    print("  用时 %.1fs  状态=%s" % (time.time() - t0, c.status()["state"]))
    for k in ("t1", "t2", "t3", "t4"):
        clean = L.sanitize_line(got.get(k))
        print("    %s 原始=%r" % (k, (got.get(k) or "")[:60]))
        print("       清洗=%r" % (clean,))
    check("live: 4 个请求全部返回", len(got) == 4, "got=%d" % len(got))
    check("live: 状态变成 ready", c.status()["state"] == L.READY,
          c.status()["state"])
    check("live: 至少 3 条能过清洗",
          sum(1 for k in got if L.sanitize_line(got[k])) >= 3,
          str([L.sanitize_line(got.get(k)) for k in sorted(got)]))

    print("\n  决策请求：")
    okd = c.submit("decide",
                   L.decide_prompt(_FakeSim(), _FakeV(), list(allowed)),
                   meta={"k": "d1"}, system=L.DECIDE_SYSTEM, num_predict=80)
    check("decide: 进队成功", okd)
    dec = None
    deadline = time.time() + 120
    while dec is None and time.time() < deadline:
        for r in c.drain():
            if r["meta"].get("k") == "d1":
                dec = r["text"]
        time.sleep(0.2)
    action, why = L.parse_decision(dec, allowed)
    print("    原始=%r" % (dec or "")[:80])
    print("    解析 action=%r why=%r" % (action, why))
    check("decide: 解析出合法行动", action in allowed, "%r" % (action,))

    # ---------------- 5. 降级 ----------------
    section("5. DEGRADE 服务不可用时的表现")
    dead = L.LLMClient(base_url="http://127.0.0.1:59999", model="nope",
                       enabled=True, min_interval=0.0, timeout=3)
    t0 = time.time()
    q = dead.submit("say", "测试", meta={"k": "x"})
    time.sleep(2.5)
    drained = dead.drain()
    dt = time.time() - t0
    st = dead.status()
    print("  提交返回=%s  状态=%s  detail=%s  收到=%d"
          % (q, st["state"], st["detail"][:60], len(drained)))
    check("degrade: 提交不会抛异常", q in (True, False))
    check("degrade: 状态进入 error", st["state"] == L.ERROR, st["state"])
    check("degrade: 没有结果返回（调用方走内置台词）", len(drained) == 0)
    check("degrade: 失败后进入退避，不再无脑重试",
          dead.submit("say", "再试", meta={}) is False, "退避生效")
    check("degrade: 整个过程没卡住主线程", dt < 6.0, "%.1fs" % dt)

    # disabled 状态
    off = L.LLMClient(enabled=False)
    check("degrade: 关闭时不提交", off.submit("say", "x", meta={}) is False)
    check("degrade: 关闭时状态是 off", off.status()["state"] == L.OFF)

    # 队列满时丢旧请求
    tiny = L.LLMClient(enabled=True, max_queue=1, base_url="http://127.0.0.1:59999",
                       min_interval=0.0, timeout=2)
    tiny.submit("say", "a", meta={})
    tiny.submit("say", "b", meta={})
    check("degrade: 队列满时丢弃而不是堆积",
          tiny.status()["stats"]["dropped"] >= 1,
          "dropped=%d" % tiny.status()["stats"]["dropped"])

    for cl in (c, dead, off, tiny):
        cl.stop()

    section("RESULT")
    print("  %d checks, %d failed" % (N[0], len(FAILS)))
    if FAILS:
        print("  FAILED: " + ", ".join(FAILS))
    return 1 if FAILS else 0


class _FakeV(object):
    name = "阿禾"
    role = "镇长"
    activity = "free"
    needs = {"energy": 62, "hunger": 41, "mood": 70}
    x = 100
    y = 100
    hidden = False
    vid = 0
    rel = {}

    def rel_level(self, o):
        return "朋友"


class _FakeSim(object):
    hour = 18.0
    day = 3
    season = 0
    weather = "clear"
    llm_events = []
    villagers = []
    villagers_by_id = {}


if __name__ == "__main__":
    sys.exit(main())
