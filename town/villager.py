# -*- coding: utf-8 -*-
"""像素小镇 · 村民

一个村民 = 一个人 + 一份作息表 + 一串关系。
没有饥饿惩罚、没有死亡，需求只影响表情和心情，
这样"忘了打开看"永远不会带来损失 —— 回来只会看到变化。
"""

import heapq
import math

TILE = 8

NAMES = [
    "阿禾", "小满", "石头", "阿杏", "老周", "豆豆", "阿栗", "秋生",
    "阿桃", "铁牛", "阿桑", "三丫", "阿木", "麦子", "阿棠", "小满月",
    "阿柚", "长庚", "阿豆", "春生", "阿菖", "小鹿", "阿岩", "溪儿",
    "阿枣", "南风", "阿笙", "正月",
]

# (职业名, 工作建筑类型或 None, 权重)
ROLE_DEFS = [
    ("镇长", "hall", 1),
    ("店主", "shop", 2),
    ("铁匠", "smithy", 2),
    ("农夫", "barn", 3),
    ("渔夫", None, 2),
    ("孩子", None, 3),
    ("木匠", None, 2),
    ("牧人", None, 2),
    ("织工", None, 2),
    ("货郎", None, 1),
    ("采药人", None, 1),
    ("村民", None, 3),
]

# 没有专属建筑的职业，白天各自待在哪
ROLE_SPOT = {
    "渔夫": "dock",
    "牧人": "farm",
    "织工": "home",
    "采药人": "wander",
    "货郎": "wander",
    "木匠": "wander",
}

HAIR_IDX = [0, 1, 2, 3, 4, 5]
CLOTH_IDX = [0, 1, 2, 3, 4, 5]
SKIN_IDX = [0, 1, 2, 3]
PANTS_IDX = [0, 1, 2, 3]

DIRS = ("down", "up", "side")


# ----------------------------------------------------------------------
# 寻路
# ----------------------------------------------------------------------
def _line_clear(walk, a, b):
    """两格之间的直线是否整条都可走（用于拉直路径）。

    斜向跨格时必须同时检查另外两个正交邻格，
    否则村民会从建筑/树的角上"削"过去，看起来像穿墙。
    """
    (x0, y0), (x1, y1) = a, b
    dx, dy = abs(x1 - x0), abs(y1 - y0)
    sx = 1 if x1 > x0 else -1
    sy = 1 if y1 > y0 else -1
    err = dx - dy
    x, y = x0, y0
    while True:
        if not walk[y][x]:
            return False
        if (x, y) == (x1, y1):
            return True
        e2 = 2 * err
        nx, ny = x, y
        if e2 > -dy:
            err -= dy
            nx += sx
        if e2 < dx:
            err += dx
            ny += sy
        if nx != x and ny != y:
            if not walk[y][nx] or not walk[ny][x]:
                return False
        x, y = nx, ny


def astar(walk, start, goal, limit=6000):
    """格子上的 A*，返回 [(tx,ty), ...]；走不到返回 None。"""
    if start == goal:
        return [start]
    if not walk[goal[1]][goal[0]]:
        return None
    openq = [(0, start)]
    came = {start: None}
    g = {start: 0}
    seen = 0
    while openq:
        _, cur = heapq.heappop(openq)
        if cur == goal:
            path = []
            node = cur
            while node is not None:
                path.append(node)
                node = came[node]
            path.reverse()
            return _simplify(walk, path)
        seen += 1
        if seen > limit:
            return None
        cx, cy = cur
        base = g[cur]
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = cx + dx, cy + dy
            if not (0 <= ny < len(walk) and 0 <= nx < len(walk[0]) and walk[ny][nx]):
                continue
            ng = base + 1
            key = (nx, ny)
            if key in g and g[key] <= ng:
                continue
            g[key] = ng
            came[key] = cur
            heapq.heappush(openq, (ng + abs(nx - goal[0]) + abs(ny - goal[1]), key))
    return None


def _simplify(walk, path):
    """把阶梯状路径拉直，走起来才像人而不是机器人。"""
    if len(path) <= 2:
        return path
    out = [path[0]]
    i = 0
    while i < len(path) - 1:
        j = len(path) - 1
        while j > i + 1 and not _line_clear(walk, path[i], path[j]):
            j -= 1
        out.append(path[j])
        i = j
    return out


