# -*- coding: utf-8 -*-
r"""把一张图打成 ASCII 灰阶图，用来在没有图形界面时"看"画面。

用法： python tools/ascii_view.py shots/live_window.png [宽]
颜色图例： python tools/ascii_view.py shots/live_window.png --color
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from PIL import Image

RAMP = " .:-=+*#%@"


def lum(c):
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def main():
    path = sys.argv[1]
    color = "--color" in sys.argv
    width = 96
    for a in sys.argv[2:]:
        if a.isdigit():
            width = int(a)
    img = Image.open(path).convert("RGB")
    ar = img.height / float(img.width)
    h = max(8, int(width * ar * 0.48))
    small = img.resize((width, h), Image.BILINEAR)
    px = small.load()
    print("=" * (width + 4))
    print("%s  %dx%d  ->  %dx%d" % (os.path.basename(path), img.width, img.height, width, h))
    print("=" * (width + 4))
    print("    " + "".join(str(i % 10) for i in range(width)))
    for y in range(h):
        row = []
        for x in range(width):
            c = px[x, y]
            if color:
                r, g, b = c
                if r > 200 and abs(r - 244) < 24 and abs(g - 233) < 26 and abs(b - 208) < 30:
                    row.append("P")            # 羊皮纸 UI
                elif b > r + 24 and b > g + 8:
                    row.append("~")            # 水 / 冷色
                elif g > r + 12 and g > b + 12:
                    row.append("#")            # 草 / 植被
                elif lum(c) < 46:
                    row.append("@")            # 很暗（描边/夜）
                elif lum(c) > 196:
                    row.append(" ")            # 很亮（桌面/雪）
                else:
                    row.append(".")
            else:
                row.append(RAMP[min(len(RAMP) - 1, int(lum(c) / 256.0 * len(RAMP)))])
        print("%3d " % y + "".join(row))
    # 统计
    px2 = img.load()
    parchment = 0
    for y in range(0, img.height, 2):
        for x in range(0, img.width, 2):
            c = px2[x, y]
            if abs(c[0] - 244) < 14 and abs(c[1] - 233) < 14 and abs(c[2] - 208) < 14:
                parchment += 1
    print("-" * (width + 4))
    print("羊皮纸像素（全图抽样）: %d" % parchment)
    print("图例: P=羊皮纸UI  ~=水/冷  #=草/植被  @=很暗  ' '=很亮/桌面  .=中间调")
    return 0


if __name__ == "__main__":
    sys.exit(main())
