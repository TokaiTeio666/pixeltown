# -*- coding: utf-8 -*-
"""像素小镇 · 本地大模型接入

设计上的死规矩：**绝不能阻塞渲染循环**。
首次调用要加载权重（实测 qwen3.5:9b 约 59 秒），
这东西要是跑在主线程上，小镇会直接卡死 59 秒。

所以这里是一个后台工作线程 + 双向队列：
  submit() 把请求丢进队列就立刻返回，主循环一帧都不会停
  drain()  主循环每帧来收一次结果，收到就应用到村民身上

其他几条也必须做到：
  * 模型不在 / 端口没开 → 静默降级，村民改说内置台词，绝不报错弹窗
  * 排队满了就丢掉旧请求（过期的对话毫无价值，不如不做）
  * 相同情境缓存，避免同一句话反复问模型
  * 单次串行 + 最小间隔，别把 GPU 占满
"""

import hashlib
import json
import queue
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict

DEFAULT_BASE = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3.5:9b"

# 一次调用的状态
OFF, LOADING, READY, ERROR = "off", "loading", "ready", "error"


# ----------------------------------------------------------------------
# 提示词构造
# ----------------------------------------------------------------------
ACT_WORDS = {
    "sleep": "正在家里睡觉",
    "eat": "刚起床，在家里吃早饭",
    "work": "正在干活",
    "lunch": "正在井边吃午饭",
    "free": "空闲着，随便走走",
    "social": "在广场上和邻居们待在一起",
    "festival": "在广场上过节，手上的活都停了",
    "play": "在镇上到处玩",
    "home": "正在回家",
}

WEATHER_CN = {"clear": "晴", "cloudy": "多云", "rain": "下雨", "snow": "下雪"}
SEASON_CN = {0: "春", 1: "夏", 2: "秋", 3: "冬"}

# ----------------------------------------------------------------------
# 说话风格
#
# 为什么需要一整张表而不是一句"说得好笑点"：
# 只给一句笼统的指令，9B 模型会连续吐出同一类句子，看两天就腻了。
# 拆成互不相同的具体手法、每次随机挑一种，才能长期不重复。
#
# goofy 是搞怪档位：0=正经，1=温和，2=放开。
# 乱序那类必须给例子 —— 不给例子模型只会写正常句子。
# ----------------------------------------------------------------------
STYLES = [
    {
        "key": "plain", "name": "平常", "goofy": 0, "weight": 4,
        "hint": "用平常的语气，像随口说的一句话。",
        "examples": [],
    },
    {
        "key": "scramble", "name": "语序颠倒", "goofy": 2, "weight": 3,
        "hint": "故意把词序打乱，但意思还大致猜得出来，错得有点可爱。"
                "做法：把相邻的词两两对调，比如「蓝天」写成「蓝真天」、"
                "「吃饭」写成「饭吃」、「要睡觉了」写成「睡要觉了」。"
                "只打乱语序，不要写成一堆读不懂的词。",
        "examples": ["这蓝真天，白真云。",
                     "我饭吃得今天很饱。",
                     "睡要觉了，眼睛在打架。"],
    },
    {
        "key": "deadpan", "name": "一本正经说废话", "goofy": 2, "weight": 3,
        "hint": "用非常认真的语气，说一句其实毫无意义的话。",
        "examples": ["只要我不动，时间就还在走。", "水是湿的，这点我确认过。"],
    },
    {
        "key": "kid", "name": "童言无忌", "goofy": 2, "weight": 2,
        "hint": "像小孩一样突然说出一句出人意料、逻辑清奇的话。",
        "examples": ["云是不是天上掉的棉花糖化了一半。",
                     "我把石头排成一排，它们就不孤单了。"],
    },
    {
        "key": "epic", "name": "小题大做", "goofy": 2, "weight": 2,
        "hint": "把一件特别小的事，说得像天大的事一样。",
        "examples": ["这一锄头下去，整个春天都得抖一抖。",
                     "我要是挪一步，这条街的格局就变了。"],
    },
    {
        "key": "roast", "name": "自我吐槽", "goofy": 1, "weight": 2,
        "hint": "吐槽自己刚才干的事，带一点自嘲。",
        "examples": ["我这一天忙的，也没见忙出个啥。",
                     "刚才那下我肯定看起来很傻。"],
    },
    {
        "key": "sfx", "name": "拟声词", "goofy": 1, "weight": 2,
        "hint": "句子开头带一个拟声词，让画面有点声音。",
        "examples": ["咕噜——肚子比我先表态了。", "啪嗒，我的耐心掉地上了。"],
    },
    {
        "key": "philo", "name": "突然哲学", "goofy": 2, "weight": 1,
        "hint": "突然感慨一句，听着挺深奥，其实挺傻的。",
        "examples": ["井里的水照不出我的前途，只能照出我的脸。",
                     "路走到头是海，海走到头是什么呢。"],
    },
    {
        "key": "rhyme", "name": "强行押韵", "goofy": 1, "weight": 1,
        "hint": "句子尽量押韵，哪怕为了押韵有点不讲道理。",
        "examples": ["天不黑我不回，活没干完心里亏。",
                     "风一吹草一动，我就想再歇五分钟。"],
    },
    {
        "key": "mishear", "name": "答非所问", "goofy": 2, "weight": 1,
        "hint": "像完全没听清别人在说什么，回一句毫不相干、但自己很认真的话。",
        "examples": ["嗯？你说井啊，井挺好的，我昨天还喝过。",
                     "吃饭？我一般用嘴。"],
    },
]