# ----------------------------------------------------------------------
# 村民
# ----------------------------------------------------------------------
class Villager(object):
    SPEED = 15.0            # 逻辑像素/秒

    def __init__(self, vid, name, role, home=None, work=None, **kw):
        self.vid = vid
        self.name = name
        self.role = role
        self.home = home            # lot id
        self.work = work            # lot id 或 None
        self.skin = kw.get("skin", 0)
        self.hair = kw.get("hair", 0)
        self.cloth = kw.get("cloth", 0)
        self.pants = kw.get("pants", 0)
        self.adult = kw.get("adult", True)

        self.x = float(kw.get("x", 0))
        self.y = float(kw.get("y", 0))      # y = 脚底
        self.dir = "down"
        self.frame = 0
        self.anim_t = 0.0
        self.moving = False

        self.path = []
        self.goal_px = None
        self.state = "idle"
        self.state_t = 0.0
        self.busy = 0.0                      # 当前活动还要持续多久（游戏小时）
        self.activity = None
        self.retry_at = None
        self.hidden = False                  # 夜里进屋后就隐藏起来
        self._flip = False
        self.ai_action = None                # 本地模型给出的小决定
        self.ai_why = ""
        self.ai_action_t = -999.0
        self.say = ""
        self.say_t = 0.0
        self.partner = None
        self.chat_with = None

        self.needs = {"hunger": 78.0, "energy": 82.0, "mood": 70.0}
        self.rel = {}                        # vid -> 好感度 0..100
        self.total_rel = 0.0

        # ---- 记忆流 ----
        # 每条 = (day, hour, text, kind)。只记"跟他有关"的事，
        # 全镇大事走 sim.llm_events，两者不混。
        # kind 用来做检索分组和反思：talk/rel/event/move/mood
        self.memories = []
        self.insight = ""                    # 反思压出来的自我认知，一句话

    # ---------------- 记忆 ----------------
    MEM_LIMIT = 24

    # 淘汰优先级：数字越小越先被丢。
    # move/event 往往是"全镇都知道"的事，谁都能记；
    # rel/talk 才是"只有我记得"的私人历史，必须留到后面才丢。
    # 不做这个分级的话，30 天后每个人的记忆表都长成同一份镇志。
    _EVICT_ORDER = ("move", "event", "mood", "talk", "rel")

    def remember(self, day, hour, text, kind="event"):
        """记一条。同一条重复出现就只更新时间，避免"和谁谁成了朋友"记十遍。"""
        text = (text or "").strip()
        if not text:
            return
        for i, (d, h, t, k) in enumerate(self.memories):
            if t == text:
                self.memories[i] = (day, hour, text, k)
                return
        self.memories.append((day, hour, text, kind))
        if len(self.memories) > self.MEM_LIMIT:
            for want in self._EVICT_ORDER:
                for i, (d, h, t, k) in enumerate(self.memories):
                    if k == want:
                        self.memories.pop(i)
                        break
                else:
                    continue
                break
            else:
                self.memories.pop(0)

    def recall(self, day, hour, limit=3, want=("rel", "talk", "event", "move", "mood")):
        """检索最该浮上心头的几条。

        没有 embedding，用三条便宜的信号打分：
          * 新近性：越近越高（半衰期约 3 天）
          * 相关性：关系类 > 对话类 > 事件类 > 闲事
          * 个体差异：同一条公共事件，不同的人在意程度不一样
        这个打分是刻意做"笨"的 —— 它必须能在断网、没模型时也算得出来。
        """
        if not self.memories:
            return []
        now = day * 24.0 + hour
        kind_w = {"rel": 1.0, "talk": 0.8, "event": 0.6, "move": 0.5, "mood": 0.4}
        scored = []
        for d, h, t, k in self.memories:
            if k not in want:
                continue
            age = max(0.0, now - (d * 24.0 + h))
            recency = 0.5 ** (age / 72.0)          # 3 天半衰
            # 用 vid + 文本内容做确定性抖动：让不同的人对同一条公共事件
            # 的"在意程度"不一样，否则全镇检索结果永远一模一样。
            # 必须按内容哈希而不是按长度 —— 按长度的话，
            # 长度相同的两条记忆会得到同一个抖动，区分度就没了。
            seed = self.vid * 37 + (sum(ord(c) for c in t) % 97)
            wobble = 1.0 + 0.35 * ((seed % 7) - 3) / 3.0
            scored.append((recency * kind_w.get(k, 0.5) * wobble, d, h, t, k))
        scored.sort(key=lambda x: -x[0])
        return [(d, h, t) for _, d, h, t, _ in scored[:limit]]

    def reflect(self, day, hour):
        """把同类记忆压成一句自我认知。攒不够 3 条就先不反思。

        优先从"私人"的 rel/talk 里提炼。move/event 这类是全镇广播的镇志，
        拿它反思，十二个人会得出同一句 insight —— 那就等于没有自我认知。
        """
        by_kind = {}
        for d, h, t, k in self.memories:
            by_kind.setdefault(k, []).append((d, h, t))
        best = None
        for k in ("rel", "talk", "mood", "move", "event"):
            items = by_kind.get(k) or []
            if len(items) >= 3:
                best = (k, items)
                break
        if not best:
            return self.insight
        k, items = best
        if k == "rel":
            # 只留最近两条，且去掉重复，读起来像"人话"
            seen, names = set(), []
            for _, _, t in reversed(items):
                if t not in seen:
                    seen.add(t)
                    names.append(t)
                if len(names) >= 2:
                    break
            self.insight = "和人走得近（%s）" % "、".join(names)
        elif k == "talk":
            self.insight = "话变得多了起来"
        elif k == "move":
            self.insight = "看着小镇一点点变样：%s" % items[-1][2]
        elif k == "mood":
            self.insight = "心里不太踏实"
        else:
            self.insight = items[-1][2]
        return self.insight

    # ---------------- 基础 ----------------
    @property
    def tile(self):
        return (int(self.x) // TILE, int(self.y - 1) // TILE)

    def face_towards(self, px, py):
        dx, dy = px - self.x, py - self.y
        if abs(dx) > abs(dy):
            self.dir = "side"
            self._flip = dx < 0
        else:
            self.dir = "up" if dy < 0 else "down"
            self._flip = False

    def say_line(self, text, dur=2.6):
        self.say = text
        self.say_t = dur

    # ---------------- 移动 ----------------
    def set_goal(self, world, px, py):
        """给一个像素目标，算出路径。返回是否可达。"""
        cols, rows = len(world.walk[0]), len(world.walk)
        tx = max(0, min(cols - 1, int(px) // TILE))
        ty = max(0, min(rows - 1, int(py) // TILE))
        tx, ty = world.nearest_walkable(tx, ty)
        sx, sy = self.tile
        if not world.walkable(sx, sy):
            sx, sy = world.nearest_walkable(sx, sy)
            self.x, self.y = sx * TILE + 4, sy * TILE + 4
        path = astar(world.walk, (sx, sy), (tx, ty))
        if path is None:
            return False
        self.path = [(tx * TILE + 4, ty * TILE + 4) for (tx, ty) in path[1:]]
        self.goal_px = (tx * TILE + 4, ty * TILE + 4)
        return True

    def _step_move(self, dt):
        if not self.path:
            self.moving = False
            return True
        tx, ty = self.path[0]
        dx, dy = tx - self.x, ty - self.y
        dist = math.hypot(dx, dy)
        step = self.SPEED * dt
        if dist <= step or dist < 0.001:
            self.x, self.y = float(tx), float(ty)
            self.path.pop(0)
            if not self.path:
                self.moving = False
                return True
            return False
        self.x += dx / dist * step
        self.y += dy / dist * step
        self.moving = True
        if abs(dx) > abs(dy):
            self.dir = "side"
            self._flip = dx < 0
        else:
            self.dir = "up" if dy < 0 else "down"
            self._flip = False
        return False

    # ---------------- 需要 ----------------
    def tick_needs(self, dt_hours, sleeping=False, eating=False):
        """推进需求。

        这里原本有个真实的设计漏洞：饱腹只减不增，作息表里 6 点和 12 点
        明明排了"吃饭"，但那只是走过去站着，不产生任何效果。
        跑几天后全镇 hunger 都是 0 —— 于是 AI 台词里每一句都在喊饿。
        补上真正的进食之后，饱腹会自然在一个区间里起伏，台词也随之多样了。
        """
        n = self.needs
        if eating:
            # 用饱和曲线：越饱回得越慢，一顿饭最多带到 85 左右。
            # 线性回复会一路顶到 100 然后卡住，饱腹值就失去意义了。
            cap = 85.0
            if n["hunger"] < cap:
                n["hunger"] = min(
                    cap, n["hunger"] + dt_hours * 90.0
                    * (1.0 - n["hunger"] / cap))
            n["energy"] = min(100.0, n["energy"] + dt_hours * 3.0)
        else:
            n["hunger"] = max(0.0, n["hunger"] - dt_hours * 4.0)
        if sleeping:
            n["energy"] = min(100.0, n["energy"] + dt_hours * 13.0)
            if not eating:
                n["hunger"] = max(0.0, n["hunger"] - dt_hours * 1.0)
        elif not eating:
            n["energy"] = max(0.0, n["energy"] - dt_hours * 4.2)
        else:
            pass
        # 心情跟着身体状态慢慢漂
        target = 70.0 + (n["energy"] - 50.0) * 0.22 + (n["hunger"] - 50.0) * 0.16
        n["mood"] += (target - n["mood"]) * min(1.0, dt_hours * 0.5)

    @property
    def mood_word(self):
        m = self.needs["mood"]
        if m >= 82:
            return "心花怒放"
        if m >= 64:
            return "心情不错"
        if m >= 46:
            return "还算平静"
        if m >= 30:
            return "有点没精神"
        return "蔫蔫的"

    def rel_level(self, other):
        v = self.rel.get(other.vid if hasattr(other, "vid") else other, 0.0)
        if v >= 92:
            return "伴侣"
        if v >= 72:
            return "挚友"
        if v >= 45:
            return "朋友"
        if v >= 20:
            return "熟人"
        return "点头之交"

    def meet(self, other, amount=1.0):
        """见面聊一会儿，互相加好感。"""
        a = self.rel.get(other.vid, 0.0)
        b = other.rel.get(self.vid, 0.0)
        # 心情好更容易亲近
        gain = amount * (0.6 + self.needs["mood"] / 200.0)
        self.rel[other.vid] = min(100.0, a + gain)
        other.rel[self.vid] = min(100.0, b + gain)

    # ---------------- 存档 ----------------
    def to_dict(self):
        return {
            "vid": self.vid, "name": self.name, "role": self.role,
            "home": self.home, "work": self.work,
            "skin": self.skin, "hair": self.hair,
            "cloth": self.cloth, "pants": self.pants, "adult": self.adult,
            "x": round(self.x, 2), "y": round(self.y, 2),
            "dir": self.dir, "state": self.state,
            "needs": {k: round(v, 2) for k, v in self.needs.items()},
            "rel": {k: round(v, 2) for k, v in self.rel.items()},
            "partner": self.partner,
            # 记忆也要存：不然每次读档，村民都失忆一次，
            # persona 里那句"你最近……"会突然消失，台词立刻变浅
            "mem": [[d, round(h, 2), t, k] for (d, h, t, k) in self.memories],
            "insight": self.insight,
        }

    def apply_dict(self, d):
        for k in ("x", "y", "dir", "state"):
            if k in d:
                setattr(self, k, d[k])
        for k, v in (d.get("needs") or {}).items():
            self.needs[k] = float(v)
        self.rel = {k: float(v) for k, v in (d.get("rel") or {}).items()}
        self.partner = d.get("partner")
        self.memories = [(int(m[0]), float(m[1]), str(m[2]), str(m[3]))
                         for m in (d.get("mem") or []) if len(m) >= 4][-self.MEM_LIMIT:]
        self.insight = d.get("insight") or ""
        self.path = []


def make_villager(vid, rng, world, role="村民", work=None, home=None, adult=True):
    name = NAMES[vid % len(NAMES)]
    v = Villager(
        vid, name, role, home=home, work=work,
        skin=rng.choice(SKIN_IDX), hair=rng.choice(HAIR_IDX),
        cloth=rng.choice(CLOTH_IDX), pants=rng.choice(PANTS_IDX),
        adult=adult,
    )
    lot = next((l for l in world.lots if l["id"] == home), None) if home else None
    if lot:
        v.x, v.y = world.door_px(lot)
    else:
        tx, ty = world.random_walkable(rng)
        v.x, v.y = tx * TILE + 4, ty * TILE + 4
    return v


def pick_role(kinds, taken, idx):
    """给新搬来的人挑一个岗位。

    优先挑**还没人干**的岗位；所有岗位都有人了，才退回到"村民/孩子"。
    原来的实现放行了农夫重复，小镇变大之后退化成"四个农夫"。
    """
    unique, generic = [], []
    for role, wkind, weight in ROLE_DEFS:
        if wkind is not None and wkind not in kinds:
            continue
        if role in ("村民", "孩子"):
            generic.extend([(role, wkind)] * weight)
            continue
        if role in taken:
            continue
        unique.extend([(role, wkind)] * weight)
    pool = unique or generic
    if not pool:
        return "村民", None
    return pool[idx % len(pool)]
