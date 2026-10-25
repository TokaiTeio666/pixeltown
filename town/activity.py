# -*- coding: utf-8 -*-
"""像素小镇 · 外部活动映射

把**真实电脑上正在发生的事**映射成小镇里的动静。

为什么要有这个模块
------------------
小镇原本是一个封闭的模拟：它自己过日子，跟你桌上这台机器毫无关系。
但人盯着它看的时候，心里想的是"我在干活，它也在干活"。
把这两件事连起来，小镇才真的像一个陪你上班的邻居，
而不是一个和现实平行的小玩具。

怎么读信号
----------
只用标准库，绝不依赖 psutil：
  * CPU 占用：Windows 走 `GetSystemTimes`（ctypes），其他平台读 /proc 或 loadavg
  * 外部标记：`save/activity.txt`，一行文本，用户可以自己写
    （比如构建脚本里 echo building > save/activity.txt）

硬约束（和 llm.py 是同一套哲学）
--------------------------------
  * **绝不阻塞**：采样在后台线程里做，主循环只读一个已算好的值
  * **绝不报错**：读不到就当作"没活动"，静默降级
  * **可关闭**：关掉之后小镇一模一样地运转
"""

import ctypes
import os
import platform
import threading
import time

# 采样间隔（秒）。太密没意义，太疏反应慢。
SAMPLE_INTERVAL = 2.0
# 滞回：进入 busy 的门槛要比退出高，否则会在临界点上来回跳
BUSY_ENTER = 55.0
BUSY_EXIT = 25.0

_IS_WIN = platform.system() == "Windows"


# ----------------------------------------------------------------------
# CPU 占用（Windows 用 GetSystemTimes，不用额外依赖）
# ----------------------------------------------------------------------
class _WinCPU(object):
    """GetSystemTimes 差分。第一次调用没有基准，所以第一次总是返回 0。"""

    def __init__(self):
        self._prev = None
        self._fn = ctypes.windll.kernel32.GetSystemTimes

    def __call__(self):
        idle = ctypes.c_ulonglong()
        kern = ctypes.c_ulonglong()
        user = ctypes.c_ulonglong()
        if not self._fn(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user)):
            return 0.0
        cur = (idle.value, kern.value, user.value)
        if self._prev is None:
            self._prev = cur
            return 0.0
        d_idle = cur[0] - self._prev[0]
        d_kern = cur[1] - self._prev[1]
        d_user = cur[2] - self._prev[2]
        self._prev = cur
        # kern 已经包含 idle，所以总时间是 kern + user
        total = d_kern + d_user
        if total <= 0:
            return 0.0
        return max(0.0, min(100.0, 100.0 * (total - d_idle) / float(total)))


def _posix_cpu():
    try:
        with open("/proc/stat", "r") as fh:
            parts = fh.readline().split()
        vals = [int(x) for x in parts[1:]]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        total = sum(vals)
        _posix_cpu._prev = getattr(_posix_cpu, "_prev", None)
        prev = _posix_cpu._prev
        _posix_cpu._prev = (idle, total)
        if prev is None:
            return 0.0
        d_idle = idle - prev[0]
        d_total = total - prev[1]
        if d_total <= 0:
            return 0.0
        return max(0.0, min(100.0, 100.0 * (d_total - d_idle) / float(d_total)))
    except Exception:
        return 0.0


class ActivityMonitor(object):
    """后台低频采样"这台机器现在忙不忙"。主循环只读 self.busy / self.level。

    level 是 0..1 的平滑值；busy 是带滞回的布尔量。
    两者都能直接喂给 sim，用来决定工匠加不加班。
    """

    def __init__(self, save_dir=None, enabled=True, interval=SAMPLE_INTERVAL):
        self.enabled = bool(enabled)
        self.interval = float(interval)
        self.save_dir = save_dir
        self.level = 0.0
        self.busy = False
        self.note = ""              # activity.txt 里那行字，原样保留
        self._cpu = _WinCPU() if _IS_WIN else _posix_cpu
        self._thread = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    # ---------------- 后台 ----------------
    def start(self):
        if not self.enabled or self._thread is not None:
            return self
        t = threading.Thread(target=self._loop, name="pixeltown-activity", daemon=True)
        t.start()
        self._thread = t
        return self

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.sample_once()
            except Exception:
                # 采样失败绝不能让线程死掉，更不能冒到主循环去
                pass
            self._stop.wait(self.interval)

    # ---------------- 采样 ----------------
    def sample_once(self):
        """采一次。单独抽出来是为了让自检能同步调用，不必等线程。"""
        cpu = 0.0
        try:
            cpu = float(self._cpu() or 0.0)
        except Exception:
            cpu = 0.0
        note = self._read_note()
        target = max(0.0, min(1.0, cpu / 100.0))
        forced = bool(note)
        if forced:
            # 有外部标记就等于"明确在忙"，不靠 CPU 猜。
            # 这条必须**立即生效**：标记是用户主动写的
            #（构建脚本里 echo building > activity.txt），
            # 要是还等平滑爬坡，标记写完两秒小镇才反应过来就没意义了。
            target = 1.0
        with self._lock:
            # 指数平滑：别让一次尖峰就把全镇拉起来
            self.level = self.level * 0.7 + target * 0.3
            if forced:
                self.level = 1.0
                self.busy = True
            elif self.busy:
                self.busy = self.level * 100.0 > BUSY_EXIT
            else:
                self.busy = self.level * 100.0 > BUSY_ENTER
            self.note = note
        return self.level

    def _read_note(self):
        if not self.save_dir:
            return ""
        p = os.path.join(self.save_dir, "activity.txt")
        try:
            if not os.path.exists(p):
                return ""
            # 只看最近 5 分钟写过的东西，老文件不该一直让小镇加班
            if time.time() - os.path.getmtime(p) > 300:
                return ""
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                return (fh.readline() or "").strip()[:40]
        except Exception:
            return ""

    # ---------------- 给主循环 ----------------
    def snapshot(self):
        with self._lock:
            return {"enabled": self.enabled, "level": round(self.level, 3),
                    "busy": self.busy, "note": self.note}

    def work_bonus(self):
        """工匠的"加班程度" 0..1。用来调工作时的动作频率 / 台词倾向。"""
        with self._lock:
            return max(0.0, min(1.0, (self.level - 0.25) / 0.6))