STYLES_BY_KEY = {s["key"]: s for s in STYLES}


def pick_style(rng, goofy=2):
    """按搞怪档位抽一种风格。档位越高，越容易抽到放得开的。"""
    pool = []
    for s in STYLES:
        if s["goofy"] <= goofy:
            pool.extend([s] * max(1, s["weight"] - s["goofy"]))
    if not pool:
        pool = [STYLES_BY_KEY["plain"]]
    return rng.choice(pool)


def talk_system(style, limit=18):
    """把风格指令拼成系统提示。

    两个已经踩过的坑：
      * 例子之间必须有换行，糊成一整行模型会当成一句乱码
      * 不能出现星号等 markdown 记号 —— 模型会照着抄进台词里
      * 必须写明"不要照抄"，否则它会反复复用同一句例子
    """
    lines = [
        "你在扮演一个像素小镇里的居民。",
        # 不写这句，模型会滑进客服模式，张口就是"你好，有什么可以帮你的吗"
        "你是在自言自语，或者在跟镇上的邻居搭话 —— 不是在回答一个用户。",
        "绝对不要说「有什么可以帮你的」「请问」这类客套话。",
        "不要把自己的名字念出来，没人会对着空气喊自己。",
        style["hint"],
    ]
    if style.get("examples"):
        lines.append("参考这种味道（不要照抄，要自己想新的）：")
        for e in style["examples"]:
            lines.append("  " + e)
    lines.append("你的回答必须是一句不超过 %d 个字的中文口语。" % limit)
    lines.append("禁止使用引号、星号、括号、表情符号、换行。"
                 "只输出这句话本身，不要任何解释或前缀。")
    return "\n".join(lines)


TALK_SYSTEM = talk_system(STYLES_BY_KEY["plain"])

# 旁白也分档：正经记录 / 有点好笑的编年史
NARRATE_STYLES = [
    ("chronicle", 1, "用温和、平实的中文把这一天记下来，像小镇的编年史。"),
    ("wry", 3, "用温和但有点好笑的语气记这一天，偶尔加一句不着调的感慨，"
               "像一个看热闹不嫌事大的旁观者。"),
    ("cozy", 2, "用很温柔的语气记这一天，带一点生活气，像睡前随口说的。"),
]


