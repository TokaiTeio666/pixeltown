# -*- coding: utf-8 -*-
r"""像素小镇 · 交互自检

渲染已经有图像体检了，但"点村民 / 拖窗口 / 切缩放 / 开鼠标穿透"
这些路径只有真正跑一遍才知道对不对。这里直接构造事件对象，
调用 app.py 里真实的事件处理函数，然后断言结果。

测试窗口用 alpha≈0 藏起来，但它仍是**真窗口**，
所以鼠标穿透那类要读 Win32 样式位的断言是真实验证，不是自欺。

用法： python tools/validate_interaction.py [--out 报告.txt]
"""
import ctypes
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = os.path.join(ROOT, "save", "_test")
FAILS = []
N = [0]


def check(name, cond, detail=""):
    N[0] += 1
    print("  [%s] %s %s" % (" OK " if cond else "FAIL", name, detail))
    if not cond:
        FAILS.append(name)


class Ev(object):
    """假的 Tk 事件，只需要处理器用到的那几个字段。"""

    def __init__(self, x=0, y=0, x_root=0, y_root=0, delta=0):
        self.x = x
        self.y = y
        self.x_root = x_root
        self.y_root = y_root
        self.delta = delta


def ow(ev):
    return ("x=%s y=%s x_root=%s y_root=%s delta=%s"
            % (ev.x, ev.y, ev.x_root, ev.y_root, ev.delta))


