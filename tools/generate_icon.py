"""生成圆角方形图标（蓝紫渐变 + 白色下载箭头），一次性脚本"""

from PIL import Image, ImageDraw


def _create_gradient(draw: ImageDraw, size: int) -> None:
    """在 draw 上绘制从上到下的蓝紫渐变背景"""
    for y in range(size):
        ratio = y / size
        r = int(59 + (139 - 59) * ratio)
        g = int(130 + (92 - 130) * ratio)
        b = int(246 + (246 - 246) * ratio)
        draw.line([(0, y), (size, y)], fill=(r, g, b))


def _draw_arrow(draw: ImageDraw, size: int) -> None:
    """在中央绘制白色下载箭头"""
    cx = size // 2
    # 箭头尺寸：相对图标大小
    w = size * 0.40
    h = size * 0.48
    shaft_w = w * 0.32
    head_h = h * 0.38

    x0 = cx - w / 2
    x1 = cx + w / 2
    y0 = cx - h / 2
    y1 = cx + h / 2

    points = [
        (cx - shaft_w / 2, y0),     # 箭杆左上
        (cx + shaft_w / 2, y0),     # 箭杆右上
        (cx + shaft_w / 2, y1 - head_h),  # 箭杆右下（内凹处）
        (x1, y1 - head_h),          # 箭头右角
        (cx, y1),                   # 箭头尖（底部中心）
        (x0, y1 - head_h),          # 箭头左角
        (cx - shaft_w / 2, y1 - head_h),  # 箭杆左下（内凹处）
    ]

    draw.polygon(points, fill=(255, 255, 255))


def generate_icon(size: int, output_path: str) -> None:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    radius = int(size * 0.20)
    _create_gradient(draw, size)
    # 用圆角矩形 alpha 蒙版裁剪
    mask = Image.new("L", (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle([(0, 0), (size, size)], radius=radius, fill=255)
    img.putalpha(mask)

    # 重新给裁剪后的图绘制箭头
    _draw_arrow(draw, size)

    img.save(output_path, "PNG")


if __name__ == "__main__":
    import sys
    outdir = sys.argv[1] if len(sys.argv) > 1 else "src/static"
    generate_icon(512, f"{outdir}/icon-512.png")
    generate_icon(192, f"{outdir}/icon-192.png")
    generate_icon(32, f"{outdir}/favicon.png")
    print(f"Icons generated in {outdir}/")