def pick_narrate_style(rng, goofy=2):
    pool = []
    for key, w, hint in NARRATE_STYLES:
        if goofy >= 1 or key == "chronicle":
            pool.extend([(key, hint)] * w)
    if goofy == 0:
        pool = [("chronicle", NARRATE_STYLES[0][2])]
    return rng.choice(pool)


def narrate_system(hint, goofy=2):
    if goofy == 0:
        return ("你在为一款像素小镇游戏写每日旁白。" + hint +
                "用一到两句话，总共不超过 42 个字。"
                "不要用引号、不要用列表、不要加标题、不要换行。"
                "只输出这段话本身。")
    return ("你在为一款治愈系像素小镇游戏写每日旁白。" + hint +
            "用一到两句话，总共不超过 42 个字。"
            "可以有一点点俏皮，但别刻意搞笑、别用网络梗。"
            "不要复述事件细节，抓一两个画面就够了。"
            "不要用引号、不要用列表、不要加标题、不要换行。"
            "只输出这段话本身。")


NARRATE_SYSTEM = narrate_system(NARRATE_STYLES[0][2], 0)

DECIDE_SYSTEM = (
    "你是像素小镇模拟器的行为决策模块。"
    "你只能从给定的选项里挑一个，只输出一行 JSON，"
    "格式 {\"action\":\"选项名\",\"why\":\"他自己会说的一句口语\"}。"
    "why 要用村民本人的口气，不超过 10 个字，像随口说的，不要书面语。"
    "不要输出任何其他文字、不要 markdown 代码块。"
)

NARRATE_SYSTEM = (
    "你在为一款治愈系像素小镇游戏写每日旁白。"
    "用一到两句平实、温和的中文描述这一天，不超过 50 个字。"
    "不要用引号、不要用列表、不要加标题、不要换行。"
    "只输出这段话本身。"
)

DECISION_OPTIONS = {
    "work": "回岗位干活",
    "social": "去广场找人聊天",
    "rest": "回家歇着",
    "eat": "去井边吃点东西",
    "walk": "在镇上随便走走",
    "farm": "去田里看看庄稼",
    "dock": "去码头看看水",
    "play": "去镇上玩",
}


def free_options(role):
    """按职业收窄可选行动。

    不收窄的话会出现"铁匠跑去种地"、"店主去田里看看庄稼"这种离谱选择 ——
    实测确实发生了，而且有三个人同时选了同一个，因为提示词几乎一样。
    """
    opts = ["walk", "social", "rest", "eat"]
    if role == "农夫":
        opts.append("farm")
    elif role == "渔夫":
        opts.append("dock")
    elif role == "孩子":
        opts.append("play")
    return opts


def rel_of(v, other_name):
    return v.rel.get(other_name)


def _body_feeling(v):
    """把需求数值翻译成"体感"，而且只在**明显**时才提。

    这里踩过两次坑：
      * 直接写「饱腹41」→ 模型抓住数字不放，连着好几条都在说肚子饿
      * 改成"有点空"仍不够 → 阈值太松，依然是每句都饿
    所以只在相当极端时才提，而且尽量少提。
    """
    out = []
    h, e, m = v.needs["hunger"], v.needs["energy"], v.needs["mood"]
    if h < 26:
        out.append("饿得发慌")
    elif h > 80:
        out.append("刚吃饱，很满足")
    if e < 24:
        out.append("累得快站不住")
    elif e > 88:
        out.append("精神头很足")
    if m < 30:
        out.append("心里闷闷的")
    elif m > 85:
        out.append("心情特别好")
    return "，".join(out)