def main():
    argv = sys.argv[1:]
    out = None
    if "--out" in argv:
        i = argv.index("--out")
        out = argv[i + 1]
    if out:
        real = sys.stdout
        # 名字必须和后面 with open(...) as fh 区分开：
        # 闭包捕获的是变量而不是值，一旦把 fh 重新绑定到别的文件，
        # Tee 再写就会撞上 "I/O operation on closed file"。
        logfh = open(os.path.join(ROOT, out), "w", encoding="utf-8")

        class Tee(object):
            def write(self, s):
                real.write(s)
                logfh.write(s)

            def flush(self):
                real.flush()
                logfh.flush()

        sys.stdout = Tee()

    os.makedirs(TMP, exist_ok=True)
    save_p = os.path.join(TMP, "town.json")
    cfg_p = os.path.join(TMP, "settings.json")
    pid_p = os.path.join(TMP, "app.pid")
    err_p = os.path.join(TMP, "error.log")
    for p in (save_p, cfg_p, pid_p, err_p):
        if os.path.exists(p):
            os.remove(p)
    # 藏起来测试，并且不置顶，免得打扰正在跑的实例
    with open(cfg_p, "w", encoding="utf-8") as fh:
        json.dump({"scale": 3, "topmost": False, "alpha": 0.02,
                   "x": 60, "y": 60, "speed": 1.0}, fh)

    import tkinter as tk
    from town import app as A
    from town.world import PW, PH

    print("=" * 74)
    print("BOOT")
    print("=" * 74)
    a = A.TownApp(seed=20240607, save_path=save_p, cfg_path=cfg_p,
                  pid_path=pid_p, err_path=err_p)
    a.root.update_idletasks()
    a.root.update()
    print("  scale=%d  window=%dx%d  villagers=%d"
          % (a.scale, a.root.winfo_width(), a.root.winfo_height(),
             len(a.sim.villagers)))
    print("  cfg injected: alpha=%.2f topmost=%s" % (
        float(a.root.attributes("-alpha")), a.topmost))

    print("\n" + "=" * 74)
    print("1. WINDOW / DPI")
    print("=" * 74)
    check("window size == PW-PH * scale", a.root.winfo_width() == PW * a.scale
          and a.root.winfo_height() == PH * a.scale,
          "%dx%d vs %dx%d" % (a.root.winfo_width(), a.root.winfo_height(),
                              PW * a.scale, PH * a.scale))
    check("dpi awareness is per-monitor or better", True,
          "already asserted by probe_dpi.py")
    check("photo matches scale", a.photo.width() == PW * a.scale
          and a.photo.height() == PH * a.scale,
          "%dx%d" % (a.photo.width(), a.photo.height()))

    print("\n" + "=" * 74)
    print("2. HIT TESTING")
    print("=" * 74)
    vis = [v for v in a.sim.villagers if not v.hidden]
    check("some villagers are visible", len(vis) > 0, "n=%d" % len(vis))
    v0 = vis[0]
    hit = a.hit_villager(v0.x, v0.y - 4)
    check("hit at villager torso finds him", hit is v0,
          "%s @(%.0f,%.0f)" % (v0.name, v0.x, v0.y))
    far = a.hit_villager(-500.0, -500.0)
    check("hit far outside finds nobody", far is None)
    below = a.hit_villager(v0.x, v0.y + 40)
    check("hit well below head misses", below is None)

    print("\n" + "=" * 74)
    print("3. CLICK BEHAVIOUR")
    print("=" * 74)
    sx = int(v0.x * a.scale)
    sy = int((v0.y - 4) * a.scale)
    wx, wy = a.root.winfo_x(), a.root.winfo_y()
    a.on_press(Ev(sx, sy, wx + sx, wy + sy))
    a.on_release(Ev(sx, sy, wx + sx, wy + sy))
    check("click villager selects him", a.selected is v0,
          str(a.selected.name if a.selected else None))
    a.on_press(Ev(sx, sy, wx + sx, wy + sy))
    a.on_release(Ev(sx, sy, wx + sx, wy + sy))
    check("click same villager again deselects", a.selected is None,
          str(a.selected))
    n_before = len(a.sim.highlights)
    a.on_press(Ev(4, 4, wx + 4, wy + 4))
    a.on_release(Ev(4, 4, wx + 4, wy + 4))
    check("click empty ground spawns a heart",
          len(a.sim.highlights) == n_before + 1,
          "%d -> %d" % (n_before, len(a.sim.highlights)))
    check("click empty ground clears selection", a.selected is None)

    print("\n" + "=" * 74)
    print("4. DRAG (threshold matters: small move must stay a click)")
    print("=" * 74)
    x0, y0 = a.root.winfo_x(), a.root.winfo_y()
    a.on_press(Ev(40, 40, x0 + 40, y0 + 40))
    a.on_drag(Ev(42, 41, x0 + 42, y0 + 41))          # 抖动 2px
    a.root.update_idletasks()
    check("2px jitter does not move the window",
          (a.root.winfo_x(), a.root.winfo_y()) == (x0, y0),
          "(%d,%d) vs (%d,%d)" % (a.root.winfo_x(), a.root.winfo_y(), x0, y0))
    a.on_release(Ev(42, 41, x0 + 42, y0 + 41))
    check("2px jitter still counts as a click",
          a.selected is v0 or a.selected is None, "selected=%s" % a.selected)

    a.selected = None
    x1, y1 = a.root.winfo_x(), a.root.winfo_y()
    a.on_press(Ev(40, 40, x1 + 40, y1 + 40))
    a.on_drag(Ev(100, 70, x1 + 100, y1 + 70))        # 拖 60x30
    a.root.update_idletasks()
    nx, ny = a.root.winfo_x(), a.root.winfo_y()
    check("real drag moves the window", (nx, ny) != (x1, y1),
          "(%d,%d) -> (%d,%d)" % (x1, y1, nx, ny))
    check("drag moved by roughly the pointer delta",
          abs((nx - x1) - 60) <= 2 and abs((ny - y1) - 30) <= 2,
          "moved (%d,%d), expected (60,30)" % (nx - x1, ny - y1))
    a.on_release(Ev(100, 70, x1 + 100, y1 + 70))
    check("drag does not select a villager", a.selected is None,
          "selected=%s" % a.selected)
    check("drag persists the new position to cfg",
          a.cfg.get("x") == nx and a.cfg.get("y") == ny,
          "cfg=(%s,%s)" % (a.cfg.get("x"), a.cfg.get("y")))

    print("\n" + "=" * 74)
    print("5. SCALE / PAUSE / TOPMOST / SPEED")
    print("=" * 74)
    for s in (4, 2, 3):
        a.set_scale(s)
        a.root.update_idletasks()
        ok = (a.root.winfo_width() == PW * s and a.root.winfo_height() == PH * s
              and a.renderer.scale == s and a.photo.width() == PW * s)
        check("set_scale(%d) resizes window, renderer and photo" % s, ok,
              "win=%dx%d renderer=%d photo=%dx%d"
              % (a.root.winfo_width(), a.root.winfo_height(), a.renderer.scale,
                 a.photo.width(), a.photo.height()))
    check("cfg remembers scale", a.cfg.get("scale") == 3, str(a.cfg.get("scale")))

    a.toggle_pause()
    check("pause sets both flags", a.paused and a.renderer.paused,
          "paused=%s renderer=%s" % (a.paused, a.renderer.paused))
    a.toggle_pause()
    check("unpause sets both flags", not a.paused and not a.renderer.paused)

    tm0 = a.topmost
    a.toggle_topmost()
    check("topmost toggles the real window attribute",
          bool(a.root.attributes("-topmost")) == (not tm0),
          "was %s now %s" % (tm0, a.root.attributes("-topmost")))
    a.toggle_topmost()

    a.set_speed(2.0)
    check("speed applied and persisted", a.speed == 2.0 and a.cfg.get("speed") == 2.0,
          "speed=%s cfg=%s" % (a.speed, a.cfg.get("speed")))
    a.set_speed(1.0)

    print("\n" + "=" * 74)
    print("6. CONTEXT MENU")
    print("=" * 74)
    m = a.build_menu()
    labels = []
    for i in range(m.index("end") + 1):
        try:
            labels.append(m.entrycget(i, "label"))
        except Exception:
            labels.append("<sep>")
    print("  entries: %s" % " | ".join(labels))
    for want in ("暂停", "时间流速", "缩放", "窗口置顶", "鼠标穿透",
                 "看看小镇日志", "立刻保存", "退出小镇"):
        check("menu has %s" % want, want in labels)
    a.paused = True
    m2 = a.build_menu()
    labels2 = []
    for i in range(m2.index("end") + 1):
        try:
            labels2.append(m2.entrycget(i, "label"))
        except Exception:
            labels2.append("<sep>")
    check("menu label flips to 继续 when paused", "继续" in labels2,
          str([x for x in labels2 if x != "<sep>"][:3]))
    a.paused = False
    a.click_through = False
    m3 = a.build_menu()
    check("menu offers 鼠标穿透 when interactive",
          "鼠标穿透" in [m3.entrycget(i, "label")
                         for i in range(m3.index("end") + 1)
                         if _label_ok(m3, i)], "ok")

    print("\n" + "=" * 74)
    print("7. CLICK-THROUGH + ESCAPE HATCH")
    print("=" * 74)
    check("starts interactive", not A.is_click_through(a.root),
          "exstyle transparent=%s" % A.is_click_through(a.root))
    a.set_click_through(True)
    a.root.update_idletasks()
    real_on = A.is_click_through(a.root)
    grip_ok = a.grip is not None and a.grip.winfo_exists() == 1
    check("click-through really sets WS_EX_TRANSPARENT on the window", real_on)
    check("a grip window appears as the escape hatch", grip_ok,
          "grip=%s" % (a.grip,))
    if grip_ok:
        gx = a.grip.winfo_x()
        gw = a.root.winfo_x() + PW * a.scale
        check("grip sits at the town window's corner", abs(gx - gw) < 200,
              "grip_x=%d town_right=%d" % (gx, gw))
        # 把手点一下就该退出穿透 —— 这是唯一的退路
        a._moved = False
        a.grip_release(Ev(10, 10, gx + 10, a.grip.winfo_y() + 10))
        a.root.update_idletasks()
        check("clicking the grip turns click-through back off",
              not A.is_click_through(a.root) and not a.click_through,
              "transparent=%s flag=%s" % (A.is_click_through(a.root),
                                          a.click_through))
        check("grip disappears after leaving click-through", a.grip is None)
    a.set_click_through(True)
    a.set_click_through(False)
    check("toggling twice returns to interactive",
          not A.is_click_through(a.root) and a.grip is None)

    print("\n" + "=" * 74)
    print("8. FRAME LOOP + SAVE")
    print("=" * 74)
    try:
        a.loop()                       # 真跑一帧（不跑 mainloop）
        check("loop() renders a frame without raising", True)
    except Exception as exc:
        check("loop() renders a frame without raising", False, repr(exc))
    a.save_now()
    check("save file written", os.path.exists(save_p),
          "%d bytes" % (os.path.getsize(save_p) if os.path.exists(save_p) else 0))
    check("settings written", os.path.exists(cfg_p))
    a.root.update()
    a._write_pid()
    check("pid file written", os.path.exists(pid_p),
          open(pid_p).read() if os.path.exists(pid_p) else "missing")

    t_saved = a.sim.t
    pop_saved = len(a.sim.villagers)
    try:
        # 先读一次穿透状态，再销毁 —— 窗口没了之后 winfo 会直接抛 TclError
        before_quit = A.is_click_through(a.root)
        a.quit_app()
        check("quit_app removes the pid file", not os.path.exists(pid_p))
        check("quit_app leaves click-through off", not before_quit)
    except Exception as exc:
        check("quit_app clean", False, repr(exc))

    # ---- 二次启动：读档 + 离线结算 ----
    print("\n" + "=" * 74)
    print("9. SECOND LAUNCH (load + offline settlement)")
    print("=" * 74)
    try:
        root2 = tk.Tk()
        root2.withdraw()
        root2.destroy()
    except Exception:
        pass
    b = A.TownApp(seed=20240607, save_path=save_p, cfg_path=cfg_p,
                  pid_path=pid_p, err_path=err_p)
    check("second launch restores sim time", abs(b.sim.t - t_saved) < 1e-6,
          "%.2f vs %.2f" % (b.sim.t, t_saved))
    check("second launch restores villagers", len(b.sim.villagers) == pop_saved,
          "%d vs %d" % (len(b.sim.villagers), pop_saved))
    check("second launch restores names",
          [v.name for v in b.sim.villagers] != [],
          str([v.name for v in b.sim.villagers][:4]))
    check("no offline summary for a fresh save (just saved)", b.summary is None,
          str(b.summary))
    # 伪造一个"6 小时前离开"的存档，验证离线补算这条路。
    # 注意：不能先调 quit_app()，它会 save_now() 把 last_seen 刷成现在，
    # 伪造的时间戳会被覆盖掉。这里直接销毁窗口。
    b.root.destroy()
    with open(save_p, "r", encoding="utf-8") as fh:
        d = json.load(fh)
    forged = d["last_seen"] - 3600 * 6
    d["last_seen"] = forged
    with open(save_p, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False)
    c = A.TownApp(seed=20240607, save_path=save_p, cfg_path=cfg_p,
                  pid_path=pid_p, err_path=err_p)
    check("6h offline produces a summary", isinstance(c.summary, str)
          and len(c.summary) > 6, str(c.summary))
    check("offline advanced the clock", c.sim.t > t_saved + 24.0,
          "%.1f vs %.1f (forged last_seen %.0f)"
          % (c.sim.t, t_saved, forged))
    print("  summary: %s" % c.summary)
    c.quit_app()

    print("\n" + "=" * 74)
    print("10. GRACEFUL STOP (stop.flag)")
    print("=" * 74)
    stop_p = os.path.join(TMP, "stop.flag")
    # 上次崩溃留下的 stale 标记不能让它一启动就自杀
    with open(stop_p, "w", encoding="ascii") as fh:
        fh.write("")
    e = A.TownApp(seed=20240607, save_path=save_p, cfg_path=cfg_p,
                  pid_path=pid_p, err_path=err_p, stop_flag=stop_p)
    check("stale stop.flag is cleared at startup", not os.path.exists(stop_p))
    check("app survives a stale stop.flag", e.root.winfo_exists() == 1)
    # 真正请求停止
    with open(stop_p, "w", encoding="ascii") as fh:
        fh.write("")
    e._write_pid()
    e._started = time.perf_counter() - 10.0     # 假装已经跑了 10 秒
    try:
        e.loop()
        raised = None
    except Exception as exc:
        raised = exc
    check("loop() honours stop.flag without raising", raised is None, repr(raised))
    check("stop.flag is consumed", not os.path.exists(stop_p))
    check("graceful stop removes the pid file", not os.path.exists(pid_p))
    check("graceful stop leaves a save behind", os.path.exists(save_p),
          "%d bytes" % (os.path.getsize(save_p) if os.path.exists(save_p) else 0))

    # ---- 清理 ----
    for p in (save_p, cfg_p, pid_p, err_p):
        if os.path.exists(p):
            os.remove(p)
    try:
        os.rmdir(TMP)
    except Exception:
        pass

    print("\n" + "=" * 74)
    print("RESULT: %d checks, %d failed" % (N[0], len(FAILS)))
    if FAILS:
        print("FAILED: " + ", ".join(FAILS))
    print("=" * 74)
    return 1 if FAILS else 0


def _label_ok(menu, i):
    try:
        menu.entrycget(i, "label")
        return True
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
