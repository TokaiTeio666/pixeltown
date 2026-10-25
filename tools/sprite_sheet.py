# -*- coding: utf-8 -*-
r"""把全部精灵拼成一张总览图，用来肉眼检查像素风是否统一。

用法： python tools/sprite_sheet.py [输出路径]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw  # noqa: E402

from town import art  # noqa: E402
from town.palette import PAL, SKIN_TONES, SKIN_SHADOWS, HAIR_COLORS, CLOTH_COLORS, PANTS_COLORS  # noqa: E402

SCALE = 5
PAD = 8
BG = (0x2B, 0x2F, 0x3A)


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "preview_sprites.png")

    cells = []  # (label, [images])

    # --- 村民 ---
    for i in range(4):
        frames = art.bake_villager(
            SKIN_TONES[i], SKIN_SHADOWS[i], HAIR_COLORS[i],
            CLOTH_COLORS[i], PANTS_COLORS[i])
        cells.append((f"villager {i}", [frames[(d, f)] for d in art.VILLAGER_DIRS for f in (0, 1)]))

    # --- 树（四季 × 3 变体） ---
    for season in ("spring", "summer", "autumn", "winter"):
        cells.append((f"tree {season}", [art.make_tree(season, v, seed=v * 13 + len(season))
                                         for v in range(3)]))

    # --- 灌木 / 道具有 ---
    props = [
        art.make_bush("summer", 0, 1), art.make_bush("autumn", 1, 2),
        art.make_rock(0, 1), art.make_rock(1, 2), art.make_rock(2, 3),
        art.make_flower(0), art.make_flower(1), art.make_flower(2), art.make_flower(3),
        art.make_fence(True), art.make_fence(False),
        art.make_lamp(), art.make_bench(), art.make_well(),
        art.make_dock(3),
        art.make_crop(1), art.make_crop(2), art.make_crop(3), art.make_crop(4),
    ]
    cells.append(("props", props))

    # --- 建筑：5 种 × 3 级 ---
    for kind in ("house", "shop", "smithy", "barn", "hall"):
        for lvl in (1, 2, 3):
            b = art.make_building(kind, lvl, variant=lvl, seed=lvl * 7)
            cells.append((f"{kind} L{lvl}", [b["image"]]))

    # --- 排版 ---
    col_w = max(sum(im.size[0] + PAD for im in imgs) for _, imgs in cells) + PAD * 2
    total_h = PAD
    rows = []
    for label, imgs in cells:
        row_h = max(im.size[1] for im in imgs) + 16
        rows.append((label, imgs, row_h))
        total_h += row_h
    canvas = Image.new("RGB", (col_w, total_h), BG)
    d = ImageDraw.Draw(canvas)

    y = PAD
    for label, imgs, row_h in rows:
        d.rectangle([0, y - 2, col_w, y + row_h - 6], fill=(0x33, 0x38, 0x45))
        d.text((PAD, y + 1), label, fill=(0xC8, 0xD0, 0xDC))
        x = PAD
        for im in imgs:
            big = im.resize((im.size[0] * SCALE, im.size[1] * SCALE), Image.NEAREST)
            canvas.paste(big, (x, y + 12), big)
            x += im.size[0] * SCALE + PAD
        y += row_h

    canvas.save(out_path)
    print("saved", out_path, canvas.size)


if __name__ == "__main__":
    main()