# 此刻注意到的小细节。
# 这是提高台词多样性的关键：模型需要一个"具体的抓手"，
# 否则它会反复回到最容易抓的那点（身体感受）上。
MOMENTS = [
    "你刚打了个哈欠。", "路边有只蚂蚁在搬东西。", "风里有股青草味。",
    "井边的水声很响。", "有片叶子一直在你眼前打转。", "你的鞋上沾了点泥。",
    "远处传来谁家的锅铲声。", "你今天已经走了不少路。",
    "台阶上晒着一排东西。", "有只鸟刚才从你头顶飞过去了。",
    "你看见自己在地上的影子。", "有只麻雀停在篱笆上看着你。",
    "你刚想起一件早就忘了的小事。", "空气里有股柴火味。",
    "地上有块石头被你踢了两下。", "你数了数一共有几户人家。",
]
MOMENTS_WEATHER = {
    "clear": ["阳光从树叶缝里漏下来。", "你的影子很清楚。"],
    "cloudy": ["云在慢慢挪。", "光线一会儿亮一会儿暗。"],
    "rain": ["雨点打在瓦片上。", "屋檐下在滴水。", "你的袖口有点潮。"],
    "snow": ["雪落在肩上没化。", "呼出来的气是白的。"],
}
MOMENTS_SEASON = {
    0: ["泥土的味道很新鲜。", "有棵树刚冒出嫩芽。"],
    1: ["蝉声吵得人发困。", "树荫底下凉快。"],
    2: ["有片黄叶掉在你脚边。", "风里有点凉。"],
    3: ["手有点冻僵了。", "地上有一层薄薄的白。"],
}
MOMENTS_TIME = [
    (0, 6, ["小镇还没醒。", "你有点想再睡会儿。"]),
    (6, 10, ["早上的空气很干净。", "今天才刚开始。"]),
    (10, 15, ["太阳正当头。", "影子缩到最短了。"]),
    (15, 19, ["影子开始变长了。", "一天过去一大半了。"]),
    (19, 24, ["天慢慢暗下来。", "灯该点上了。"]),
]


def moment_detail(rng, sim):
    pool = list(MOMENTS)
    pool += MOMENTS_WEATHER.get(sim.weather, [])
    pool += MOMENTS_SEASON.get(sim.season, [])
    h = sim.hour
    for a, b, lines in MOMENTS_TIME:
        if a <= h < b:
            pool += lines
            break
    return rng.choice(pool)


def villager_persona(sim, v, limit=18, rng=None):
    """把一个村民当前的真实状态，压成一段给模型的第二人称设定。

    关键在于"接地气"：模型必须知道现在几点、天气、自己在干嘛、
    身体什么感觉、身边有谁，说的话才不会像凭空冒出来的。
    另外塞一个随机小细节当抓手，台词才不会条条都长一个样。
    """
    hour = sim.hour
    clock = "%d点%s" % (int(hour), "半" if 0.4 < hour % 1 < 0.6 else "")
    near = []
    for o in sim.villagers:
        if o is v or o.hidden:
            continue
        if abs(o.x - v.x) < 26 and abs(o.y - v.y) < 22:
            lv = v.rel_level(o)
            near.append("%s（%s）" % (o.name, lv))
    parts = [
        "你叫%s，是小镇的%s。" % (v.name, v.role),
        "现在是%s季第%d天，%s，天气%s。" % (
            SEASON_CN.get(sim.season, "春"), sim.day, clock,
            WEATHER_CN.get(sim.weather, "晴")),
        "你%s。" % ACT_WORDS.get(v.activity or "", "在镇上待着"),
    ]
    feel = _body_feeling(v)
    if feel:
        parts.append("你%s。" % feel)
    fest = getattr(sim, "festival", None)
    if fest:
        parts.append("今天是%s，全镇都在庆祝。" % fest)
    if rng is not None:
        parts.append(moment_detail(rng, sim))
    if near:
        parts.append("你身边有：%s。" % "、".join(near[:3]))
    friend = None
    for oid, val in sorted(v.rel.items(), key=lambda kv: -kv[1]):
        cand = sim.villagers_by_id.get(oid)
        if cand and val >= 45:
            friend = cand
            break
    if friend:
        parts.append("你和%s关系很好。" % friend.name)
    # 个人记忆：优先用"他想起来的"那几条，而不是全镇共用的大事摘要。
    # 这一步是把"当下状态"升级成"有过去的人"的关键 ——
    # 没有它，同一个村民每一天开口都像刚出生。
    recalled = []
    try:
        recalled = v.recall(sim.day, sim.hour, limit=3)
    except Exception:
        recalled = []
    if recalled:
        parts.append("你记得：%s。" % "；".join(t for _, _, t in recalled))
    insight = getattr(v, "insight", "")
    if insight:
        parts.append("你最近%s。" % insight.rstrip("。"))
    if not recalled and sim.llm_events:
        parts.append("最近镇上发生的事：%s。" % "；".join(sim.llm_events[-2:]))
    parts.append("请用不超过 %d 个字说一句你现在想说的话。" % limit)
    return "".join(parts)


