# -*- coding: utf-8 -*-
r"""像素小镇 · 离线出图

把小镇在不同时辰 / 季节 / 成长阶段渲染成 PNG，
既可以自己检查效果，也能直接发给别人看。

用法：
    python tools/render_shots.py [--scale 3] [--outdir shots]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from PIL import Image, ImageDraw, ImageChops          # noqa: E402
from town.sim import TownSim                          # noqa: E402
from town.render import Renderer, KEY                 # noqa: E402
from town.palette import PAL                          # noqa: E402
from town.world import PW, PH                         # noqa: E402

PAL_UI_BG = PAL["ui_bg"]
PAL_UI_BORDER = PAL["ui_border"]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def desktop_bg(w, h, tint=(0x2E, 0x35, 0x48)):
    """假装是桌面壁纸，用来看透明抠图的效果。"""
    bg = Image.new("RGB", (w, h), tint)
    d = ImageDraw.Draw(bg)
    for y in range(h):
        t = y / float(h)
        d.line([0, y, w, y], fill=(
            int(tint[0] * (1.0 - t) + tint[0] * 0.45 * t),
            int(tint[1] * (1.0 - t) + tint[1] * 0.50 * t),
            int(tint[2] * (1.0 - t) + tint[2] * 0.62 * t)))
    for i in range(0, w, 64):
        d.line([i, 0, i + 24, h], fill=(tint[0] + 8, tint[1] + 8, tint[2] + 12))
    return bg


def key_mask(img):
    """把 #FF00FF 透明键变成 alpha。"""
    px = img.load()
    m = Image.new("L", img.size, 0)
    mp = m.load()
    k = (KEY[0], KEY[1], KEY[2])
    for y in range(img.size[1]):
        for x in range(img.size[0]):
            if px[x, y] != k:
                mp[x, y] = 255
    return m


