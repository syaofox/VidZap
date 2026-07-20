"""从主图标生成 Android 各密度图标 + 自适应图标前景"""

from pathlib import Path

from PIL import Image, ImageDraw

MASTER = Path("src/static/icon-512.png")
RES_DIR = Path("android/app/src/main/res")

DENSITIES: dict[str, int] = {
    "mipmap-mdpi": 48,
    "mipmap-hdpi": 72,
    "mipmap-xhdpi": 96,
    "mipmap-xxhdpi": 144,
    "mipmap-xxxhdpi": 192,
}


def _draw_arrow(draw: ImageDraw, size: int, cx: int, cy: int) -> None:
    """在 (cx,cy) 居中绘制白色下载箭头"""
    w = size * 0.40
    h = size * 0.48
    shaft_w = w * 0.32
    head_h = h * 0.38

    x0 = cx - w / 2
    x1 = cx + w / 2
    y0 = cy - h / 2
    y1 = cy + h / 2

    points = [
        (cx - shaft_w / 2, y0),
        (cx + shaft_w / 2, y0),
        (cx + shaft_w / 2, y1 - head_h),
        (x1, y1 - head_h),
        (cx, y1),
        (x0, y1 - head_h),
        (cx - shaft_w / 2, y1 - head_h),
    ]
    draw.polygon(points, fill=(255, 255, 255))


def generate_launcher_from_master() -> None:
    """将主图标缩放到各密度并保存为 ic_launcher.png"""
    master = Image.open(MASTER).convert("RGBA")
    for folder, size in DENSITIES.items():
        out = Path(RES_DIR) / folder / "ic_launcher.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        resized = master.resize((size, size), Image.LANCZOS)
        resized.save(out, "PNG")
        print(f"  {out}")


def generate_foreground() -> None:
    """为自适应图标生成前景层（透明底 + 白色箭头，所有密度）"""
    for folder, size in DENSITIES.items():
        safe_zone = int(size * 0.66)
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        _draw_arrow(draw, safe_zone, size // 2, size // 2)

        out = Path(RES_DIR) / folder / "ic_launcher_foreground.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out, "PNG")
        print(f"  {out}")


if __name__ == "__main__":
    generate_launcher_from_master()
    generate_foreground()