def decide_prompt(sim, v, options):
    lines = [
        "%s（%s）刚空下来，需要决定接下来做什么。" % (v.name, v.role),
        "现在%s季、%s，精力%d、饱腹%d、心情%d。" % (
            SEASON_CN.get(sim.season, "春"),
            WEATHER_CN.get(sim.weather, "晴"),
            v.needs["energy"], v.needs["hunger"], v.needs["mood"]),
        "可选行动（action 字段必须原样照抄左边的英文键）：",
    ]
    for key in options:
        lines.append("  %s = %s" % (key, DECISION_OPTIONS.get(key, key)))
    close = [o.name for o in sim.villagers
             if o is not v and not o.hidden
             and abs(o.x - v.x) < 40 and abs(o.y - v.y) < 34]
    if close:
        lines.append("附近有：%s。" % "、".join(close[:3]))
    lines.append('只输出一行 JSON，例如 {"action":"%s","why":"想散散步"}'
                 % (options[0] if options else "walk"))
    return "".join(lines)


SEASON_SCENTS = {
    0: "新芽、花、青草味",
    1: "蝉声、树荫、晒得发烫",
    2: "黄叶、凉风、该收的庄稼",
    3: "雪、白气、冻僵的手",
}


def narrate_prompt(sim, lines_today):
    season = SEASON_CN.get(sim.season, "春")
    head = "今天是第%d天，**%s季**，天气%s。" % (
        sim.day, season, WEATHER_CN.get(sim.weather, "晴"))
    # 必须把季节说死。只写"X季"实测不够 —— 模型会在春天写出柿子和冬天。
    head += "这个季节该有的东西是：%s。" % SEASON_SCENTS.get(sim.season, "")
    head += "不要提到别的季节的东西。"
    if lines_today:
        body = "今天镇上发生了这些事：" + "；".join(lines_today[-8:]) + "。"
    else:
        body = "今天镇上很安静，没什么特别的事。"
    return head + body + "请写一句当天的旁白。"


# ----------------------------------------------------------------------
# 输出清洗
# ----------------------------------------------------------------------
BAD_MARKERS = ("```", "```json", "\n", "作为", "当然", "好的，", "以下是")


def sanitize_line(text, limit=24):
    """把模型输出收拾成能直接进对话框的一句话。

    模型很爱加引号、加换行、加"当然可以"这类客套，
    这些在 8x12 像素的对话框里全是灾难，必须在这里掐掉。
    """
    if not text:
        return None
    t = text.strip()
    # 去掉 markdown 代码块围栏
    if t.startswith("```"):
        t = t.strip("`")
        if "\n" in t:
            t = t.split("\n", 1)[1] if t.lower().startswith("json") else t
    t = t.replace("\r", " ").replace("\n", " ").strip()
    for q in ('"', "'", "「", "」", "“", "”", "『", "』"):
        t = t.strip(q)
    t = t.strip()
    # 去掉常见的客套前缀
    for pre in ("好的，", "当然，", "好的:", "答案：", "这句话是"):
        if t.startswith(pre):
            t = t[len(pre):]
    if not t:
        return None
    if any(m in t for m in BAD_MARKERS):
        # 还残留问题（多行/代码块）就只取第一句
        for sep in ("。", "！", "？", ".", "!", "?"):
            if sep in t:
                t = t.split(sep)[0] + sep
                break
    t = t.strip()
    if len(t) > limit:
        # 优先在句号处断开，硬截会留下半句话
        cut = -1
        for sep in ("。", "！", "？", "，", "、"):
            k = t.rfind(sep, 0, limit + 1)
            if k > cut:
                cut = k
        if cut >= limit // 2:
            return t[:cut + 1]
        t = t[:limit].rstrip("，,、 ") + "…"
    # 太短或者几乎没有中文的，判定为失败（让调用方回退到内置台词）
    han = sum(1 for ch in t if "\u4e00" <= ch <= "\u9fff")
    if han < 3:
        return None
    return t


