# -*- coding: utf-8 -*-
r"""本地大模型连通性与延迟实测

在决定"要不要让村民用大模型说话"之前，必须知道两件事：
  1. 一次调用要多久（决定能不能实时）
  2. 中文短句的质量如何（决定要不要加约束）

用法：
    python tools/test_llm.py                 # 默认测所有对话模型
    python tools/test_llm.py qwen3.5:9b
"""
import json
import sys
import time
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = "http://127.0.0.1:11434"

PERSONA = (
    "你是一个像素小镇里的居民，名叫{name}，职业是{role}。"
    "现在{time_desc}，天气{weather}，季节{season}。"
    "你{doing}。"
    "请用一句不超过 20 个字的中文说出你此刻的感想。"
    "只输出这句话本身，不要引号，不要解释，不要换行。"
)


def post(path, payload, timeout=180):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def chat(model, prompt, system=None, timeout=180, num_predict=64, think=False):
    msgs = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    payload = {
        "model": model,
        "messages": msgs,
        "stream": False,
        "options": {"temperature": 0.9, "num_predict": num_predict},
    }
    if not think:
        payload["think"] = False
    t0 = time.time()
    try:
        out = post("/api/chat", payload, timeout=timeout)
    except urllib.error.HTTPError as e:
        return None, time.time() - t0, "HTTP %s: %s" % (e.code, e.read()[:200])
    except Exception as e:
        return None, time.time() - t0, repr(e)
    return out, time.time() - t0, None


def main():
    models = sys.argv[1:] or ["qwen3.5:9b", "glm-4.7-flash:latest"]

    # 先看服务状态
    req = urllib.request.Request(BASE + "/api/tags")
    with urllib.request.urlopen(req, timeout=5) as r:
        tags = json.loads(r.read().decode())
    have = [m["name"] for m in tags.get("models", [])]
    print("=" * 74)
    print("服务可用模型：%s" % have)
    print("=" * 74)

    for model in models:
        if model not in have:
            print("\n跳过 %s（未安装）" % model)
            continue
        print("\n" + "=" * 74)
        print("模型：%s" % model)
        print("=" * 74)

        # 第一次调用含加载权重，单独计时
        p = PERSONA.format(name="阿禾", role="镇长", time_desc="早上七点",
                           weather="晴", season="春", doing="刚走出家门")
        out, dt, err = chat(model, p, timeout=300)
        if err:
            print("  首调失败：%s" % err)
            continue
        print("  首调（含加载权重）%5.1fs" % dt)
        print("  原始返回键：%s" % sorted(out.keys()))
        print("  内容：%r" % out.get("message", {}).get("content"))
        if out.get("message", {}).get("thinking"):
            print("  ⚠ 有 thinking 字段，长度 %d"
                  % len(out["message"]["thinking"]))
        ev = out.get("eval_count"), out.get("eval_duration")
        if ev[0] and ev[1]:
            print("  生成 %s tokens，%.1f tok/s"
                  % (ev[0], ev[0] / (ev[1] / 1e9)))

        # 预热后再测三次，这才是真实延迟
        cases = [
            ("小满", "农夫", "正午", "多云", "夏", "在田里拔草"),
            ("石头", "店主", "傍晚", "小雨", "秋", "在整理货架"),
            ("豆豆", "孩子", "晚上八点", "晴", "春", "在广场上追萤火虫"),
        ]
        lat = []
        for name, role, td, w, s, doing in cases:
            p = PERSONA.format(name=name, role=role, time_desc=td,
                               weather=w, season=s, doing=doing)
            out, dt, err = chat(model, p)
            lat.append(dt)
            txt = (out or {}).get("message", {}).get("content", "")
            txt = (txt or "").strip().replace("\n", " ")
            print("  %5.2fs  %s（%s）-> %s" % (dt, name, role, txt))

        if lat:
            lat.sort()
            print("  延迟：中位 %.2fs  最快 %.2fs  最慢 %.2fs"
                  % (lat[len(lat) // 2], lat[0], lat[-1]))

        # 一次多说的请求：结构化决策（如果要做"自主决策"就得靠这个）
        print("\n  -- 结构化决策测试 --")
        sysp = ("你是小镇模拟器的决策模块。只输出 JSON，"
                "格式 {\"action\":\"...\",\"target\":\"...\"}，不要任何其他文字。")
        q = ('村民阿禾（镇长）现在空闲。可选行动：'
             'work(去大厅), social(去广场找人聊天), rest(回家休息), '
             'walk(随便走走), eat(去井边吃点东西)。'
             '他精力 62，饱腹 41，心情 70，和「小满」关系是朋友。'
             '请选一个行动。')
        out, dt, err = chat(model, q, system=sysp, num_predict=80)
        if err:
            print("  失败：%s" % err)
        else:
            print("  %5.2fs  -> %r" % (dt, (out.get("message", {})
                                           .get("content") or "").strip()))

    print("\n" + "=" * 74)
    print("结论要点：看中位延迟。低于 2s 就能做实时对话；")
    print("          高于 8s 就只能做每天一次的旁白，不能做逐句对话。")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
