# -*- coding: utf-8 -*-
"""像素小镇 · 桌面悬浮小窗

窗口本身用 -transparentcolor 抠掉 #FF00FF，
所以桌面上看到的是一座漂着的小岛，而不是一个方框。

鼠标穿透有一个必须处理的安全问题：
一旦穿透，这个窗口就再也收不到任何点击 —— 包括"取消穿透"。
所以穿透开启时会自动弹出一枚小把手（独立窗口，不穿透），
点它就是退出穿透模式。没有这条退路，穿透就是不可恢复状态。
"""

import ctypes
import json
import os
import subprocess
import sys
import time
import tkinter as tk

from PIL import ImageTk

from .render import Renderer, KEY
from .sim import TownSim
from .llm import DEFAULT_BASE, DEFAULT_MODEL
from .activity import ActivityMonitor
from .saves import SaveManager
from .world import PW, PH

KEY_HEX = "#%02X%02X%02X" % KEY
_IS_WIN = sys.platform.startswith("win")
APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(APP_DIR)
SAVE_DIR = os.path.join(ROOT_DIR, "save")
SAVE_PATH = os.path.join(SAVE_DIR, "town.json")
CFG_PATH = os.path.join(SAVE_DIR, "settings.json")
PID_PATH = os.path.join(SAVE_DIR, "app.pid")
ERR_PATH = os.path.join(SAVE_DIR, "error.log")
STOP_FLAG = os.path.join(SAVE_DIR, "stop.flag")


def log_error(where, exc):
    """pythonw.exe 没有 stderr，出错必须落到文件里，否则完全看不见。"""
    try:
        os.makedirs(SAVE_DIR, exist_ok=True)
        with open(ERR_PATH, "a", encoding="utf-8") as fh:
            fh.write("[%s] %s: %r\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), where, exc))
    except Exception:
        pass

SPEEDS = [("慢", 0.5), ("正常", 1.0), ("快", 2.0), ("飞快", 5.0)]
SCALES = [2, 3, 4]
DEFAULT_SEED = 20240607

# ---- Win32：鼠标穿透 ----
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
_user32 = ctypes.windll.user32
_user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
_user32.GetWindowLongW.restype = ctypes.c_long
_user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
_user32.SetWindowLongW.restype = ctypes.c_long


def _make_dpi_aware():
    """必须声明 DPI 感知。

    否则 Windows 会把窗口按非整数比例（例如 125%）缩放，
    像素画会被双线性插值糊成一团 —— 这正是像素风应用最怕的事。
    声明之后 winfo_screenwidth / GetWindowRect 都返回物理像素，
    窗口尺寸就等于 PW*scale，一一对应。
    """
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)      # PER_MONITOR_AWARE
        return True
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return True
    except Exception:
        return False


def _hwnd(widget):
    h = widget.winfo_id()
    p = _user32.GetParent(h)
    return p if p else h


def apply_click_through(widget, on):
    h = _hwnd(widget)
    style = _user32.GetWindowLongW(h, GWL_EXSTYLE)
    if on:
        style |= (WS_EX_TRANSPARENT | WS_EX_LAYERED)
    else:
        style &= ~WS_EX_TRANSPARENT
    _user32.SetWindowLongW(h, GWL_EXSTYLE, style)


def is_click_through(widget):
    """读回真实的窗口样式位。

    不能只信自己记的布尔值 —— 穿透失败却以为成功，
    对用户来说就是"窗口既点不动也关不掉"，必须实测。
    """
    h = _hwnd(widget)
    style = _user32.GetWindowLongW(h, GWL_EXSTYLE)
    return bool(style & WS_EX_TRANSPARENT)


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            d = json.load(fh)
            return d if isinstance(d, dict) else default
    except Exception:
        return default


