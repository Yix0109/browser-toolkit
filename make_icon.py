"""生成程序图标 app.ico（蓝底 + 白色闪电）。"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


def make(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    s = size / 256.0

    # 圆角矩形 + 竖向渐变
    radius = int(52 * s)
    bg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    grad = Image.new("RGBA", (size, size))
    top, bottom = (37, 99, 235), (30, 58, 138)
    for y in range(size):
        t = y / max(1, size - 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,)
        ImageDraw.Draw(grad).line([(0, y), (size, y)], fill=color)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    bg.paste(grad, (0, 0), mask)
    img.alpha_composite(bg)

    # 白色闪电
    pts = [(150, 20), (70, 140), (120, 140), (100, 236), (190, 110), (138, 110)]
    draw.polygon([(x * s, y * s) for x, y in pts], fill=(255, 255, 255, 255))
    return img


def main() -> None:
    out = Path(__file__).with_name("app.ico")
    sizes = [16, 24, 32, 48, 64, 128, 256]
    make(256).save(out, format="ICO", sizes=[(s, s) for s in sizes])
    print("icon ->", out)


if __name__ == "__main__":
    main()
