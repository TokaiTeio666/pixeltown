# -*- coding: utf-8 -*-
"""像素小镇 · 事件文案

所有"发生了什么事"的中文说法都集中在这里，方便统一语气：
轻、短、不惊不吓，永远不报坏消息。
"""

KIND_NAMES = {
    "hall": "小镇大厅",
    "shop": "杂货铺",
    "smithy": "铁匠铺",
    "barn": "谷仓",
    "house": "民居",
}

SEASON_NAMES = {0: "春", 1: "夏", 2: "秋", 3: "冬"}
SEASON_LINES = {
    0: ["河边的柳树冒出了新芽", "风里开始有青草的味道", "第一朵野花开了"],
    1: ["蝉声把午后拉得很长", "树荫底下最凉快", "西瓜熟得正好"],
    2: ["第一片叶子黄了", "风吹过稻田，一片金黄", "柿子挂满了枝头"],
    3: ["下雪了，屋顶白了", "水边结了一层薄冰", "呼出的气都能看见"],
}
WEATHER_NAMES = {"clear": "晴", "cloudy": "多云", "rain": "雨", "snow": "雪"}
WEATHER_LINES = {
    "clear": ["今天天气很好", "阳光晒得人发懒"],
    "cloudy": ["云一层一层地压过来", "天色暗了些"],
    "rain": ["下雨了，大家都在屋檐下躲", "雨点打在瓦片上"],
    "snow": ["雪下得很安静", "雪地上留下了一串脚印"],
}


def meet_line(a, b, level):
    if level == "伴侣":
        return "%s和%s并肩坐在长椅上，谁也没说话" % (a, b)
    if level == "挚友":
        return "%s和%s聊了很久，笑声传到了街上" % (a, b)
    if level == "朋友":
        return "%s和%s在路口碰上了" % (a, b)
    return "%s朝%s点了点头" % (a, b)


def work_line(name, role):
    return {
        "镇长": "%s在小镇大厅里翻看记事本",
        "店主": "%s把货架上的东西摆整齐",
        "铁匠": "%s的锤子敲了一下午",
        "农夫": "%s去地里看了看庄稼",
        "渔夫": "%s在码头坐了一会儿",
        "孩子": "%s追着一只蝴蝶跑",
        "村民": "%s在街上慢慢走",
    }.get(role, "%s在镇上转了一圈") % name


def build_line(kind, name=None):
    k = KIND_NAMES.get(kind, "房子")
    if kind == "house":
        return "叮叮当当，小镇上多了一栋新%s" % k
    return "%s终于盖起来了" % k


def upgrade_line(kind, level):
    k = KIND_NAMES.get(kind, "房子")
    if level == 2:
        return "%s翻修了一新，屋顶和门窗都换了" % k
    return "%s扩建了，远远就能看见" % k


def season_line(season, idx):
    lines = SEASON_LINES.get(season, [])
    return lines[idx % len(lines)] if lines else "季节换了"


def weather_line(w):
    lines = WEATHER_LINES.get(w, [])
    return lines[0] if lines else ""


def new_neighbor_line(name):
    return "%s搬来了，成了小镇的新住户" % name


# ----------------------------------------------------------------------
# 人口流动
#
# 搬走这件事必须处理得很小心：这个应用的原则是"不会有坏事"，
# 所以离开从来不是因为谁被冷落了，而是"出去看看"。
# 而且搬走会腾出位子，随后就有人搬进来 —— 小镇有出有进，不是只涨不跌。
# ----------------------------------------------------------------------
ARRIVE_LINES = [
    "%s搬来了，说是看上了这地方安静",
    "%s带着一口箱子搬来了，箱子里据说全是书",
    "%s搬来了，第一天就把全镇的路走了一遍",
    "%s搬来了，说想在河边搭个小棚子",
    "%s搬来了，还带来了一盆花",
    "有个叫%s的年轻人搬来了，说是来学手艺的",
    "%s搬来了，说这里的水比别处甜",
]

DEPART_LINES = [
    "%s说要去城里学门新手艺，收拾了行李走了",
    "%s跟着路过的商队走了，说想看看外面的世界",
    "%s搬去了山那边，说是亲戚家缺人手",
    "%s说想出海走一趟，不知道什么时候回来",
    "%s走了，留了张字条说春天或许会回来",
    "%s离开了小镇，大家都去路口送了送",
]

DEPART_WARM = [
    "走之前%s把家里的花分给了邻居们",
    "%s把钥匙交给了隔壁，说屋子帮着照看一下",
    "大家在路口站了一会儿，看着%s走远",
]


def festival_start(name):
    return "今天是%s，全镇都歇了手上的活" % name


# 节日期间的场景描写，(季节 -> 候选)
FESTIVAL_SCENES = {
    0: ["树上都系了彩带", "孩子们把花戴在头上", "门口摆出了新摘的花枝"],
    1: ["水面上漂着一盏盏小灯", "大家把想说的话写在灯上", "灯顺着水漂到了湾里"],
    2: ["谷仓前堆满了新收的粮食", "广场上拼起了长桌", "锅里的热气一直没断过"],
    3: ["街上挂满了灯笼", "雪地被灯照得暖暖的", "每家门口都点了一盏"],
}