class TownApp(object):
    def __init__(self, seed=DEFAULT_SEED, save_path=None, cfg_path=None,
                 pid_path=None, err_path=None, stop_flag=None):
        # 路径可注入：自检工具才能在不动用户存档的前提下驱动真实 GUI
        self.save_path = save_path or SAVE_PATH
        self.cfg_path = cfg_path or CFG_PATH
        self.pid_path = pid_path or PID_PATH
        self.err_path = err_path or ERR_PATH
        self.stop_flag = stop_flag or STOP_FLAG
        _make_dpi_aware()
        self.cfg = load_json(self.cfg_path, {})
        self.seed = int(self.cfg.get("seed", seed))
        self.scale = int(self.cfg.get("scale", 3))
        if self.scale not in SCALES:
            self.scale = 3
        self.speed = float(self.cfg.get("speed", 1.0))
        self.topmost = bool(self.cfg.get("topmost", True))
        self.paused = False
        self.click_through = False
        self.selected = None
        self.grip = None
        self.anim_t = 0.0
        # 必须和 loop() 里的 now 用同一种时钟。
        # 混用 time.time()（墙钟 ~1.7e9）和 perf_counter()（开机秒数）会让
        # now - last_save 恒为巨额负数，自动存档永远不会触发。
        self.last_save = time.perf_counter()
        self.last_t = time.perf_counter()
        self._started = self.last_t
        # 上次崩溃可能留下 stop.flag，不清掉的话这次一启动就会自杀
        try:
            if os.path.exists(self.stop_flag):
                os.remove(self.stop_flag)
        except Exception:
            pass
        self._grab = None

        # ---- 存档 / 新建 ----
        self.summary = None
        if os.path.exists(self.save_path):
            try:
                # 先不补算：要等大模型接上以后再补，
                # 否则补算期间模型还没就绪，那几天的旁白会被整批跳过
                self.sim, _ = TownSim.load(self.save_path, seed=self.seed,
                                           catch_up=False)
            except Exception as exc:
                log_error("读档失败，改为新建", exc)
                self.sim = TownSim(seed=self.seed, save_path=self.save_path)
        else:
            self.sim = TownSim(seed=self.seed, save_path=self.save_path)
        self.sim.save_path = self.save_path

        # 小镇管理（列表 / 新建 / 切换 / 备份）—— 纯文件操作，不依赖 sim
        self.saves = SaveManager(SAVE_DIR)
        self.saves.ensure_dirs()

        # ---- 接本地大模型 ----
        # 默认开：用户装了 Ollama 才装的这个功能。
        # 但**连不上也完全不影响使用**，村民会退回内置台词。
        self.ai_enabled = bool(self.cfg.get("ai_enabled", True))
        self.ai_model = self.cfg.get("ai_model", DEFAULT_MODEL)
        self.ai_base = self.cfg.get("ai_base", DEFAULT_BASE)
        try:
            self.sim.setup_llm(enabled=self.ai_enabled, model=self.ai_model,
                               base=self.ai_base)
            self.sim.ai_goofy = int(self.cfg.get("ai_goofy", 2))
        except Exception as exc:
            log_error("接入本地模型失败", exc)
            self.ai_enabled = False

        # 现在模型就位了，再补算离线时间 —— 这几天的旁白也能让模型写
        self.summary = None
        away = 0.0
        if self.sim.loaded_last_seen:
            away = max(0.0, time.time() - self.sim.loaded_last_seen)
            if away > 30.0:
                try:
                    self.summary = self.sim.catch_up(away)
                except Exception as exc:
                    log_error("离线补算失败", exc)

        # ---- 外部活动（真实电脑忙不忙）----
        # 和 AI 一样是"有更好、没有也行"的东西：
        # 关掉、或采样失败，小镇照着原来的样子运转。
        self.activity_on = bool(self.cfg.get("activity_enabled", True))
        self.monitor = None
        if self.activity_on:
            try:
                self.monitor = ActivityMonitor(save_dir=SAVE_DIR, enabled=True).start()
            except Exception as exc:
                log_error("外部活动采样启动失败", exc)
                self.monitor = None
                self.activity_on = False

        self.renderer = Renderer(self.sim, scale=self.scale)
        self.renderer.paused = self.paused

        # ---- 窗口 ----
        self.root = tk.Tk()
        self.root.title("像素小镇")
        self.root.overrideredirect(True)
        if self.topmost:
            self.root.attributes("-topmost", True)
        self.root.configure(bg=KEY_HEX)
        try:
            self.root.wm_attributes("-transparentcolor", KEY_HEX)
        except tk.TclError:
            pass
        self.root.attributes("-alpha", float(self.cfg.get("alpha", 1.0)))

        w, h = PW * self.scale, PH * self.scale
        x = self.cfg.get("x")
        y = self.cfg.get("y")
        if x is None or y is None:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            x = max(0, sw - w - 40)
            y = max(0, sh - h - 110)
        self.root.geometry("%dx%d+%d+%d" % (w, h, int(x), int(y)))

        self.canvas = tk.Canvas(self.root, width=w, height=h, bg=KEY_HEX,
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        frame = self.renderer.draw_frame(0.016, 0.0, selected=None)
        self.photo = ImageTk.PhotoImage(frame)
        self.img_id = self.canvas.create_image(0, 0, anchor="nw", image=self.photo)

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Button-3>", self.popup)
        self.canvas.bind("<MouseWheel>", self.on_wheel)
        self.root.bind("<Escape>", lambda e: self.clear_selection())
        self.root.bind("<Control-s>", lambda e: self.save_now())

    # ==================================================================
    # 主循环
    # ==================================================================
    def run(self):
        self._write_pid()
        self.root.after(60, self.loop)
        self.root.mainloop()

    def _write_pid(self):
        """写一个 PID 文件，方便"停止小镇.bat"精确地只关掉这一个进程。"""
        try:
            d = os.path.dirname(self.pid_path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(self.pid_path, "w", encoding="ascii") as fh:
                fh.write(str(os.getpid()))
        except Exception:
            pass

    def loop(self):
        # 优雅退出通道：外部放一个 stop.flag 文件，程序自己存档再关。
        # 比 taskkill 强杀好得多 —— 强杀会丢掉最后一次存档，
        # 而且现实中还常被杀软或权限拦下来。
        # 要求至少运行 2 秒才响应，避免极早期误触。
        if (time.perf_counter() - self._started) > 2.0 \
                and os.path.exists(self.stop_flag):
            try:
                os.remove(self.stop_flag)
            except Exception:
                pass
            self.quit_app()
            return

        now = time.perf_counter()
        dt = min(0.25, max(0.0, now - self.last_t))
        self.last_t = now
        if not self.paused:
            # 外部活动快照只在"开了"的时候注入；没开就保持默认值，
            # 模拟层面完全不知道这台机器忙不忙。
            if self.monitor is not None:
                try:
                    self.sim.set_activity(self.monitor.snapshot())
                except Exception as exc:
                    log_error("外部活动快照失败", exc)
            self.sim.tick(dt * self.speed)
        self.anim_t += dt

        # 选中的村民可能已经进屋了
        if self.selected is not None and (self.selected.hidden or
                                          self.selected not in self.sim.villagers):
            self.selected = None

        self.renderer.paused = self.paused
        frame = self.renderer.draw_frame(dt, self.anim_t, selected=self.selected)
        try:
            self.photo.paste(frame)
            self.canvas.itemconfig(self.img_id, image=self.photo)
        except Exception:
            # 尺寸变了（缩放切换）就整张重建
            self.photo = ImageTk.PhotoImage(frame)
            self.canvas.itemconfig(self.img_id, image=self.photo)

        if now - self.last_save > 30.0:
            self.last_save = now
            try:
                self.sim.save(self.save_path)
            except Exception as exc:
                log_error("自动保存", exc)

        self.root.after(33, self.loop)

    # ==================================================================
    # 鼠标
    # ==================================================================
    def on_press(self, ev):
        self._grab = (ev.x_root - self.root.winfo_x(), ev.y_root - self.root.winfo_y())
        self._moved = False

    def on_drag(self, ev):
        if not self._grab:
            return
        dx = ev.x_root - self.root.winfo_x() - self._grab[0]
        dy = ev.y_root - self.root.winfo_y() - self._grab[1]
        if abs(dx) > 3 or abs(dy) > 3 or self._moved:
            self._moved = True
            self.root.geometry("+%d+%d" % (ev.x_root - self._grab[0],
                                           ev.y_root - self._grab[1]))
            self.reposition_grip()

    def on_release(self, ev):
        if self._moved:
            self.cfg["x"] = self.root.winfo_x()
            self.cfg["y"] = self.root.winfo_y()
            self.save_cfg()
            return
        lx = ev.x / float(self.scale)
        ly = ev.y / float(self.scale)
        v = self.hit_villager(lx, ly)
        if v is not None:
            self.selected = None if self.selected is v else v
        else:
            self.selected = None
            # 点空地：冒一颗小爱心
            self.sim.highlights.append([lx, ly - 2.0, "heart", 2.4])

    def on_wheel(self, ev):
        i = SCALES.index(self.scale) if self.scale in SCALES else 1
        i = max(0, min(len(SCALES) - 1, i + (1 if ev.delta < 0 else -1)))
        self.set_scale(SCALES[i])

    def hit_villager(self, lx, ly):
        best, bd = None, 1e9
        for v in self.sim.villagers:
            if v.hidden:
                continue
            if abs(v.x - lx) <= 5.5 and (v.y - 13.0) <= ly <= (v.y + 1.5):
                d = (v.x - lx) ** 2 + (v.y - ly) ** 2
                if d < bd:
                    bd, best = d, v
        return best

    def clear_selection(self):
        self.selected = None

    # ==================================================================
    # 菜单
    # ==================================================================
    def build_menu(self):
        """单独建出菜单对象，方便自检（不必真的弹出来）。"""
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="继续" if self.paused else "暂停",
                      command=self.toggle_pause)
        sp = tk.Menu(m, tearoff=0)
        for name, mul in SPEEDS:
            sp.add_command(label=("%s  ✓" % name) if abs(mul - self.speed) < 1e-6 else name,
                           command=lambda mu=mul: self.set_speed(mu))
        m.add_cascade(label="时间流速", menu=sp)
        sc = tk.Menu(m, tearoff=0)
        for s in SCALES:
            sc.add_command(label=("%dx  ✓" % s) if s == self.scale else ("%dx" % s),
                           command=lambda sv=s: self.set_scale(sv))
        m.add_cascade(label="缩放", menu=sc)
        m.add_command(label=("取消置顶" if self.topmost else "窗口置顶"),
                      command=self.toggle_topmost)
        m.add_command(label=("关闭鼠标穿透" if self.click_through else "鼠标穿透"),
                      command=lambda: self.set_click_through(not self.click_through))
        m.add_separator()
        ai = self.sim.ai_status()
        ai_state = {"ready": "已连接", "loading": "加载中", "error": "连不上",
                    "off": "已关闭"}.get(ai.get("state"), "?")
        m.add_command(label="AI 村民：%s（点击%s）"
                            % (ai_state, "关闭" if self.ai_enabled else "开启"),
                      command=self.toggle_ai)
        gf = tk.Menu(m, tearoff=0)
        for lvl, name in ((0, "正经"), (1, "温和"), (2, "搞怪")):
            gf.add_command(label=("%s  ✓" % name) if lvl == self.sim.ai_goofy else name,
                           command=lambda l=lvl: self.set_goofy(l))
        m.add_cascade(label="AI 搞怪程度", menu=gf)
        act = self.sim.activity or {}
        if self.activity_on and self.monitor is not None:
            lvl = int(round(100 * float(act.get("level") or 0.0)))
            act_label = "外部活动：%d%%（点击关闭）" % lvl
        else:
            act_label = "外部活动：已关闭（点击开启）"
        m.add_command(label=act_label, command=self.toggle_activity)
        m.add_separator()
        # ---- 小镇管理：换一座 / 备份 / 回退，不用再手删 town.json ----
        m.add_cascade(label="我的小镇", menu=self.build_towns_menu(m))
        m.add_separator()
        m.add_command(label="看看小镇日志", command=self.show_log)
        m.add_command(label="立刻保存", command=self.save_now)
        m.add_separator()
        m.add_command(label="退出小镇", command=self.quit_app)
        return m

    def popup(self, ev):
        m = self.build_menu()
        try:
            m.tk_popup(ev.x_root, ev.y_root)
        finally:
            m.grab_release()

    def build_towns_menu(self, parent):
        """「我的小镇」子菜单：现存的小镇 + 新建 + 备份。

        以前换小镇要手删 save/town.json，删错了就没了。
        这里把它变成可点的事，而且删档走回收站。
        """
        sub = tk.Menu(parent, tearoff=0)
        try:
            slots = self.saves.list_slots()
        except Exception as exc:
            log_error("读取小镇列表失败", exc)
            slots = []
        if not slots:
            sub.add_command(label="（还没有小镇）", state="disabled")
        for s in slots:
            # 当前存档的文件名是 town.json，不能直接当显示名用，
            # 否则会冒出一个叫 "town" 的条目和别的镇并列，看不出哪个在玩
            if s.get("is_current"):
                shown = s.get("source_name") or ("第%d天这座" % s["day"])
                label = "✓ 正在玩：%s · 第%d天 · %d人 · %s" % (
                    shown, s["day"], s["pop"], s["season"])
            else:
                label = "　%s · 第%d天 · %d人 · %s" % (
                    s["name"], s["day"], s["pop"], s["season"])
            sub.add_command(
                label=label,
                command=lambda _n=s["name"], _cur=s.get("is_current"): self.use_town(_n, _cur))
        sub.add_separator()
        sub.add_command(label="新开一座小镇…", command=self.new_town)
        sub.add_separator()
        sub.add_command(label="备份当前小镇", command=self.backup_town)
        return sub

    def backup_town(self):
        try:
            p = self.saves.backup()
            self.saves.prune_backups()
        except Exception as exc:
            log_error("备份失败", exc)
            return
        if p:
            self.sim.log("小镇备份好了：%s" % os.path.basename(p))

    def use_town(self, name, is_current=False):
        """切换到另一座小镇。当前这座会先保存 + 备份，不会丢。

        切换必须重启进程：地块、人头、记忆全要从新档重建，
        热替换的路径太多（渲染器缓存、村民引用、LLM 队列都在指着旧对象），
        不值得为一个很少发生的操作去冒那个险。
        """
        if is_current:
            return
        try:
            self.sim.save(self.save_path)
            self.saves.activate(name)
        except Exception as exc:
            log_error("切换小镇失败", exc)
            return
        # 真的重启：拉一个新进程再退出自己。
        # 原来这里只设了个 restart_please 标志就 quit_app()，
        # 而那个标志没有任何地方读 —— 结果是窗口直接消失、
        # 小镇根本没换过来，用户只能自己再启动一次（还以为崩了）。
        self.relaunch()

    def relaunch(self):
        """用同一个解释器 + 同样参数起一个新进程，然后退出自己。"""
        started = False
        try:
            argv = [sys.executable, "-m", "town.app"]
            kwargs = {"cwd": ROOT_DIR, "close_fds": True}
            if _IS_WIN:
                # pythonw 下不要弹控制台；DETACHED_PROCESS 让子进程脱离本进程存活
                kwargs["creationflags"] = 0x00000008
            subprocess.Popen(argv, **kwargs)
            started = True
        except Exception as exc:
            log_error("重启失败", exc)
        if not started:
            # 起不来就别静默退出，留个记号在日志里让用户能看到
            try:
                self.sim.log("切换后自动重启失败，请手动重新打开小镇")
            except Exception:
                pass
        self.quit_app()

    def new_town(self):
        """新开一座小镇：随机 seed（或沿用当前的）、自动命名。"""
        try:
            import random as _r
            seed = _r.randrange(100000, 999999)
            name = "小镇-%d" % seed
            sim = TownSim(seed=seed)
            self.saves.create_slot(name, seed, sim.to_dict())
            self.sim.log("新开了一座小镇：%s（在「我的小镇」里切换）" % name)
        except Exception as exc:
            log_error("新建小镇失败", exc)

    def toggle_pause(self):
        self.paused = not self.paused
        self.renderer.paused = self.paused

    def toggle_ai(self):
        self.ai_enabled = not self.ai_enabled
        try:
            self.sim.set_ai(self.ai_enabled)
        except Exception as exc:
            log_error("切换 AI 失败", exc)
        self.cfg["ai_enabled"] = self.ai_enabled
        self.save_cfg()

    def set_speed(self, mul):
        self.speed = mul
        self.cfg["speed"] = mul
        self.save_cfg()

    def toggle_activity(self):
        """开关"外部活动映射"。关掉之后小镇完全不受电脑忙闲影响。"""
        self.activity_on = not self.activity_on
        try:
            if self.activity_on:
                if self.monitor is None:
                    self.monitor = ActivityMonitor(save_dir=SAVE_DIR, enabled=True).start()
            else:
                if self.monitor is not None:
                    self.monitor.stop()
                    self.monitor = None
                # 关掉时把快照清干净，否则小镇会一直停在"忙"的状态
                self.sim.set_activity({"enabled": False})
        except Exception as exc:
            log_error("切换外部活动失败", exc)
        self.cfg["activity_enabled"] = self.activity_on
        self.save_cfg()

    def set_goofy(self, level):
        """切换 AI 的搞怪程度：0 正经 / 1 温和 / 2 搞怪。"""
        self.sim.ai_goofy = int(level)
        self.cfg["ai_goofy"] = int(level)
        self.save_cfg()

    def toggle_topmost(self):
        self.topmost = not self.topmost
        self.root.attributes("-topmost", self.topmost)
        self.cfg["topmost"] = self.topmost
        self.save_cfg()

    def set_scale(self, s):
        if s == self.scale or s not in SCALES:
            return
        x, y = self.root.winfo_x(), self.root.winfo_y()
        self.scale = s
        w, h = PW * s, PH * s
        self.root.geometry("%dx%d+%d+%d" % (w, h, x, y))
        self.canvas.configure(width=w, height=h)
        self.renderer = Renderer(self.sim, scale=s)
        self.renderer.paused = self.paused
        frame = self.renderer.draw_frame(0.016, self.anim_t, selected=self.selected)
        self.photo = ImageTk.PhotoImage(frame)
        self.canvas.itemconfig(self.img_id, image=self.photo)
        self.cfg.update({"scale": s, "x": x, "y": y})
        self.save_cfg()
        self.reposition_grip()

    def show_log(self):
        win = tk.Toplevel(self.root)
        win.title("小镇日志")
        win.attributes("-topmost", True)
        win.geometry("500x520")

        ai = self.sim.ai_status()
        head = tk.Label(
            win, anchor="w", justify="left", bg="#E8DCC0", fg="#3B2E1E",
            font=("Microsoft YaHei", 9), padx=10, pady=6,
            text=("AI 村民：%s    模型 %s\n已问 %d 次，成功 %d，失败 %d，缓存命中 %d"
                  % ({"ready": "已连接", "loading": "加载中", "error": "连不上",
                      "off": "已关闭"}.get(ai.get("state"), "?"),
                     ai.get("model", "-"),
                     ai.get("stats", {}).get("sent", 0),
                     ai.get("stats", {}).get("ok", 0),
                     ai.get("stats", {}).get("failed", 0),
                     ai.get("stats", {}).get("cache_hits", 0))))
        head.pack(fill="x")

        txt = tk.Text(win, wrap="word", bg="#F4E9D0", fg="#3B2E1E",
                      font=("Microsoft YaHei", 9), relief="flat", padx=10, pady=8)
        txt.pack(fill="both", expand=True)
        txt.tag_configure("h", foreground="#8A6A45",
                          font=("Microsoft YaHei", 9, "bold"))
        txt.tag_configure("chat", foreground="#4A5A6A")

        txt.insert("end", "—— 村民说的话 ——\n", "h")
        if self.sim.chat_log:
            for d, h, line in self.sim.chat_log[-60:][::-1]:
                txt.insert("end", "第%d天 %05.1f  %s\n" % (d, h, line), "chat")
        else:
            txt.insert("end", "（还没有）\n", "chat")
        txt.insert("end", "\n—— 小镇事件 ——\n", "h")
        for d, h, line in self.sim.log_lines[-70:][::-1]:
            txt.insert("end", "第%d天 %05.1f  %s\n" % (d, h, line))
        txt.configure(state="disabled")
        tk.Button(win, text="关闭", command=win.destroy).pack(pady=6)

    def save_now(self):
        try:
            self.sim.save(self.save_path)
        except Exception as exc:
            log_error("手动保存", exc)
        self.save_cfg()

    def save_cfg(self):
        self.cfg.update({
            "seed": self.seed, "scale": self.scale, "speed": self.speed,
            "topmost": self.topmost, "x": self.root.winfo_x(),
            "y": self.root.winfo_y(),
            "ai_enabled": self.ai_enabled, "ai_model": self.ai_model,
            "ai_base": self.ai_base, "ai_goofy": self.sim.ai_goofy,
            "activity_enabled": self.activity_on,
        })
        try:
            d = os.path.dirname(self.cfg_path)
            if d:
                os.makedirs(d, exist_ok=True)
            with open(self.cfg_path, "w", encoding="utf-8") as fh:
                json.dump(self.cfg, fh, ensure_ascii=False, indent=1)
        except Exception:
            pass

    # ==================================================================
    # 鼠标穿透 + 把手
    # ==================================================================
    def set_click_through(self, on):
        self.click_through = on
        try:
            apply_click_through(self.root, on)
        except Exception as exc:
            sys.stderr.write("穿透切换失败：%r\n" % (exc,))
            self.click_through = False
            return
        if on:
            self.show_grip()
        else:
            self.hide_grip()
        self.save_cfg()

    def show_grip(self):
        if self.grip is not None:
            self.reposition_grip()
            return
        g = tk.Toplevel(self.root)
        g.overrideredirect(True)
        g.attributes("-topmost", True)
        g.configure(bg=KEY_HEX)
        try:
            g.wm_attributes("-transparentcolor", KEY_HEX)
        except tk.TclError:
            pass
        c = tk.Canvas(g, width=34, height=34, bg=KEY_HEX,
                      highlightthickness=0, bd=0)
        c.pack()
        c.create_oval(2, 2, 31, 31, fill="#F4E9D0", outline="#6B4F2A", width=2)
        c.create_text(17, 17, text="⊙", fill="#6B4F2A",
                      font=("Microsoft YaHei", 13, "bold"))
        c.bind("<ButtonPress-1>", self.grip_press)
        c.bind("<B1-Motion>", self.grip_drag)
        c.bind("<ButtonRelease-1>", self.grip_release)
        c.bind("<Button-3>", self.popup)
        self.grip, self.grip_canvas = g, c
        self.reposition_grip()

    def reposition_grip(self):
        if self.grip is None:
            return
        gx = self.root.winfo_x() + PW * self.scale - 40
        gy = self.root.winfo_y() + 10
        self.grip.geometry("+%d+%d" % (gx, gy))

    def hide_grip(self):
        if self.grip is not None:
            self.grip.destroy()
            self.grip = None

    def grip_press(self, ev):
        self._grab = (ev.x_root - self.root.winfo_x(), ev.y_root - self.root.winfo_y())
        self._moved = False

    def grip_drag(self, ev):
        self.on_drag(ev)

    def grip_release(self, ev):
        if getattr(self, "_moved", False):
            self.cfg["x"] = self.root.winfo_x()
            self.cfg["y"] = self.root.winfo_y()
            self.save_cfg()
            return
        # 把手点一下 = 退出鼠标穿透
        self.set_click_through(False)

    # ==================================================================
    def quit_app(self):
        self.save_now()
        try:
            if self.sim.llm is not None:
                self.sim.llm.stop()          # 让后台线程收工，别留悬挂线程
        except Exception:
            pass
        try:
            if self.monitor is not None:
                self.monitor.stop()          # 外部活动采样线程同样要收
        except Exception:
            pass
        try:
            apply_click_through(self.root, False)
        except Exception:
            pass
        try:
            if os.path.exists(self.pid_path):
                os.remove(self.pid_path)
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass


def main():
    app = TownApp(seed=DEFAULT_SEED)
    app.run()


if __name__ == "__main__":
    main()
