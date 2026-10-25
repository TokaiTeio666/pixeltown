# -*- coding: utf-8 -*-
"""像素小镇 · 模拟器

时间：默认 1 个游戏日 = 6 分钟真实时间（约 15 秒 = 1 游戏小时）。
成长：解锁空地 → 盖房 → 翻新，一天推进一步，永远只往前走，不会有坏事。
离线：回来时最多补算 3 个游戏日，然后告诉你"你不在的时候发生了什么"。
"""

import json
import os
import random
import time

from . import events
from . import llm as LLM
from . import villager as V
from .world import World, TILE

DAY_SECONDS = 360.0          # 1 个游戏日 = 6 分钟
SEASON_DAYS = 7              # 1 个季节 = 7 个游戏日
CROP_HOURS_PER_STAGE = 10.0  # 作物每长一级要多少游戏小时
MAX_OFFLINE_DAYS = 3.0
SAVE_VERSION = 2

AI_DAILY_BUDGET = 150        # 每天最多问模型多少次
AI_AMBIENT_HOURS = 2.4       # 每隔多少游戏小时让一个村民说句话

# ---- 人口 ----
MAX_POP = 12                 # 硬上限：再热闹也不能挤爆这座小岛
CORE_SLOTS = 4               # 公共建筑的主事人（镇长/店主/铁匠/农夫）
POP_FLOOR = 4                # 再冷清也不低于这个数
ARRIVE_CHANCE = 0.22         # 有空位时，每次人口结算搬进来的概率（一天结算两次）
DEPART_CHANCE = 0.10         # 满员时，每次人口结算搬走的概率

# ---- 节日 ----
FESTIVAL_DAY = 4             # 每个季节的第几天办节
FESTIVAL_NAMES = {0: "花朝节", 1: "河灯节", 2: "丰收祭", 3: "灯会"}

# ---- 随机事件 / 迁徙 ----
EVENT_CHANCE = 0.42          # 每天发生一件小事的概率
MIGRATION_CHANCE = 0.11      # 每天来一群动物的概率
EVENT_COOLDOWN = 4           # 同一件小事至少隔几天才能再出现

# 搬走的倾向：村民最容易走，有专任岗位的人最难走
DEPART_WEIGHT = {
    "村民": 4.0, "货郎": 2.2, "采药人": 2.0, "木匠": 1.6, "织工": 1.6,
    "牧人": 1.6, "孩子": 0.9,
    "镇长": 0.3, "店主": 0.4, "铁匠": 0.4, "农夫": 0.4, "渔夫": 0.7,
}

# AI 决定的小行动 -> 实际要去的地点
ACTION_SPOT = {
    "walk": "wander",
    "social": "plaza",
    "rest": "home",
    "eat": "well",
    "farm": "farm",
    "dock": "dock",
    "play": "wander",
}

ROLE_TO_KIND = {
    "镇长": "hall",
    "店主": "shop",
    "铁匠": "smithy",
    "农夫": "barn",
}


