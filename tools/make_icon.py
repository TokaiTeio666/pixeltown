# -*- coding: utf-8 -*-
r"""像素小镇 · 图标生成

为什么要自己拼 ICO 文件：
Pillow 存 ICO 时对每个尺寸都用 LANCZOS 缩放，像素画会被插值糊掉。
像素图标只能按整数倍最近邻放大，所以这里手工写 ICO 容器，
每个尺寸都是原生 1x/2x/3x... 的干净像素。

图案是手绘的 16x16 像素徽标：红顶小屋 + 一棵树 + 小岛水面。
16x16 是经典图标尺寸，桌面默认 48px 时就是 3 倍放大，边缘绝对锐利。

用法： python tools/make_icon.py
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 色板（和 town/palette.py 保持一致）
C = {
    ".": (0, 0, 0, 0),                    # 透明
    "O": (0x3B, 0x2E, 0x24, 255),         # 描边
    "R": (0xC0, 0x50, 0x4D, 255),         # 屋顶
    "r": (0x96, 0x3B, 0x39, 255),         # 屋顶暗部
    "W": (0xE4, 0xD9, 0xC3, 255),         # 墙面
    "D": (0x7A, 0x52, 0x30, 255),         # 门
    "G": (0x74, 0xB2, 0x45, 255),         # 草
    "g": (0x5E, 0x99, 0x35, 255),         # 草暗
    "L": (0x5F, 0xA3, 0x3F, 255),         # 树叶
    "l": (0xA5, 0xD6, 0x68, 255),         # 叶亮
    "N": (0x7A, 0x52, 0x30, 255),         # 树干
    "A": (0x37, 0x68, 0x9C, 255),         # 水
}

# 手绘 16x16。每行必须正好 16 个字符。
ART = [
    "................",
    "...OOOOOO.......",
    "..ORRRRRRO......",
    ".ORRRRRRRRO..LL.",
    ".ORRRRRRRRO.LLLL",
    ".OOOOOOOOOO.LLLL",
    ".OWWWWWWWWO..LL.",
    ".OWWWWWWWWO..N..",
    ".OWWDDWWWWO..N..",
    ".OWWDDWWWWO..N..",
    ".OWWDDWWWWO..N..",
    ".OWWDDWWWWO..N..",
    ".OGGGGGGGGGGNGG.",
    ".OGGGGGGGGGGGGG.",
    ".OAAAAAAAAAAAAO.",
    "..OOOOOOOOOOOO..",
]

W = 16
SIZES = [16, 24, 32, 48, 64, 128, 256]


def build_native():
    im = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    px = im.load()
    for y, row in enumerate(ART):
        if len(row) != W:
            raise SystemExit("第 %d 行长度是 %d，应该是 %d：%r"
                             % (y, len(row), W, row))
        for x, ch in enumerate(row):
            if ch not in C:
                raise SystemExit("第 %d 行第 %d 列有未知字符 %r" % (y, x, ch))
            px[x, y] = C[ch]
    return im


def write_ico(images, path):
    """手工写 ICO：每个条目塞一张 PNG（Vista 以后 Windows 都支持）。

    Pillow 的 ICO 保存会对每个尺寸做 LANCZOS 缩放，像素画会被糊掉，
    所以不能直接把图交给它。
    """
    n = len(images)
    blobs = []
    for size, im in images:
        assert im.size == (size, size), (size, im.size)
        import io
        buf = io.BytesIO()
        im.save(buf, format="PNG", optimize=True)
        blobs.append(buf.getvalue())

    header = struct.pack("<HHH", 0, 1, n)
    offset = 6 + 16 * n
    entries = b""
    for (size, _), data in zip(images, blobs):
        b = 0 if size >= 256 else size          # 256 在 ICO 里记作 0
        entries += struct.pack("<BBBBHHII", b, b, 0, 0, 1, 32, len(data), offset)
        offset += len(data)

    with open(path, "wb") as fh:
        fh.write(header)
        fh.write(entries)
        for data in blobs:
            fh.write(data)
    return path


def main():
    native = build_native()
    images = []
    for s in SIZES:
        if s % W:                                # 只做整数倍，保证像素锐利
            continue
        images.append((s, native.resize((s, s), Image.NEAREST)))
    print("生成尺寸：%s" % [s for s, _ in images])

    ico = os.path.join(ROOT, "pixeltown.ico")
    write_ico(images, ico)
    print("ICO  = %s  (%d 字节)" % (ico, os.path.getsize(ico)))

    png = os.path.join(ROOT, "pixeltown_icon_preview.png")
    # 只摆整数倍尺寸：非整数倍最近邻会让像素大小不均匀
    show = [16, 32, 48, 64, 128]
    pad = 12
    total = sum(show) + pad * (len(show) + 1)
    preview = Image.new("RGBA", (total, max(show) + pad * 2), (0x2E, 0x35, 0x48, 255))
    x = pad
    for s in show:
        im = native.resize((s, s), Image.NEAREST)
        preview.alpha_composite(im, (x, pad))
        x += s + pad
    preview.save(png)
    print("预览 = %s  %s" % (png, preview.size))

    # 读回来验证
    back = Image.open(ico)
    got = sorted(back.ico.sizes()) if hasattr(back, "ico") else []
    print("回读尺寸：%s" % got)
    ok = all((s, s) in got for s, _ in images)
    print("RESULT: %s" % ("OK" if ok else "MISSING SIZES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