def parse_decision(text, allowed, options=None):
    """从模型输出里抠出 action，并且必须落在允许集合内。

    三道防线，因为小模型很不听话：
      1. 正常 JSON
      2. 输出里出现裸的英文键名（常见于它只回一个词）
      3. 输出里出现选项的中文描述（它照抄了右边那一列）
    全都对不上才算失败 —— 失败就回退到作息表，不会瞎走。
    """
    if not text:
        return None, None
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if "\n" in t:
            t = t.split("\n", 1)[1]

    # --- 1) JSON ---
    i, j = t.find("{"), t.rfind("}")
    if 0 <= i < j:
        try:
            d = json.loads(t[i:j + 1])
            if isinstance(d, dict):
                action = str(d.get("action", "")).strip().lower()
                why = str(d.get("why", "")).strip()
                if action in allowed:
                    return action, (why[:14] if len(why) > 14 else why)
        except Exception:
            pass

    low = t.lower()
    # --- 2) 裸英文键名 ---
    for k in sorted(allowed, key=len, reverse=True):
        if k in low:
            return k, ""
    # --- 3) 中文描述反查 ---
    if options:
        for k, desc in options.items():
            if k in allowed and desc and desc in t:
                return k, ""
    for k, desc in DECISION_OPTIONS.items():
        if k in allowed and desc in t:
            return k, ""
    return None, None