class TownSim(object):
    def __init__(self, seed=20240607, save_path=None):
        self.rng = random.Random(seed)
        self.seed = seed
        self.world = World(seed)
        self.save_path = save_path

        # 从第 1 天早上 7 点开始。
        # 不能用午夜开局：一打开看到的是漆黑空镇、全镇睡觉，第一印象很差；
        # 早上 7 点正好是人陆续出门的时候。
        self.t = 7.0
        self.weather = "clear"
        self.log_lines = []      # [(day, hour, text), ...]
        self.highlights = []     # 供渲染层消费的小特效 [(x, y, kind, ttl)]
        self.villagers = []
        self.last_seen = time.time()
        self._ambient_t = 0.0
        self._last_growth_day = 0
        self._last_weather_day = 0

        self.queue = self._make_growth_queue()
        self.queue_index = 0
        self._crop_log_day = -1
        self._next_growth_t = 30.0        # 先给个值，下面按 t 对齐到 6/18 点
        self._resync_growth_clock()

        # ---- 本地大模型（默认不启用，由 app 调 setup_llm 打开）----
        self.llm = None
        self.ai_model = LLM.DEFAULT_MODEL
        self.ai_base = LLM.DEFAULT_BASE
        self.chat_log = []                # AI 说的话单独存，不淹掉小镇事件
        self.llm_events = []              # 最近大事，给模型当上下文
        self.llm_calls_today = 0
        self._llm_day = -1
        self._ai_was_enabled = False      # 存档里记录的开关，由 app 决定要不要真的接
        # 诊断用：决策落地了几次、被拒了几次、模型原始输出长什么样。
        # 不加这些，"决策一条都没生效"会变成完全静默的失败。
        self.ai_decisions = 0
        self.ai_decide_rejected = 0
        self.ai_said = 0
        self.ai_narrations = 0
        self.ai_narrate_submitted = 0
        self.ai_styles = {}               # 各种风格各被用了多少次（验证用）
        self.ai_goofy = 2                 # 搞怪档位：0 正经 / 1 温和 / 2 放开
        self.ai_raw = []
        self.loaded_last_seen = None
        self._llm_say_pending = set()
        self._llm_decide_pending = set()
        self._llm_free_decided = set()
        self._llm_ambient_t = 0.0
        self._llm_last_speaker = -1
        self._next_vid = 0

        # ---- 人口流动 / 节日 / 随机事件 / 动物迁徙 ----
        self.fx = {}          # 小效果的剩余时长（游戏小时），{种类: 小时}
        self.migrations = []  # 正在经过小镇的动物群
        self.festival_days = 0
        self.arrivals = 0
        self.departures = 0
        self._recent_events = []

        # ---- 外部活动（真实电脑忙不忙）----
        # 这里只存"别人算好的一帧"，不持有线程、不读系统。
        # 好处：离线出图 / 自检 / 读档都完全不受影响，
        # 没有这个字段时小镇一模一样地运转。
        self.activity = {"enabled": False, "level": 0.0, "busy": False, "note": ""}
        self._activity_said_t = -999.0

        self._spawn_initial()
        self.log("小镇的一天开始了，慢慢看就好")

    # ==================================================================
    # 时间
    # ==================================================================
    @property
    def hours_per_second(self):
        return 24.0 / DAY_SECONDS

    @property
    def day(self):
        return int(self.t // 24) + 1

    @property
    def hour(self):
        return self.t % 24.0

    @property
    def season(self):
        return ((self.day - 1) // SEASON_DAYS) % 4

    @property
    def season_name(self):
        return events.SEASON_NAMES[self.season]

    # ==================================================================
    # 村民
    # ==================================================================
    def lot(self, lot_id):
        for l in self.world.lots:
            if l["id"] == lot_id:
                return l
        return None

    def _free_home(self):
        used = {v.home for v in self.villagers if v.home}
        for l in self.world.lots:
            if l["is_house"] and l["level"] > 0 and l["id"] not in used:
                return l["id"]
        return None

    def add_villager(self, home=None, force_role=None):
        # vid 必须单调递增，不能用 len(villagers)。
        # 一旦有人搬走，len 会回退，新来的人就会和旧 id 撞车 ——
        # 关系表、待回复集合全是按 vid 索引的，撞了就串味。
        vid = self._next_vid
        self._next_vid += 1
        role = force_role
        work = None
        if role is None:
            # 镇上没有孩子的时候，优先补一个 —— 没有孩子的小镇没意思
            if not any(v.role == "孩子" for v in self.villagers) \
                    and len(self.villagers) >= 4 and self.rng.random() < 0.5:
                role, wkind = "孩子", None
            else:
                taken = {v.role for v in self.villagers}
                role, wkind = V.pick_role({l["kind"] for l in self.world.lots},
                                          taken, vid)
                work = next((l["id"] for l in self.world.lots
                             if l["kind"] == wkind), None)
        elif role in ROLE_TO_KIND:
            work = next((l["id"] for l in self.world.lots
                         if l["kind"] == ROLE_TO_KIND[role]), None)
        if home is None:
            home = self._free_home()
        v = V.make_villager(vid, self.rng, self.world, role=role, work=work, home=home,
                            adult=(role != "孩子"))
        v.say_line("大家好呀", 3.0)
        self.villagers.append(v)
        return v

    def _spawn_initial(self):
        # 每栋开局就存在的建筑配一位主事的人
        for l in self.world.lots:
            if l["level"] <= 0:
                continue
            role = {"hall": "镇长", "shop": "店主", "smithy": "铁匠",
                    "barn": "农夫"}.get(l["kind"])
            if role:
                self.add_villager(home=self._free_home() or l["id"], force_role=role)
        # 再补两个住户，让街上开始有人
        for _ in range(2):
            self.add_villager()

    # ==================================================================
    # 人口流动 / 节日 / 随机事件 / 动物迁徙
    # ==================================================================
    @property
    def built_houses(self):
        return sum(1 for l in self.world.lots if l["is_house"] and l["level"] > 0)

    @property
    def capacity(self):
        """住得下多少人：公共岗位固定 4 个，每盖好一栋民居多一个位子。"""
        return min(MAX_POP, CORE_SLOTS + self.built_houses)

    @property
    def festival(self):
        """今天是节日就返回节日名，否则 None。

        做成无状态属性，不进存档 —— 由日期直接算出来，
        存档读档、离线补算都不会算错。
        """
        if (self.day - 1) % SEASON_DAYS + 1 == FESTIVAL_DAY:
            return FESTIVAL_NAMES.get(self.season)
        return None

    def _population_step(self):
        """人口有出有进。

        原来的实现只涨不跌：盖一栋房就来一个人，最后挤满。
        现在满了会有人搬走、空出位子又有人搬来 —— 小镇是活的，不是单向增长。
        搬走永远是"出去看看"，绝不是因为谁被冷落了。
        """
        pop = len(self.villagers)
        cap = self.capacity
        if pop < cap:
            if self.rng.random() < ARRIVE_CHANCE:
                self._arrive()
        elif pop > POP_FLOOR and self.rng.random() < DEPART_CHANCE:
            self._depart()

    def _arrive(self):
        if len(self.villagers) >= self.capacity:
            return
        v = self.add_villager()
        self.arrivals += 1
        line = self.rng.choice(events.ARRIVE_LINES) % v.name
        self.log(line)
        self.llm_remember(line)
        self.highlights.append([v.x, v.y - 13.0, "heart", 2.4])
        if len(self.highlights) > 40:
            self.highlights = self.highlights[-40:]
        return v

    def _depart(self):
        if len(self.villagers) <= POP_FLOOR:
            return
        # 按职业决定"谁更可能走"：村民最容易走，有专任岗位的人最难走。
        # 但**要留住最后一个孩子** —— 一个没有孩子的小镇不像小镇。
        # （原来的实现优先让"村民/孩子"走，结果孩子每次都先走光。）
        kids = sum(1 for v in self.villagers if v.role == "孩子")
        cand = []
        for v in self.villagers:
            if v.role == "孩子" and kids <= 1:
                continue
            cand.append((v, DEPART_WEIGHT.get(v.role, 0.6)))
        if not cand:
            return
        total = sum(w for _, w in cand)
        r = self.rng.random() * total
        who = cand[-1][0]
        for v, w in cand:
            r -= w
            if r <= 0:
                who = v
                break
        line = self.rng.choice(events.DEPART_LINES) % who.name
        warm = self.rng.choice(events.DEPART_WARM) % who.name
        self.log(line)
        self.log(warm)
        self.llm_remember(line)
        # 关系最好的人在路口站一会儿
        best_id, best_val = None, -1.0
        for oid, val in who.rel.items():
            if val > best_val and self.villagers_by_id.get(oid):
                best_val, best_id = val, oid
        if best_id is not None:
            o = self.villagers_by_id.get(best_id)
            if o is not None:
                o.say_line("路上小心", 3.4)
                self.highlights.append([o.x, o.y - 12.0, "heart", 2.4])
        self.villagers.remove(who)
        self.departures += 1
        # 别人对他的记忆也清掉，免得存档里挂着不存在的人
        for v in self.villagers:
            v.rel.pop(who.vid, None)
        self._llm_say_pending.discard(who.vid)
        self._llm_decide_pending.discard(who.vid)
        self._llm_free_decided.discard(who.vid)
        return who

    def start_migration(self, kind=None, text=None):
        """让一群动物从小镇经过。"""
        if text is None or kind is None:
            k, t = events.MIGRATIONS.get(self.season, ("birds", "一群鸟从镇子上头飞过"))
            kind = kind or k
            text = text or t
        self.migrations.append({
            "kind": kind,
            "t": 0.0,
            "life": self.rng.uniform(22.0, 32.0),
            "y": self.rng.uniform(26.0, 86.0),
            "vx": self.rng.uniform(11.0, 19.0),
            "seed": self.rng.randrange(100000),
            "n": self.rng.randint(5, 9),
        })
        after = self.rng.choice(events.MIGRATION_AFTER)
        line = "%s。%s" % (text, after)
        self.log(line)
        self.llm_remember(text)
        return self.migrations[-1]

    def _maybe_migration(self):
        if self.migrations:
            return
        if self.rng.random() < MIGRATION_CHANCE:
            self.start_migration()

    def _random_event(self):
        """每天可能发生一件小事。全是好事或者无所谓的事。

        刚发生过的几天内不再抽到 —— 否则会出现"野猫连着两天溜进小镇"。
        """
        if self.rng.random() > EVENT_CHANCE:
            return None
        pool = [e for e in events.RANDOM_EVENTS
                if e[1] not in self._recent_events]
        if not pool:
            pool = list(events.RANDOM_EVENTS)
        total = sum(w for w, _, _ in pool)
        r = self.rng.random() * total
        for w, text, fx in pool:
            r -= w
            if r <= 0:
                self.log(text)
                self.llm_remember(text)
                if fx:
                    self.fx[fx] = self.rng.uniform(5.0, 9.0)
                self._recent_events.append(text)
                if len(self._recent_events) > EVENT_COOLDOWN:
                    self._recent_events.pop(0)
                return text
        return None

    def _advance_visuals(self, dt_hours):
        """推进小效果和动物群的进度。这段纯视觉，不影响任何模拟数值。"""
        if self.fx:
            for k in list(self.fx):
                self.fx[k] -= dt_hours
                if self.fx[k] <= 0:
                    del self.fx[k]
        if self.migrations:
            dt_real = dt_hours / self.hours_per_second
            for m in self.migrations:
                m["t"] += dt_real
            self.migrations = [m for m in self.migrations if m["t"] < m["life"]]

    def _on_festival_start(self):
        name = self.festival
        if not name:
            return
        self.festival_days += 1
        self.log(events.festival_start(name))
        self.log(events.festival_scene(self.season, self.day))
        self.llm_remember("今天是%s" % name)
        # 节日是全镇共同的记忆，每个人都记得
        for v in self.villagers:
            self._mem(v, "今天是%s" % name, "event")
        if self.festival_days == 1:
            self._ai_note("festival", name)

    # ==================================================================
    # 成长队列
    # ==================================================================
    def _make_growth_queue(self):
        rng = random.Random(self.seed ^ 0x5EED)
        unlock, build, upgrade = [], [], []
        for l in self.world.lots:
            if not l["unlocked"]:
                unlock.append(("unlock", l["id"], 0))
            if l["level"] < 1:
                build.append(("build", l["id"], 1))
            for lvl in (2, 3):
                if l["level"] < lvl:
                    upgrade.append(("upgrade", l["id"], lvl))
        for g in (unlock, build, upgrade):
            rng.shuffle(g)
        # 轮着来：先划地、再盖房、然后翻新，交替推进，看得出节奏
        out = []
        while unlock or build or upgrade:
            for g in (unlock, build, upgrade):
                if g:
                    out.append(g.pop(0))
        return out

    def _do_growth(self):
        """推进一次成长。返回 (kind, 描述, 新村民) 或 None。"""
        while self.queue_index < len(self.queue):
            kind, lot_id, lvl = self.queue[self.queue_index]
            self.queue_index += 1
            lot = self.lot(lot_id)
            if lot is None:
                continue
            if kind == "unlock":
                if lot["unlocked"]:
                    continue
                lot["unlocked"] = True
                return ("unlock", "有人在镇子边上丈量土地，像是要盖新房子", None)
            if kind == "build":
                if lot["level"] >= 1 or not lot["unlocked"]:
                    continue
                lot["level"] = 1
                self.world._rebuild_walk()
                self._settle_villagers()
                newbie = None
                if lot["is_house"]:
                    newbie = self.add_villager(home=lot["id"])
                    return ("build", events.build_line(lot["kind"]) +
                            "，" + events.new_neighbor_line(newbie.name), newbie)
                return ("build", events.build_line(lot["kind"]), None)
            if kind == "upgrade":
                if lot["level"] >= lvl:
                    continue
                lot["level"] = lvl
                self.world._rebuild_walk()
                self._settle_villagers()
                return ("upgrade", events.upgrade_line(lot["kind"], lvl), None)
        return None

    # ==================================================================
    # 日志
    # ==================================================================
    def log(self, text):
        self.log_lines.append((self.day, round(self.hour, 1), text))
        if len(self.log_lines) > 200:
            self.log_lines = self.log_lines[-200:]

    def recent_lines(self, n=6):
        return self.log_lines[-n:]

    # ==================================================================
    # 行走目标
    # ==================================================================
    def _random_near(self, tile, radius):
        w = self.world
        for _ in range(60):
            tx = tile[0] + self.rng.randint(-radius, radius)
            ty = tile[1] + self.rng.randint(-radius, radius)
            if w.walkable(tx, ty):
                return (tx * TILE + 4, ty * TILE + 4)
        tx, ty = w.nearest_walkable(tile[0], tile[1])
        return (tx * TILE + 4, ty * TILE + 4)

    def _prop_tile(self, kind):
        w = self.world
        for p in w.props:
            if p["kind"] == kind:
                return (p["x"] // TILE, max(0, p["y"] // TILE - 1))
        return None

    @property
    def plaza_tile(self):
        if not hasattr(self, "_plaza"):
            w = self.world
            pts = [(x, y) for y in range(len(w.tiles)) for x in range(len(w.tiles[0]))
                   if w.tiles[y][x] == "plaza"]
            if pts:
                self._plaza = (sum(p[0] for p in pts) // len(pts),
                               sum(p[1] for p in pts) // len(pts))
            else:
                self._plaza = (len(w.tiles[0]) // 2, self.world.street_row)
        return self._plaza

    def _door_tile(self, lot):
        """建筑门前的落脚格，和 world.door_px / _carve_connectors 保持一致。"""
        x0, y0, x1, y1 = lot["rect"]
        return (((x0 + x1) // 2) // TILE, (y1 - 1) // TILE + 1)

    def _work_tile(self, v):
        w = self.world
        if v.role == "渔夫" and w.dock:
            return (w.dock["tx"] + 1, w.dock["ty"])
        if v.work:
            lot = self.lot(v.work)
            if lot:
                return self._door_tile(lot)
        return self.plaza_tile

    def home_lot(self, v):
        """村民的住处：没有固定房子就安排到已有的民居里（大家合住）。"""
        lot = self.lot(v.home) if v.home else None
        if lot is not None and lot["level"] > 0:
            return lot
        houses = [l for l in self.world.lots if l["is_house"] and l["level"] > 0]
        if houses:
            return houses[v.vid % len(houses)]
        return None

    def _spot_tile(self, v, kind):
        if kind == "home":
            lot = self.home_lot(v)
            if lot:
                return self._door_tile(lot)
            return self.plaza_tile
        if kind == "work":
            return self._work_tile(v)
        if kind == "plaza":
            return self._random_tile_near(self.plaza_tile, 4)
        if kind == "festival":
            return self._random_tile_near(self.plaza_tile, 7)
        if kind == "well":
            t = self._prop_tile("well")
            return self._random_tile_near(t, 3) if t else self.plaza_tile
        if kind == "bench":
            t = self._prop_tile("bench")
            return self._random_tile_near(t, 3) if t else self.plaza_tile
        if kind == "farm":
            if self.world.farms:
                f = self.rng.choice(self.world.farms)
                return (f["tx"] + self.rng.randrange(f["w"]),
                        f["ty"] + self.rng.randrange(f["h"]))
            return self.plaza_tile
        if kind == "dock":
            d = self.world.dock
            if d:
                return (d["tx"] + self.rng.randrange(1, max(2, d["len"])), d["ty"])
            return self.plaza_tile
        # wander
        tx, ty = self.world.random_walkable(self.rng)
        return (tx, ty)

    def _random_tile_near(self, tile, radius):
        px, py = self._random_near(tile, radius)
        return (px // TILE, py // TILE)

    def _go(self, v, kind):
        tx, ty = self._spot_tile(v, kind)
        px, py = tx * TILE + 4, ty * TILE + 4
        return v.set_goal(self.world, px, py)

    # ==================================================================
    # 作息
    # ==================================================================
    def _desired(self, v, h):
        child = (v.role == "孩子")
        # 节日：手上的活全停，大家聚在广场
        if self.festival:
            if h < 6.0:
                return "sleep", "home"
            if h < 8.5:
                return "eat", "home"
            if h < 12.0:
                return "festival", "plaza"
            if h < 14.0:
                return "lunch", "plaza"          # 会餐
            if h < 18.0:
                return "festival", "plaza"
            if h < 22.0:
                return "social", "plaza"         # 夜市
            if h < 23.0:
                return "home", "home"
            return "sleep", "home"
        if h < 6.0:
            return "sleep", "home"
        if h < 7.0:
            return "eat", "home"
        if child:
            if h < 12.0:
                return "play", "wander"
            if h < 13.0:
                return "lunch", "well"
            if h < 17.5:
                return "play", "wander"
        else:
            spot = V.ROLE_SPOT.get(v.role, "work")
            if h < 12.0:
                return "work", spot
            if h < 13.0:
                return "lunch", "well"
            if h < 17.5:
                return "work", spot
        if h < 19.0:
            # 空闲时段：模型如果有建议就按建议走（他仍然是"空闲"，作息表没被打破）
            if v.ai_action and (self.t - v.ai_action_t) < 8.0:
                return "free", ACTION_SPOT.get(v.ai_action, "wander")
            return "free", ("bench" if self.rng.random() < 0.5 else "wander")
        if h < 21.5:
            return "social", "plaza"
        if h < 22.5:
            return "home", "home"
        return "sleep", "home"

    def _update_villager(self, v, dt_hours, dt_real):
        act, spot = self._desired(v, self.hour)
        need_new = (act != v.activity) or (v.retry_at is not None and self.t >= v.retry_at)
        if need_new:
            changed = (act != v.activity)
            # 先把活动落定，再问模型。
            # 顺序反过来的话，请求里带的情境还是旧活动，
            # 等结果回来时活动已经变了，会被"情境已变"检查整批丢掉 —— 而且是静默的。
            v.activity = act
            v.retry_at = None
            v.chat_with = None
            v.hidden = False
            if changed:
                if act == "free":
                    self._ask_free_decision(v)     # 每天每人只问一次
                else:
                    v.ai_action = None             # 换活动了，旧决定作废
            act, spot = self._desired(v, self.hour)     # 决定可能刚落下来，重新取一次
            if self._go(v, spot):
                v.state = "go:" + act
            else:
                # 一时走不过去，就地待着，过几个游戏小时再试
                v.path = []
                v.retry_at = self.t + 3.0
                v.state = act

        arrived = (not v.path)
        if arrived and v.state.startswith("go:"):
            v.state = act
            if act == "sleep":
                v.hidden = True
            elif act == "work" and self.rng.random() < 0.10:
                # 到岗时偶尔说一句（AI 在就用 AI 的，不在就用内置的）
                self.say_with_ai(v, events.work_line(v.name, v.role))

        # 吃早饭和吃午饭是**真的在吃**：饱腹会回升。
        # 否则饱腹只减不增，几天后全镇都是 0，AI 台词会句句喊饿。
        v.tick_needs(dt_hours, sleeping=(act == "sleep"),
                     eating=(act in ("eat", "lunch")))
        if v.say_t > 0:
            v.say_t -= dt_real
            if v.say_t <= 0:
                v.say = ""

        if v.hidden:
            return
        v._step_move(dt_real)

        if act in ("social", "festival"):
            self._socialize(v)

    def _socialize(self, v):
        for o in self.villagers:
            if o is v or o.hidden:
                continue
            if abs(o.x - v.x) < 15 and abs(o.y - v.y) < 13:
                if v.chat_with == o.vid:
                    return
                v.chat_with = o.vid
                before = v.rel_level(o)
                v.meet(o, amount=2.2)
                after = v.rel_level(o)
                # 日常搭话也记 —— 这是最高频、也最私人的素材。
                # 只记"聊过"太笼统，带上对方名字才有检索价值；
                # 用概率控制频率，否则记忆表会被"和某某聊天"刷满。
                if self.rng.random() < 0.22:
                    self._mem(v, "和%s聊了几句" % o.name, "talk")
                if after != before and after in ("朋友", "挚友", "伴侣"):
                    self.log(events.meet_line(v.name, o.name, after))
                    self.llm_remember("%s和%s成了%s" % (v.name, o.name, after))
                    # 关系变化双方各记一条：记忆本来就是各记各的
                    self._mem(v, "和%s成了%s" % (o.name, after), "rel")
                    self._mem(o, "和%s成了%s" % (v.name, after), "rel")
                    # 关系升温是最值得让模型说一句的时刻
                    if not self.say_with_ai(v, "今天真开心"):
                        pass
                    # 必须是 list：tick() 里要就地衰减，tuple 会抛 TypeError
                    self.highlights.append([(v.x + o.x) / 2.0, min(v.y, o.y) - 12.0,
                                            "heart", 2.4])
                    if len(self.highlights) > 40:
                        self.highlights = self.highlights[-40:]
                return
        v.chat_with = None

    # ==================================================================
    # 本地大模型
    # ==================================================================
    @property
    def villagers_by_id(self):
        return {v.vid: v for v in self.villagers}

    def setup_llm(self, enabled=True, model=None, base=None):
        """接上本地大模型。失败也没关系 —— 所有调用点都有内置台词兜底。"""
        if model:
            self.ai_model = model
        if base:
            self.ai_base = base
        if self.llm is None:
            self.llm = LLM.LLMClient(base_url=self.ai_base,
                                     model=self.ai_model, enabled=enabled)
        else:
            self.llm.base_url = self.ai_base
            self.llm.model = self.ai_model
            self.llm.set_enabled(enabled)
        if enabled:
            # 预热：第一次调用要加载权重（实测 9B 约 59 秒），
            # 提前触发，免得村民第一次开口要等一分钟
            self.llm.submit("warmup", "你好", meta={"vid": -1}, num_predict=8)
        return self.llm

    def set_ai(self, on):
        if self.llm is None:
            self.setup_llm(enabled=on)
        else:
            self.llm.set_enabled(on)
            if on:
                self.llm.submit("warmup", "你好", meta={"vid": -1}, num_predict=8)

    @property
    def ai_on(self):
        return bool(self.llm is not None and self.llm.enabled)

    def ai_status(self):
        if self.llm is None:
            return {"enabled": False, "state": "off", "detail": "",
                    "model": self.ai_model, "queued": 0, "stats": {}}
        return self.llm.status()

    def llm_remember(self, text):
        """记一条最近大事，之后会作为上下文喂给模型，让它说的话不离谱。"""
        self.llm_events.append(text)
        if len(self.llm_events) > 8:
            self.llm_events = self.llm_events[-8:]

    def _mem(self, v, text, kind="event"):
        """往某个村民的个人记忆里写一条（带时间）。谁在场谁才记得。"""
        if v is None:
            return
        try:
            v.remember(self.day, self.hour, text, kind)
        except Exception:
            pass

    def _mem_pair(self, a, b, text, kind="rel"):
        """两个人之间的事，双方各记一条 —— 记忆本来就是各记各的。"""
        self._mem(a, text, kind)
        self._mem(b, text, kind)

    def _ai_note(self, tag, raw):
        """留几条模型原始输出，方便事后查“为什么没生效”。"""
        self.ai_raw.append((tag, (raw or "")[:120]))
        if len(self.ai_raw) > 12:
            self.ai_raw = self.ai_raw[-12:]

    def chat(self, text):
        """AI 说的话单独记一份：正文日志留给小镇事件，不被打字刷屏。"""
        self.chat_log.append((self.day, round(self.hour, 1), text))
        if len(self.chat_log) > 240:
            self.chat_log = self.chat_log[-240:]

    def _ai_budget_ok(self):
        if self._llm_day != self.day:
            self._llm_day = self.day
            self.llm_calls_today = 0
            self._llm_free_decided.clear()
        return self.llm_calls_today < AI_DAILY_BUDGET

    def say_with_ai(self, v, fallback=None):
        """让一个村民说句话。

        能问模型就问模型；问不了（关了 / 忙 / 超预算 / 连不上）就用内置台词。
        这个方法永远不阻塞，最坏情况是这一帧什么都不做。

        每次随机挑一种说话风格（平常 / 语序颠倒 / 一本正经说废话 …），
        否则模型会连续吐出同一类句子，看两天就腻了。
        """
        if self.ai_on and v.vid not in self._llm_say_pending and self._ai_budget_ok():
            style = LLM.pick_style(self.rng, self.ai_goofy)
            try:
                prompt = LLM.villager_persona(self, v, rng=self.rng)
            except Exception:
                prompt = None
            if prompt and self.llm.submit(
                    "say", prompt,
                    meta={"vid": v.vid, "activity": v.activity,
                          "style": style["key"]},
                    system=LLM.talk_system(style)):
                self._llm_say_pending.add(v.vid)
                self.llm_calls_today += 1
                return True
        if fallback:
            v.say_line(fallback, 3.0)
        return False

    def _ask_free_decision(self, v):
        """空闲时段让模型在有限选项里挑一个去哪。

        注意这里**不改变作息表**：他仍然是"空闲"，
        只是由模型决定往哪边走。这样既有自主感，又不会破坏小镇的规律。
        """
        if not self.ai_on or v.vid in self._llm_decide_pending:
            return
        if v.vid in self._llm_free_decided or not self._ai_budget_ok():
            return
        options = LLM.free_options(v.role)
        try:
            prompt = LLM.decide_prompt(self, v, options)
        except Exception:
            return
        if self.llm.submit("decide", prompt,
                           meta={"vid": v.vid, "activity": v.activity},
                           system=LLM.DECIDE_SYSTEM, num_predict=80):
            self._llm_decide_pending.add(v.vid)
            self._llm_free_decided.add(v.vid)
            self.llm_calls_today += 1

    def poll_llm(self):
        """主循环每帧调用：把后台线程算好的结果应用到村民身上。

        所有"等回来发现情况已经变了"的请求都要丢掉 ——
        半分钟前的一句台词硬塞到现在，只会显得莫名其妙。
        """
        if self.llm is None:
            return
        by_id = self.villagers_by_id
        for r in self.llm.drain():
            kind, meta = r["kind"], r.get("meta") or {}
            text = r.get("text") or ""
            vid = meta.get("vid")

            if kind == "warmup":
                continue

            if kind == "say":
                self._llm_say_pending.discard(vid)
                v = by_id.get(vid)
                if v is None or v.hidden:
                    continue
                if meta.get("activity") != v.activity:
                    continue                      # 情境已经变了，别硬塞
                line = LLM.sanitize_line(text, 24)
                if line:
                    v.say_line(line, 3.6)
                    self.chat("%s：%s" % (v.name, line))
                    self.ai_said += 1
                    # 自己说过的话也进记忆：下次开口才不会重复同样的感慨
                    self._mem(v, "我说过「%s」" % line, "talk")
                    sk = meta.get("style") or "?"
                    self.ai_styles[sk] = self.ai_styles.get(sk, 0) + 1
                else:
                    self._ai_note("say-reject", text)
                continue

            if kind == "decide":
                self._llm_decide_pending.discard(vid)
                v = by_id.get(vid)
                if v is None or v.hidden:
                    continue
                if meta.get("activity") != v.activity:
                    continue
                action, why = LLM.parse_decision(text, set(ACTION_SPOT),
                                                 LLM.DECISION_OPTIONS)
                if not action:
                    # 静默失败最要命：记下来，不然只知道"决策没生效"
                    self.ai_decide_rejected += 1
                    self._ai_note("decide-reject", text)
                    continue
                v.ai_action = action
                v.ai_why = why
                v.ai_action_t = self.t
                self.ai_decisions += 1
                # 让他立刻改道，而不是等下一个活动切换
                v.retry_at = self.t
                if why:
                    v.say_line(why, 3.0)
                    self.chat("%s（%s）：%s" % (v.name, action, why))
                continue

            if kind == "narrate":
                line = LLM.sanitize_line(text, 60)
                if line:
                    self.log("（旁白）" + line)
                    self.llm_remember(line)
                    self.ai_narrations += 1
                else:
                    # 静默失败最要命：记下来，否则只知道"旁白没出来"
                    self._ai_note("narrate-reject", text)
                continue

    def _ai_ambient(self, dt_hours):
        """隔一阵子挑一个在场的村民说句话，让小镇不总是那么安静。"""
        if not self.ai_on:
            return
        self._llm_ambient_t += dt_hours
        if self._llm_ambient_t < AI_AMBIENT_HOURS:
            return
        self._llm_ambient_t = 0.0
        pool = [v for v in self.villagers
                if not v.hidden and v.activity not in ("sleep", "home", None)
                and v.vid not in self._llm_say_pending]
        # 别让同一个人连着说两次：实测会变成"豆豆饿、豆豆还是饿"
        if len(pool) > 1:
            pool = [v for v in pool if v.vid != self._llm_last_speaker] or pool
        if not pool:
            return
        pick = self.rng.choice(pool)
        self._llm_last_speaker = pick.vid
        self.say_with_ai(pick, None)

    def _ask_narration(self, day_lines):
        if not self.ai_on:
            self._ai_note("narrate-skip", "ai off")
            return
        if not self._ai_budget_ok():
            self._ai_note("narrate-skip", "budget")
            return
        try:
            prompt = LLM.narrate_prompt(self, day_lines)
        except Exception as exc:
            self._ai_note("narrate-prompt-err", repr(exc))
            return
        nkey, nhint = LLM.pick_narrate_style(self.rng, self.ai_goofy)
        if self.llm.submit("narrate", prompt,
                           meta={"day": self.day, "style": nkey},
                           system=LLM.narrate_system(nhint, self.ai_goofy),
                           num_predict=120):
            self.llm_calls_today += 1
            self.ai_narrate_submitted += 1
            self.ai_styles["nar:" + nkey] = self.ai_styles.get("nar:" + nkey, 0) + 1
        else:
            # 队列满 / 退避中 —— 必须记下来，不然就是"一整天没有旁白"且不知道为什么
            self._ai_note("narrate-submit-fail",
                          "qsize=%d retry_after=%.1f"
                          % (self.llm._q.qsize(),
                             max(0.0, self.llm._retry_after - time.time())))

    # ==================================================================
    # 作物 / 天气 / 季节
    # ==================================================================
    def _grow_crops(self, dt_hours):
        step = dt_hours / CROP_HOURS_PER_STAGE
        for f in self.world.farms:
            for c in f["cells"]:
                if c["stage"] >= 4:
                    # 熟透了就先留着（一片金黄很好看），之后才收掉重种
                    if self.rng.random() < step * 0.35:
                        c["stage"] = 0
                        c["grow"] = 0.0
                    continue
                c["grow"] += step * (0.8 + 0.4 * c["grow"])
                if c["grow"] >= 1.0:
                    c["grow"] = 0.0
                    c["stage"] += 1
        # 收成消息只是点缀，三天最多一条，否则会把成长事件淹掉
        if (self.day - self._crop_log_day >= 3 and any(
                c["stage"] >= 4 for f in self.world.farms for c in f["cells"])):
            who = next((v for v in self.villagers if v.role == "农夫"), None)
            if who and self.rng.random() < 0.4:
                self._crop_log_day = self.day
                self.log(events.crop_line(who.name))

    def _pick_weather(self):
        season = self.season
        if season == 3:
            pool = ["clear", "cloudy", "snow", "snow"]
        elif season == 2:
            pool = ["clear", "clear", "cloudy", "rain"]
        else:
            pool = ["clear", "clear", "cloudy", "rain"]
        return self.rng.choice(pool)

    def _on_new_day(self):
        # 天气
        if self.rng.random() < 0.45:
            self.weather = self._pick_weather()
            self.log(events.weather_line(self.weather))
        # 季节
        if (self.day - 1) % SEASON_DAYS == 0:
            self.log(events.season_line(self.season, self.day // SEASON_DAYS))
        # 昨天的旁白：把昨天真实发生的事交给模型写成一句话。
        # **必须排掉（旁白）本身** —— 否则模型会把自己前一天写的话
        # 当成"今日事件"，然后照抄自己，一天天越写越像。
        yesterday = self.day - 1
        day_lines = [t for (d, h, t) in self.log_lines
                     if d == yesterday and not t.startswith("（旁白）")]
        self._ask_narration(day_lines)
        # 节日 / 随机小事 / 动物迁徙
        self._on_festival_start()
        self._random_event()
        self._maybe_migration()

    # ==================================================================
    # 推进
    # ==================================================================
    def advance(self, dt_hours, detailed=True):
        remaining = dt_hours
        guard = 0
        while remaining > 1e-6 and guard < 20000:
            guard += 1
            hour = self.t % 24.0
            to_boundary = 24.0 - hour if hour > 1e-6 else 24.0
            step = min(remaining, to_boundary)
            if detailed:
                # 村民的移动和需求必须用小步长推，否则跨天时会瞬移
                step = min(step, 0.5)
            prev_day = self.day
            self.t += step
            remaining -= step

            if detailed:
                dt_real = step / self.hours_per_second
                for v in self.villagers:
                    self._update_villager(v, step, dt_real)
                # 让小镇不总是那么安静；补算时跳过，免得一次性灌一堆请求
                self._ai_ambient(step)
            self._grow_crops(step)
            self._advance_visuals(step)
            if self.day != prev_day:
                self._on_new_day()
            # 一天推进两次成长（早上 6 点、傍晚 6 点），看得到变化又不会太快
            while self.t >= self._next_growth_t:
                res = self._do_growth()
                if res:
                    self.log(res[1])          # 成长事件必须写进日志，这是"养成"的回报
                    # 只让"此刻在外面看得见"的人记住这件事。
                    # 全镇广播会让每个人的记忆长得一模一样，
                    # 十二个人就变成一个复读机 —— 那正是记忆流最该避免的事。
                    for v in self.villagers:
                        if not v.hidden:
                            self._mem(v, res[1], "move")
                    # 记忆攒够了就压一句自我认知，供 persona 使用
                    for v in self.villagers:
                        try:
                            v.reflect(self.day, self.hour)
                        except Exception:
                            pass
                # 人口结算跟着成长节奏走：房子多了就该有人搬来
                self._population_step()
                self._next_growth_t += 12.0

    def _settle_villagers(self):
        """建筑盖起来后地面可能变得不可走，把人挪出来 / 让他们重新找路。

        两种情况都要处理：
          1. 人正站在刚被占用的格子上
          2. 人还在路上，但**前方路线**被新房子挡住了 —— 只管当前格子的话，
             村民会径直穿墙走进刚盖好的房子里
        """
        w = self.world
        for v in self.villagers:
            tx, ty = v.tile
            if not w.walkable(tx, ty):
                nx, ny = w.nearest_walkable(tx, ty)
                v.x, v.y = nx * TILE + 4, ny * TILE + 4
                v.path = []
                v.retry_at = self.t + 0.4
                continue
            if v.path:
                for (px, py) in v.path:
                    if not w.walkable(int(px) // TILE, int(py - 1) // TILE):
                        v.path = []
                        v.retry_at = self.t + 0.4
                        break

    def _resync_growth_clock(self):
        import math as _m
        k = _m.floor((self.t - 6.0) / 12.0) + 1
        self._next_growth_t = 6.0 + k * 12.0

    def set_activity(self, snap):
        """由外层（app）注入一帧外部活动快照。缺字段就当没开。"""
        if not isinstance(snap, dict):
            return
        self.activity = {
            "enabled": bool(snap.get("enabled", False)),
            "level": float(snap.get("level", 0.0) or 0.0),
            "busy": bool(snap.get("busy", False)),
            "note": str(snap.get("note") or "")[:40],
        }

    def _activity_reactions(self):
        """机器在忙 → 镇上的工匠也说一句。这条是"陪着你上班"的全部意思。

        刻意做得很轻：只在日志里多说两句话，外加给对方记一条记忆。
        **不改变作息表、不改变移动速度** —— 小镇的节奏不该被你的
        CPU 占用率绑架，机器再忙农民也不会半夜下地。
        """
        a = self.activity
        if not a.get("enabled"):
            return
        if not (a.get("busy") or a.get("note")):
            return
        # 别一直刷屏：同一条"忙"的播报至少隔 3 个游戏小时
        if self.t - self._activity_said_t < 3.0:
            return
        workers = [v for v in self.villagers
                   if not v.hidden and v.activity == "work"
                   and v.role in ("铁匠", "木匠", "织工", "农夫", "渔夫")]
        if not workers:
            return
        v = self.rng.choice(workers)
        note = a.get("note") or ""
        if note:
            line = events.activity_note_line(v.name, v.role, note)
        else:
            line = events.activity_busy_line(v.name, v.role)
        if line:
            self.log(line)
            self._mem(v, line, "mood")
            self._activity_said_t = self.t

    def tick(self, dt_real):
        dt_real = max(0.0, min(dt_real, 5.0))
        self.advance(dt_real * self.hours_per_second, detailed=True)
        # 收取后台线程算好的模型输出（绝不在渲染线程里等）
        self.poll_llm()
        # 偶发的生活碎片。AI 开着的时候就少写点内置台词，
        # 免得正文日志被两种语气混在一起。
        self._ambient_t += dt_real * self.hours_per_second
        if self._ambient_t > 1.6:
            self._ambient_t = 0.0
            chance = 0.08 if self.ai_on else 0.30
            if self.rng.random() < chance and self.villagers:
                v = self.rng.choice(self.villagers)
                if not v.hidden and not v.say:
                    self.log(self.rng.choice(events.idle_lines(v.name)))
        for h in self.highlights:
            h[3] -= dt_real
        self.highlights = [h for h in self.highlights if h[3] > 0]
        # 真实电脑忙的时候，镇上的工匠也跟着忙（关掉就什么都不发生）
        self._activity_reactions()

    # ==================================================================
    # 存档
    # ==================================================================
    def to_dict(self):
        return {
            "version": SAVE_VERSION,
            "seed": self.seed,
            "t": self.t,
            "weather": self.weather,
            "queue_index": self.queue_index,
            "last_seen": time.time(),
            "ai": {"enabled": self.ai_on, "model": self.ai_model,
                   "base": self.ai_base, "goofy": self.ai_goofy},
            # next_vid 必须存：读档时重置成 0 的话，
            # 新搬来的人会和存档里的老村民 id 撞车
            "pop": {"next_vid": self._next_vid, "arrivals": self.arrivals,
                    "departures": self.departures,
                    "festival_days": self.festival_days},
            "ai_diag": {"said": self.ai_said, "decisions": self.ai_decisions,
                        "decide_rejected": self.ai_decide_rejected,
                        "narrations": self.ai_narrations,
                        "calls_today": self.llm_calls_today,
                        "styles": dict(self.ai_styles),
                        "raw": self.ai_raw[-8:]},
            "chat_log": self.chat_log[-80:],
            "llm_events": self.llm_events[-8:],
            "lots": {l["id"]: {"level": l["level"], "unlocked": l["unlocked"]}
                     for l in self.world.lots},
            "farms": [[c["stage"] for c in f["cells"]] for f in self.world.farms],
            "villagers": [v.to_dict() for v in self.villagers],
            "log": self.log_lines[-60:],
        }

    def save(self, path=None):
        path = path or self.save_path
        if not path:
            return False
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return True

    @classmethod
    def load(cls, path, seed=20240607, catch_up=True):
        """读档。

        catch_up=False 时**不做**离线补算，交给调用方在接上大模型之后再补。
        否则补算期间 self.llm 还是 None，那几天的旁白会被整批跳过 ——
        而"你不在的时候发生了什么"恰恰是最该让模型写一句的地方。
        """
        with open(path, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        sim = cls(seed=d.get("seed", seed), save_path=path)
        sim.t = float(d.get("t", 24.0))
        sim.weather = d.get("weather", "clear")
        sim.queue_index = int(d.get("queue_index", 0))
        sim.log_lines = [tuple(x) for x in d.get("log", [])]
        sim.chat_log = [tuple(x) for x in d.get("chat_log", [])]
        sim.llm_events = list(d.get("llm_events") or [])
        ai = d.get("ai") or {}
        sim.ai_model = ai.get("model") or LLM.DEFAULT_MODEL
        sim.ai_base = ai.get("base") or LLM.DEFAULT_BASE
        sim.ai_goofy = int(ai.get("goofy", 2))
        sim._ai_was_enabled = bool(ai.get("enabled", False))
        pop = d.get("pop") or {}
        sim.arrivals = int(pop.get("arrivals", 0))
        sim.departures = int(pop.get("departures", 0))
        sim.festival_days = int(pop.get("festival_days", 0))
        sim._resync_growth_clock()

        lots = d.get("lots") or {}
        for l in sim.world.lots:
            if l["id"] in lots:
                l["level"] = int(lots[l["id"]].get("level", l["level"]))
                l["unlocked"] = bool(lots[l["id"]].get("unlocked", l["unlocked"]))
        sim.world._rebuild_walk()

        farms = d.get("farms") or []
        for f, stages in zip(sim.world.farms, farms):
            for c, s in zip(f["cells"], stages):
                c["stage"] = int(s)

        # 村民：按存档重建，数量对不上就补齐
        saved = d.get("villagers") or []
        sim.villagers = []
        for i, vd in enumerate(saved):
            home = vd.get("home")
            role = vd.get("role", "村民")
            work = vd.get("work")
            v = V.Villager(vd.get("vid", i), vd.get("name", "村民"),
                           role, home=home, work=work,
                           skin=vd.get("skin", 0), hair=vd.get("hair", 0),
                           cloth=vd.get("cloth", 0), pants=vd.get("pants", 0),
                           adult=vd.get("adult", True))
            v.apply_dict(vd)
            sim.villagers.append(v)
        # id 计数器要接上：取「存档里记的」和「现有最大 vid+1」的较大者。
        # 只用其中一个都可能撞车 —— 搬走过人的存档里 max(vid)+1 会偏小。
        sim._next_vid = max(
            int(pop.get("next_vid", 0)),
            max([v.vid for v in sim.villagers] + [-1]) + 1,
        )
        while len(sim.villagers) < 3:
            sim.add_villager()

        # 离线补算
        last = float(d.get("last_seen", time.time()))
        sim.loaded_last_seen = last
        away = max(0.0, time.time() - last)
        summary = None
        if catch_up and away > 30.0:
            summary = sim.catch_up(away)
        return sim, summary

    def catch_up(self, real_seconds):
        """离线补算：最多 3 个游戏日，然后给一句总结。"""
        hours = min(real_seconds * self.hours_per_second, MAX_OFFLINE_DAYS * 24.0)
        before = {
            "day": self.day, "season": self.season,
            "built": sum(1 for l in self.world.lots if l["level"] > 0),
            "sum_level": sum(l["level"] for l in self.world.lots),
            "pop": len(self.villagers),
        }
        left = hours
        step = 0.25
        while left > 1e-6:
            d = min(step, left)
            self.advance(d, detailed=False)
            left -= d
        # 补算结束后把村民按当前作息"就位"，免得他们挤在昨天的地方
        for v in self.villagers:
            act, spot = self._desired(v, self.hour)
            v.activity = act
            tx, ty = self._spot_tile(v, spot)
            v.x, v.y = tx * TILE + 4, ty * TILE + 4
            v.path = []
            v.hidden = (act == "sleep")
        day = self.day
        after = {
            "built": sum(1 for l in self.world.lots if l["level"] > 0),
            "sum_level": sum(l["level"] for l in self.world.lots),
            "pop": len(self.villagers),
        }
        days = hours / 24.0
        built = max(0, after["built"] - before["built"])
        gained = max(0, after["sum_level"] - before["sum_level"]) - built
        arrivals = max(0, after["pop"] - before["pop"])
        text = events.offline_summary(days, built, gained, arrivals,
                                      self.season != before["season"])
        self.log("（离线）" + text)
        return text