def festival_scene(season, idx):
    lines = FESTIVAL_SCENES.get(season, [])
    return lines[idx % len(lines)] if lines else ""


# 随机小事。全是好事或者无所谓的事 —— 没有一件是坏的。
# (权重, 文案, 视觉效果种类)
RANDOM_EVENTS = [
    (3, "有只野猫溜进了小镇，蹲在井边不肯走", "cat"),
    (2, "谁家的鸡跑出来了，全镇帮着找了一下午", None),
    (3, "外乡的货郎来了，在广场摆了半天的摊", "market"),
    (2, "有人在田里挖出一个怪模怪样的陶罐", None),
    (2, "不知谁在墙上画了一只很大的猫", None),
    (2, "有人过生日，广场上分了点心", "party"),
    (2, "一群鸭子摇摇摆摆地穿过了大街", "ducks"),
    (2, "有户人家在屋顶晒了一整排被子", None),
    (1, "井水突然变得特别甜，大家都去打了一桶", None),
    (1, "半夜有谁在唱歌，第二天谁也没承认", None),
    (2, "雨后挂起了一道彩虹，横跨整个小镇", "rainbow"),
    (1, "早上起了雾，房子像是浮着的", "mist"),
    (1, "夜里有人看见流星，许了愿", "meteor"),
    (1, "河边的芦苇开花了，风一吹像雪", None),
    (1, "有只狗跟着货郎来了，赖着不走", "dog"),
]

# 动物迁徙：季节 -> (种类, 文案)
MIGRATIONS = {
    0: ("butterfly", "一大群蝴蝶从镇子上头飞过去了"),
    1: ("dragonfly", "蜻蜓成群地掠过水面"),
    2: ("birds", "大雁排成人字，往南边去了"),
    3: ("birds", "一群白鸟顶着雪往北边飞"),
}

MIGRATION_AFTER = [
    "它们走了以后，镇上安静了好一会儿",
    "有人一直看到最后一只看不见为止",
    "孩子们追着跑了一段，没追上",
    "有只掉队的在后面急急忙忙地赶",
]


def crop_line(name):
    return "%s把地里的庄稼收了一茬" % name


def fishing_line(name):
    if name:
        return "%s在码头钓上来一条小鱼" % name
    return "有人在码头钓上来一条小鱼"


def idle_lines(name):
    return [
        "%s在广场上发了一会儿呆" % name,
        "%s绕着小池塘走了半圈" % name,
        "%s蹲下来看了一会儿蚂蚁" % name,
        "%s在井边喝了口水" % name,
    ]


def offline_summary(days, built, upgraded, arrivals, season_changed):
    parts = []
    if days >= 1:
        parts.append("你不在的这段时间，小镇过了 %g 天" % days)
    else:
        parts.append("你刚离开一会儿，小镇没什么变化")
    if arrivals:
        parts.append("新搬来 %d 位住户" % arrivals)
    if built:
        parts.append("盖起了 %d 栋新房子" % built)
    if upgraded:
        parts.append("%d 栋老房子翻新了" % upgraded)
    if season_changed:
        parts.append("季节已经换了")
    return "，".join(parts) + "。"


# ----------------------------------------------------------------------
# 外部活动映射
#
# 机器在忙的时候，镇上的工匠也说两句。语气必须和别处一样轻，
# 而且**不能暴露"我在看你的 CPU"这件事** —— 那会让小镇变成监控软件。
# 所以这些话说的是"活儿多"，不是"你占用率高"。
# ----------------------------------------------------------------------
ACTIVITY_BUSY_LINES = {
    "铁匠": ["%s今天的炉子就没停过，锤声比平时密",
             "%s说活儿一下子多了起来，铁块烧了一炉又一炉"],
    "木匠": ["%s的刨花堆了半屋子，手一直没停下",
             "%s说今天木料用得快，得再去搬一趟"],
    "织工": ["%s的织机响个不停，线轴换了一个又一个",
             "%s低头织了一整天，说手都热了"],
    "农夫": ["%s在地里来回跑，说今天得赶完这片",
             "%s把锄头抡得飞快，田垄都翻了一遍"],
    "渔夫": ["%s把网撒了又收，说今天水里格外热闹",
             "%s在码头忙到忘了看天色"],
}


def activity_busy_line(name, role):
    pool = ACTIVITY_BUSY_LINES.get(role)
    if not pool:
        return ""
    # 不用内置 hash()：字符串哈希每个进程都不一样，
    # 那样存档前后同一个人会跳到另一条台词，读起来像换了个人。
    idx = sum(ord(c) for c in name) % len(pool)
    return pool[idx] % name


def activity_note_line(name, role, note):
    """外部给了明确的标记文本时，用它当"今天在忙什么"。"""
    pool = ACTIVITY_BUSY_LINES.get(role)
    if not pool:
        return "%s说今天镇上格外忙" % name
    return "%s听说「%s」，手上的活儿不敢停" % (name, note)