# ----------------------------------------------------------------------
# 客户端
# ----------------------------------------------------------------------
class LLMClient(object):
    def __init__(self, base_url=DEFAULT_BASE, model=DEFAULT_MODEL,
                 enabled=True, keep_alive="30m", min_interval=1.1,
                 timeout=300, max_queue=6, max_cache=512, think=False):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.enabled = bool(enabled)
        self.keep_alive = keep_alive
        self.min_interval = float(min_interval)
        self.timeout = float(timeout)
        self.max_queue = int(max_queue)
        self.think = bool(think)

        self._q = queue.Queue()
        self._out = queue.Queue()
        self._lock = threading.Lock()
        self._cache = OrderedDict()
        self._max_cache = max_cache
        self._stop = False
        self._last_call = 0.0
        self._fail_streak = 0
        self._retry_after = 0.0

        self.state = OFF if not self.enabled else LOADING
        self.detail = ""
        self.stats = {"sent": 0, "ok": 0, "failed": 0, "dropped": 0,
                      "cache_hits": 0, "queued": 0}
        self.first_ok_ts = None

        self._thread = threading.Thread(target=self._worker, daemon=True,
                                        name="pixeltown-llm")
        self._thread.start()

    # ---------------- 对外 ----------------
    def set_enabled(self, on):
        self.enabled = bool(on)
        with self._lock:
            if not self.enabled:
                self.state = OFF
                self.detail = ""
            elif self.state == OFF:
                self.state = LOADING
                self.detail = "等待模型"
                self._retry_after = 0.0
        # 清掉队列里排着的请求
        if not self.enabled:
            while True:
                try:
                    self._q.get_nowait()
                except queue.Empty:
                    break

    def submit(self, kind, prompt, meta=None, system=None, num_predict=64):
        """丢进队列立刻返回。返回 False 表示这次算了（调用方用内置台词兜底）。"""
        if not self.enabled:
            return False
        now = time.time()
        if now < self._retry_after:
            return False
        if self._q.qsize() >= self.max_queue:
            self.stats["dropped"] += 1
            return False
        key = hashlib.sha1(
            (self.model + "|" + (system or "") + "|" + prompt)
            .encode("utf-8")).hexdigest()
        self._q.put({"kind": kind, "prompt": prompt, "system": system,
                     "num_predict": num_predict, "meta": meta or {},
                     "key": key, "ts": now})
        self.stats["queued"] += 1
        return True

    def drain(self, max_items=6):
        """主循环每帧调用，把已完成的结果取走。"""
        out = []
        for _ in range(max_items):
            try:
                out.append(self._out.get_nowait())
            except queue.Empty:
                break
        return out

    def status(self):
        with self._lock:
            st, detail = self.state, self.detail
        return {
            "enabled": self.enabled, "state": st, "detail": detail,
            "model": self.model, "queued": self._q.qsize(),
            "stats": dict(self.stats),
        }

    def stop(self):
        self._stop = True

    # ---------------- 工作线程 ----------------
    def _mark(self, state, detail=""):
        with self._lock:
            self.state = state
            self.detail = detail

    def _call(self, item):
        payload = {
            "model": self.model,
            "messages": ([{"role": "system", "content": item["system"]}]
                         if item.get("system") else []) +
                        [{"role": "user", "content": item["prompt"]}],
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"temperature": 0.9, "num_predict": item["num_predict"],
                        "top_p": 0.9},
        }
        if not self.think:
            payload["think"] = False
        data = json.dumps(payload).encode("utf-8")
        url = self.base_url + "/api/chat"

        for attempt in (0, 1):
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    obj = json.loads(r.read().decode("utf-8", "replace"))
                msg = obj.get("message") or {}
                return (msg.get("content") or ""), None
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", "replace")[:200]
                except Exception:
                    pass
                # 有些模型不接受 think 字段，去掉再试一次
                if attempt == 0 and "think" in payload and e.code in (400, 500):
                    payload.pop("think", None)
                    data = json.dumps(payload).encode("utf-8")
                    continue
                return None, "HTTP %s %s" % (e.code, body)
            except Exception as e:
                return None, repr(e)
        return None, "unreachable"

    def _worker(self):
        while not self._stop:
            try:
                item = self._q.get(timeout=0.25)
            except queue.Empty:
                continue
            if not self.enabled:
                continue

            # 命中缓存就直接回，不打扰模型
            with self._lock:
                hit = self._cache.get(item["key"])
                if hit is not None:
                    self._cache.move_to_end(item["key"])
                    self.stats["cache_hits"] += 1
            if hit is not None:
                self._out.put({"kind": item["kind"], "text": hit,
                               "meta": item["meta"], "cached": True})
                continue

            # 限速：单卡上并发只会互相拖慢，串行 + 最小间隔最稳
            gap = time.time() - self._last_call
            if gap < self.min_interval:
                time.sleep(self.min_interval - gap)
            self._last_call = time.time()

            self.stats["sent"] += 1
            text, err = self._call(item)
            if err:
                self.stats["failed"] += 1
                self._fail_streak += 1
                # 指数退避，最多 60 秒重试一次，别把日志刷爆
                back = min(60.0, 2.0 ** min(self._fail_streak, 6))
                self._retry_after = time.time() + back
                self._mark(ERROR, err[:90])
                continue

            self._fail_streak = 0
            self._retry_after = 0.0
            self.stats["ok"] += 1
            if self.first_ok_ts is None:
                self.first_ok_ts = time.time()
            with self._lock:
                self._cache[item["key"]] = text
                self._cache.move_to_end(item["key"])
                while len(self._cache) > self._max_cache:
                    self._cache.popitem(last=False)
            self._mark(READY, "")
            self._out.put({"kind": item["kind"], "text": text,
                           "meta": item["meta"], "cached": False})
