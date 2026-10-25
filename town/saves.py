# -*- coding: utf-8 -*-
"""像素小镇 · 存档与小镇模板管理

以前想换一座小镇，只能自己删 `save/town.json` —— 这对普通用户太不友好：
删错了就没了，也不知道有哪些"模板"可选。这个模块把它变成几件明确的事：

  * **列表**：看看现在有哪些存档 / 各自多少天、多少人、什么季节
  * **新建**：指定 seed 和名字，开一座新小镇（不动当前存档）
  * **切换**：把某个存档设为当前小镇
  * **备份**：一键存档快照，出事能退回来
  * **删除**：移到 `save/trash/` 而不是真删（可恢复）

设计原则
--------
  * 只做文件操作，不 import tkinter、不碰 sim 的运行时状态
  * 任何一步失败都要"什么都不改变"，不能留下半个存档
  * 删档走回收站，不直接 unlink
"""

import json
import os
import shutil
import time

SAVE_NAME = "town.json"


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


class SaveManager(object):
    def __init__(self, save_dir):
        self.save_dir = save_dir
        self.slots_dir = os.path.join(save_dir, "towns")
        self.backup_dir = os.path.join(save_dir, "backups")
        self.trash_dir = os.path.join(save_dir, "trash")
        self.current = os.path.join(save_dir, SAVE_NAME)

    # ---------------- 目录 ----------------
    def ensure_dirs(self):
        for d in (self.save_dir, self.slots_dir, self.backup_dir, self.trash_dir):
            try:
                os.makedirs(d, exist_ok=True)
            except Exception:
                pass

    def slot_path(self, name):
        """把用户给的名字变成安全的文件名（中文保留，去掉路径分隔符等）。"""
        safe = "".join(c for c in str(name) if c not in '\\/:*?"<>|').strip()
        safe = safe.replace("..", ".")
        if not safe:
            safe = "town"
        return os.path.join(self.slots_dir, safe + ".json")

    # ---------------- 摘要 ----------------
    def summarize(self, path):
        """读一个存档，抽出"这镇长什么样"的摘要。坏了就返回 None。"""
        d = _read_json(path)
        if not isinstance(d, dict) or "t" not in d:
            return None
        t = float(d.get("t") or 0.0)
        pop = d.get("pop") or {}
        villagers = d.get("villagers") or []
        memo = 0
        for v in villagers:
            if isinstance(v, dict):
                memo += len(v.get("mem") or [])
        return {
            "path": path,
            "name": os.path.splitext(os.path.basename(path))[0],
            "seed": d.get("seed"),
            "day": int(t // 24) + 1,
            "hour": round(t % 24, 1),
            "season": ("春", "夏", "秋", "冬")[int(t // 24 // 7) % 4],
            "pop": len(villagers),
            "arrivals": pop.get("arrivals", 0),
            "departures": pop.get("departures", 0),
            "memories": memo,
            "mtime": os.path.getmtime(path) if os.path.exists(path) else 0,
            "size": os.path.getsize(path) if os.path.exists(path) else 0,
        }

    def list_slots(self, include_current=True):
        """列出所有可切换的小镇（当前存档 + towns/ 下的模板）。

        注意：当前存档的文件名永远是 town.json，直接拿文件名当显示名
        会出现一个叫 "town" 的条目和「甲镇」「乙镇」并列，用户根本分不清
        哪个是自己正在玩的那个。所以这里把当前项标记成 is_current，
        并额外给出它来自哪个 towns/ 模板（source_name）。
        """
        out = []
        if include_current and os.path.exists(self.current):
            s = self.summarize(self.current)
            if s:
                s["is_current"] = True
                s["source_name"] = None
                # 当前存档是某个模板的副本 —— 按 seed + 天数找回它的名字
                try:
                    for nm in os.listdir(self.slots_dir):
                        if not nm.endswith(".json"):
                            continue
                        t = self.summarize(os.path.join(self.slots_dir, nm))
                        if (t and t["seed"] == s["seed"]
                                and abs(t["day"] - s["day"]) <= 0):
                            s["source_name"] = t["name"]
                            break
                except Exception:
                    pass
                out.append(s)
        try:
            names = sorted(os.listdir(self.slots_dir))
        except Exception:
            names = []
        for nm in names:
            if not nm.endswith(".json"):
                continue
            p = os.path.join(self.slots_dir, nm)
            s = self.summarize(p)
            if s:
                s["is_current"] = False
                out.append(s)
        return out

    # ---------------- 操作 ----------------
    def create_slot(self, name, seed, payload):
        """把 payload（一个 sim.to_dict()）存成一个独立存档。

        先写临时文件再原子替换：中途失败不会留下半个存档。
        """
        self.ensure_dirs()
        p = self.slot_path(name)
        d = os.path.dirname(p)
        os.makedirs(d, exist_ok=True)
        tmp = p + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, p)
        except Exception:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
            raise
        return p

    def activate(self, name):
        """把某个 towns/<name>.json 设为当前小镇。

        当前存档会先备份，再整体替换 —— 切错了还能从 backups/ 退回来。
        **首次切换也必须备份**：那时候当前存档里可能是玩了几十天的镇子，
        没有备份直接覆盖，用户就再也找不回来了。
        """
        src = self.slot_path(name)
        if not os.path.exists(src):
            raise IOError("找不到这座小镇：%s" % name)
        self.ensure_dirs()
        if os.path.exists(self.current):
            self.backup(tag="before-%s" % name)
        tmp = self.current + ".tmp"
        shutil.copyfile(src, tmp)
        os.replace(tmp, self.current)
        return self.current

    def backup(self, tag=None):
        """给当前存档存一份快照。返回快照路径；没有存档时返回 None。"""
        if not os.path.exists(self.current):
            return None
        self.ensure_dirs()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        base = "%s_%s" % (stamp, tag) if tag else stamp
        dst = os.path.join(self.backup_dir, base + ".json")
        i = 1
        while os.path.exists(dst):
            dst = os.path.join(self.backup_dir, "%s_%d.json" % (base, i))
            i += 1
        shutil.copyfile(self.current, dst)
        return dst

    def remove_slot(self, name):
        """删一个小镇 —— 移进 trash/，不是真删。"""
        src = self.slot_path(name)
        if not os.path.exists(src):
            raise IOError("找不到这座小镇：%s" % name)
        self.ensure_dirs()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dst = os.path.join(self.trash_dir, "%s_%s.json" % (os.path.basename(src)[:-5], stamp))
        shutil.move(src, dst)
        return dst

    def prune_backups(self, keep=12):
        """备份只留最近 keep 份，免得目录无限长大。"""
        try:
            names = [n for n in os.listdir(self.backup_dir) if n.endswith(".json")]
        except Exception:
            return 0
        if len(names) <= keep:
            return 0
        names.sort(key=lambda n: os.path.getmtime(os.path.join(self.backup_dir, n)))
        removed = 0
        for nm in names[:len(names) - keep]:
            try:
                os.remove(os.path.join(self.backup_dir, nm))
                removed += 1
            except Exception:
                pass
        return removed