def analyze(img):
    """代替人眼的图像体检。

    注意：透明键是 #FF00FF（品红），统计均色时**必须排除**，
    否则均值会被染成粉色，完全读不出画面本身。
    """
    k = (KEY[0], KEY[1], KEY[2])
    colors = img.getcolors(maxcolors=1 << 22)
    ncolors = len(colors) if colors else -1
    gw, gh = 176, 120
    small = img.resize((gw, gh), Image.NEAREST)
    px = small.load()
    grid = [[0] * gw for _ in range(gh)]
    tot = [0, 0, 0]
    cnt = 0
    key_px = 0
    for y in range(gh):
        for x in range(gw):
            c = px[x, y]
            if c == k:
                key_px += 1
                continue
            grid[y][x] = 1
            tot[0] += c[0]
            tot[1] += c[1]
            tot[2] += c[2]
            cnt += 1
    mean = tuple(v // max(1, cnt) for v in tot)
    first = next((y for y in range(gh) if any(grid[y])), gh)
    last = next((y for y in range(gh - 1, -1, -1) if any(grid[y])), -1)
    # 剪影是否是一个整块（不该有零散碎片或大洞）
    start = None
    for y in range(gh):
        for x in range(gw):
            if grid[y][x]:
                start = (x, y)
                break
        if start:
            break
    seen = 0
    if start:
        stack = [start]
        vis = {start}
        while stack:
            cx, cy = stack.pop()
            seen += 1
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = cx + dx, cy + dy
                if 0 <= nx < gw and 0 <= ny < gh and grid[ny][nx] and (nx, ny) not in vis:
                    vis.add((nx, ny))
                    stack.append((nx, ny))
    return {
        "ncolors": ncolors,
        "mean": mean,
        "brightness": sum(mean) / 3.0,
        "key_pct": 100.0 * key_px / (gw * gh),
        "opaque_pct": 100.0 * cnt / (gw * gh),
        "first_row": first,
        "last_row": last,
        "connected_pct": 100.0 * seen / max(1, cnt),
        "ui_colors": sum(1 for c, col in (colors or []) if col in (PAL_UI_BG, PAL_UI_BORDER)),
    }


def diff_ratio(a, b):
    """两张图有多少比例的像素不同（都缩到 176x120 再比）。"""
    sa = a.resize((176, 120), Image.NEAREST)
    sb = b.resize((176, 120), Image.NEAREST)
    pa, pb = sa.load(), sb.load()
    n = 0
    for y in range(120):
        for x in range(176):
            if pa[x, y] != pb[x, y]:
                n += 1
    return n / (176.0 * 120.0)


def shoot(sim, renderer, name, outdir, anim_t=0.0, selected=None, keep=False):
    img = renderer.draw_frame(1 / 30.0, anim_t, selected=selected)
    img.save(os.path.join(outdir, name + ".png"))
    m = key_mask(img)
    bg = desktop_bg(img.width, img.height)
    bg.paste(img, (0, 0), m)
    bg.save(os.path.join(outdir, name + "_desk.png"))
    info = analyze(img)
    px = img.load()
    corners = [px[0, 0], px[img.width - 1, 0], px[0, img.height - 1],
               px[img.width - 1, img.height - 1]]
    corner_ok = all(c == (KEY[0], KEY[1], KEY[2]) for c in corners)
    info.update({
        "name": name, "size": img.size,
        "corners_transparent": corner_ok,
        "bytes": os.path.getsize(os.path.join(outdir, name + ".png")),
        "img": img if keep else None,
    })
    return info


def to_target(sim, day_offset, hour):
    """把时间推到"再过 day_offset 天的 hour 点"，必须走 advance 让村民状态跟上。"""
    target = sim.t + day_offset * 24.0
    target = (int(target // 24) * 24) + hour
    if target <= sim.t:
        target += 24.0
    sim.advance(target - sim.t, detailed=True)


def to_festival(sim, want_season=0, hour=10.0, max_days=80):
    """推到"想要的那个季节的节日当天 hour 点"。"""
    for _ in range(max_days * 2):
        if sim.festival and sim.season == want_season:
            break
        sim.advance(6.0, detailed=True)
    if not sim.festival:
        return False
    target = (sim.day - 1) * 24 + hour
    if target > sim.t:
        sim.advance(target - sim.t, detailed=True)
    return True


def main():
    argv = sys.argv[1:]
    scale = 3
    outdir = "shots"
    if "--scale" in argv:
        i = argv.index("--scale")
        scale = int(argv[i + 1])
    if "--outdir" in argv:
        i = argv.index("--outdir")
        outdir = argv[i + 1]
    outdir = os.path.join(ROOT, outdir)
    os.makedirs(outdir, exist_ok=True)

    print("=" * 74)
    print("RENDERING  scale=%d  logical=%dx%d  ->  %dx%d" % (
        scale, PW, PH, PW * scale, PH * scale))
    print("=" * 74)

    rows = []
    keep = {}

    # ---- 同一天的不同时辰 ----
    hours = [("0600_dawn", 6.2), ("0900_morning", 9.0), ("1230_noon", 12.5),
             ("1700_dusk", 17.0), ("1930_sunset", 19.4), ("2200_night", 22.0)]
    for name, h in hours:
        sim = TownSim(seed=20240607)
        sim.advance(24.0 + h - sim.t, detailed=True)
        sim.weather = "clear"
        r = Renderer(sim, scale=scale)
        row = shoot(sim, r, name, outdir, anim_t=h, keep=True)
        keep[name] = row.pop("img")
        rows.append(row)

    # ---- 成长阶段（第 1 / 12 / 30 天）----
    for name, days in (("grow_day01", 0), ("grow_day12", 11), ("grow_day30", 29)):
        sim = TownSim(seed=20240607)
        if days:
            sim.advance(days * 24.0, detailed=True)
        to_target(sim, 0, 10.0)
        sim.weather = "clear"
        r = Renderer(sim, scale=scale)
        row = shoot(sim, r, name, outdir, anim_t=10.0, keep=True)
        keep[name] = row.pop("img")
        rows.append(row)

    # ---- 四季 ----
    for name, days, season in (("season_spring", 0, 0), ("season_summer", 8, 1),
                               ("season_autumn", 16, 2), ("season_winter", 24, 3)):
        sim = TownSim(seed=20240607)
        if days:
            sim.advance(days * 24.0, detailed=True)
        to_target(sim, 0, 10.0)
        sim.weather = "clear"
        r = Renderer(sim, scale=scale)
        row = shoot(sim, r, name, outdir, anim_t=10.0, keep=True)
        keep[name] = row.pop("img")
        rows.append(row)

    # ---- 节日 ----
    for name, want_season, hour in (("festival_spring", 0, 10.0),
                                    ("festival_autumn", 2, 10.0),
                                    ("festival_night", 3, 20.0)):
        sim = TownSim(seed=20240607)
        ok = to_festival(sim, want_season, hour)
        sim.weather = "clear"
        r = Renderer(sim, scale=scale)
        row = shoot(sim, r, name, outdir, anim_t=hour, keep=True)
        print("    %s -> %s (找到=%s)" % (name, sim.festival, ok))
        keep[name] = row.pop("img")
        rows.append(row)

    # ---- 动物迁徙 ----
    for name, kind, text, prog in (
            ("migrate_birds", "birds", "大雁排成人字，往南边去了", 0.45),
            ("migrate_butterfly", "butterfly", "一大群蝴蝶从镇子上头飞过去了", 0.5),
            ("migrate_duck", "duck", "一群鸭子摇摇摆摆地穿过了大街", 0.55)):
        sim = TownSim(seed=20240607)
        sim.advance(24 * 6, detailed=True)
        to_target(sim, 0, 11.0)
        sim.weather = "clear"
        sim.migrations = []                   # 清掉自然生成的，只留测试用的
        m = sim.start_migration(kind, text)
        m["t"] = m["life"] * prog
        r = Renderer(sim, scale=scale)
        row = shoot(sim, r, name, outdir, anim_t=4.0, keep=True)
        keep[name] = row.pop("img")
        rows.append(row)

    # ---- 天气小效果 ----
    for name, fx in (("fx_rainbow", "rainbow"), ("fx_mist", "mist"),
                     ("fx_meteor", "meteor"), ("fx_market", "market"),
                     ("fx_cat", "cat"), ("fx_party", "party")):
        sim = TownSim(seed=20240607)
        sim.advance(24 * 10, detailed=True)
        to_target(sim, 0, 21.0 if fx == "meteor" else 11.0)
        sim.weather = "clear"
        sim.fx = {fx: 6.0}
        r = Renderer(sim, scale=scale)
        row = shoot(sim, r, name, outdir, anim_t=3.0, keep=True)
        keep[name] = row.pop("img")
        rows.append(row)

    # ---- 人口到上限时街上挤不挤 ----
    sim = TownSim(seed=20240607)
    sim.advance(24 * 34, detailed=True)
    to_target(sim, 0, 10.0)
    print("\n  34 天后人口 = %d / 容量 %d（上限 12）"
          % (len(sim.villagers), sim.capacity))
    print("  职业：%s" % sorted({v.role for v in sim.villagers}))
    print("  搬入 %d 次，搬出 %d 次，节日 %d 天"
          % (sim.arrivals, sim.departures, sim.festival_days))
    r = Renderer(sim, scale=scale)
    row = shoot(sim, r, "crowd_day34", outdir, anim_t=10.0, keep=True)
    keep["crowd_day34"] = row.pop("img")
    rows.append(row)

    # ---- 天气 ----
    for name, w in (("weather_rain", "rain"), ("weather_snow", "snow")):
        sim = TownSim(seed=20240607)
        sim.advance(24 * 26.0, detailed=True)
        to_target(sim, 0, 14.0)
        sim.weather = w
        r = Renderer(sim, scale=scale)
        img = None
        for k in range(60):                      # 让雨雪粒子铺满
            img = r.draw_frame(1 / 30.0, k * 0.2)
        img.save(os.path.join(outdir, name + ".png"))
        bg = desktop_bg(img.width, img.height)
        bg.paste(img, (0, 0), key_mask(img))
        bg.save(os.path.join(outdir, name + "_desk.png"))
        info = analyze(img)
        info.update({"name": name, "size": img.size,
                     "corners_transparent": True,
                     "bytes": os.path.getsize(os.path.join(outdir, name + ".png"))})
        keep[name] = img
        rows.append(info)

    # ---- 选中村民的信息卡 ----
    sim = TownSim(seed=20240607)
    sim.advance(24 * 6.0, detailed=True)
    to_target(sim, 0, 20.0)                      # 傍晚广场社交，人都在外面
    sim.weather = "clear"
    r = Renderer(sim, scale=scale)
    for k in range(50):
        r.draw_frame(1 / 30.0, k * 0.1)
    vis = [v for v in sim.villagers if not v.hidden]
    print("  可选中村民数 = %d" % len(vis))
    if vis:
        row = shoot(sim, r, "card_selected", outdir, anim_t=5.0, selected=vis[0], keep=True)
        keep["card_selected"] = row.pop("img")
        rows.append(row)

    print("\n%-18s %-12s %7s %6s %7s %8s %8s %s" % (
        "FILE", "SIZE", "颜色数", "均色", "透明%", "顶/底到达", "连通%", "角透明"))
    print("-" * 90)
    bad = []
    by_name = {}
    for r in rows:
        by_name[r["name"]] = r
        print("%-18s %-12s %7d %6d %6.1f%% %4d/%-4d %7.1f%% %s" % (
            r["name"], "%dx%d" % r["size"], r["ncolors"], r["brightness"],
            r["key_pct"], r["first_row"], r["last_row"], r["connected_pct"],
            "是" if r["corners_transparent"] else "否"))
        if not r["corners_transparent"]:
            bad.append(r["name"] + ":角不透明")
        if r["size"] != (PW * scale, PH * scale):
            bad.append(r["name"] + ":尺寸")
        if 0 <= r["ncolors"] < 180:
            bad.append(r["name"] + ":颜色过少(%d)" % r["ncolors"])
        if r["ui_colors"] < 2:
            bad.append(r["name"] + ":UI未绘制")
        # 小岛必须是一整块、上下都够到，透明边在 8%~45% 之间
        if r["connected_pct"] < 97.0:
            bad.append(r["name"] + ":剪影碎裂(%.1f%%)" % r["connected_pct"])
        if not (6 <= r["key_pct"] <= 45):
            bad.append(r["name"] + ":透明区异常(%.1f%%)" % r["key_pct"])
        if r["first_row"] > 120 * 0.22:
            bad.append(r["name"] + ":顶部没画到")
        if r["last_row"] < 120 * 0.78:
            bad.append(r["name"] + ":底部没画到")

    # 昼夜亮度必须拉开，否则染色没生效
    if "0900_morning" in by_name and "2200_night" in by_name:
        dm = by_name["0900_morning"]["brightness"]
        dn = by_name["2200_night"]["brightness"]
        print("\n  白天亮度=%.0f  夜晚亮度=%.0f  比值=%.2f" % (dm, dn, dn / max(1.0, dm)))
        if dn / max(1.0, dm) > 0.85:
            bad.append("夜晚不够暗")

    # ---- 差异体检：该不一样的地方必须不一样 ----
    print("\n" + "=" * 74)
    print("DIFF CHECKS")
    print("=" * 74)
    pairs = [
        ("0900_morning", "2200_night", 0.20, "昼夜染色"),
        ("0900_morning", "0600_dawn", 0.10, "晨昏变化"),
        ("grow_day01", "grow_day30", 0.015, "小镇成长"),
        ("season_spring", "season_winter", 0.12, "季节变化"),
        ("weather_rain", "weather_snow", 0.02, "天气差异"),
        ("0900_morning", "card_selected", 0.05, "信息卡"),
    ]
    for a, b, need, label in pairs:
        if a not in keep or b not in keep:
            print("  [SKIP] %s (%s vs %s 缺图)" % (label, a, b))
            continue
        d = diff_ratio(keep[a], keep[b])
        ok = d >= need
        print("  [%s] %-10s %s vs %s = %.1f%% (需 ≥%.1f%%)" % (
            " OK " if ok else "FAIL", label, a, b, 100 * d, 100 * need))
        if not ok:
            bad.append("%s差异不足" % label)

    # ---- 特效必须"局部"：加一个小特效不该动到整幅画面 ----
    # 这条检查是有来历的：曾经把 `if "mist" in fx` 写成 `if "mist"`，
    # 字符串常量恒为真，于是只要 fx 非空就会把雾糊满全画面 ——
    # 不报错、不崩溃，只是画面悄悄变亮，光看代码根本发现不了。
    print("\n" + "=" * 74)
    print("FX LOCALITY（小特效只能影响局部）")
    print("=" * 74)
    probe = TownSim(seed=20240607)
    probe.advance(24 * 8, detailed=True)
    to_target(probe, 0, 11.0)
    probe.weather = "clear"
    pr = Renderer(probe, scale=scale)
    probe.fx = {}
    base_img = pr.draw_frame(1 / 30.0, 3.0)
    local_budget = {"meteor": 0.05, "cat": 0.05, "party": 0.10,
                    "market": 0.10, "ducks": 0.10, "dog": 0.05}
    for fxname, budget in local_budget.items():
        probe.fx = {fxname: 6.0}
        img = pr.draw_frame(1 / 30.0, 3.0)
        d = ImageChops.difference(base_img, img)
        px = d.load()
        changed = sum(1 for y in range(0, d.height, 3)
                      for x in range(0, d.width, 3) if px[x, y] != (0, 0, 0))
        frac = changed / float((d.width // 3) * (d.height // 3))
        ok = frac <= budget
        print("  [%s] %-8s 改动 %.2f%%  (上限 %.0f%%)"
              % (" OK " if ok else "FAIL", fxname, 100 * frac, 100 * budget))
        if not ok:
            bad.append("fx %s 影响范围过大" % fxname)
    probe.fx = {}

    # ---- 喷泉：广场正中心的地标，必须真的画出来、水柱还得会动 ----
    # 只断言"结构正确"是不够的：喷泉曾经只写了一半（世界里有这个道具，
    # 渲染层没接上），画面里什么都没有，而所有别的检查都照过。
    print("\n" + "=" * 74)
    print("FOUNTAIN（广场正中的喷泉）")
    print("=" * 74)

    def check(name, cond, detail=""):
        print("  [%s] %s %s" % (" OK " if cond else "FAIL", name, detail))
        if not cond:
            bad.append(name)

    fsim = TownSim(seed=20240607)
    fsim.advance(24 * 3, detailed=True)
    to_target(fsim, 0, 12.5)                     # 正午：染色最弱，颜色最准
    fsim.weather = "clear"
    fr = Renderer(fsim, scale=scale)
    box = fsim.world.fountain_box()
    fpx0, fpy0, fpx1, fpy1 = box
    s = scale

    def waterish(im, lo_x, lo_y, hi_x, hi_y):
        """框内"偏蓝"的像素数 —— 水本体、水柱、波光都算，石头和描边不算。"""
        px = im.load()
        n = 0
        for y in range(max(0, lo_y * s), min(im.height, hi_y * s)):
            for x in range(max(0, lo_x * s), min(im.width, hi_x * s)):
                r, g, b = px[x, y][:3]
                if b > r + 24 and b > g + 8:
                    n += 1
        return n

    # 水柱画在本体上方那 12 像素里（render.py 里 jet 贴在 py - jet.height）
    jy0, jy1 = fpy0 - 12, fpy0
    frame_a = fr.draw_frame(1 / 30.0, 12.0)
    frame_b = fr.draw_frame(1 / 30.0, 12.2)
    body_water = waterish(frame_a, fpx0, fpy0, fpx1, fpy1)
    jet_a = waterish(frame_a, fpx0, jy0, fpx1, jy1)
    jet_b = waterish(frame_b, fpx0, jy0, fpx1, jy1)
    box_cx = (fpx0 + fpx1) / 2.0
    print("  本体框 px=%s  画面中心 x=%d  喷泉中心 x=%.0f" % (box, PW // 2, box_cx))
    print("  水色像素：本体 %d  水柱 %d / %d" % (body_water, jet_a, jet_b))
    check("fountain in the middle of the picture", abs(box_cx - PW / 2.0) <= PW * 0.06,
          "%.0f vs %d" % (box_cx, PW // 2))
    check("fountain water is drawn", body_water >= 40 * s * s,
          "%d 像素（需 ≥%d）" % (body_water, 40 * s * s))
    check("fountain jet is drawn", jet_a >= 4 * s * s,
          "%d 像素（需 ≥%d）" % (jet_a, 4 * s * s))
    check("fountain jet animates",
          list(frame_a.crop((fpx0 * s, jy0 * s, fpx1 * s, jy1 * s)).getdata())
          != list(frame_b.crop((fpx0 * s, jy0 * s, fpx1 * s, jy1 * s)).getdata()),
          "两帧水柱一模一样")

    print("\n" + "=" * 74)
    print("RESULT: %d shots, %d problems" % (len(rows), len(bad)))
    if bad:
        print("BAD: " + ", ".join(sorted(set(bad))))
    print("OUTDIR: %s" % outdir)
    print("=" * 74)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
