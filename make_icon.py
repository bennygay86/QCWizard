from PIL import Image, ImageDraw

SIZE = 512
PURPLE = (106, 27, 154, 255)
PURPLE_DARK = (74, 20, 140, 255)
PURPLE_LIGHT = (149, 117, 205, 255)
GOLD = (255, 213, 79, 255)
STAR = (255, 235, 130, 255)
TRANSPARENT = (0, 0, 0, 0)


def draw_star(draw, cx, cy, r_outer, r_inner, fill):
    import math
    pts = []
    for i in range(10):
        angle = -math.pi / 2 + i * math.pi / 5
        r = r_outer if i % 2 == 0 else r_inner
        pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    draw.polygon(pts, fill=fill)


def make_icon():
    img = Image.new("RGBA", (SIZE, SIZE), TRANSPARENT)
    d = ImageDraw.Draw(img)

    # Hat brim (ellipse)
    brim_left = 40
    brim_right = SIZE - 40
    brim_top = 380
    brim_bottom = 460
    d.ellipse([brim_left, brim_top, brim_right, brim_bottom], fill=PURPLE_DARK)

    # Hat cone (triangle, curved tip via polygon)
    # Slightly curled tip to the right
    cone = [
        (140, 410),  # bottom-left base
        (SIZE - 140, 410),  # bottom-right base
        (340, 90),   # mid-upper
        (370, 50),   # tip
        (330, 70),   # tip curl
    ]
    d.polygon(cone, fill=PURPLE)

    # Highlight on cone (lighter purple stripe)
    highlight = [
        (180, 405),
        (210, 405),
        (310, 130),
        (295, 130),
    ]
    d.polygon(highlight, fill=PURPLE_LIGHT)

    # Gold band where cone meets brim
    band_top = 360
    band_bottom = 400
    band_pts = [
        (130, band_top),
        (SIZE - 130, band_top),
        (SIZE - 110, band_bottom),
        (110, band_bottom),
    ]
    d.polygon(band_pts, fill=GOLD)

    # Stars on the hat
    draw_star(d, 250, 260, 28, 12, STAR)
    draw_star(d, 310, 200, 18, 8, STAR)
    draw_star(d, 230, 180, 14, 6, STAR)
    draw_star(d, 290, 320, 16, 7, STAR)

    # Save as ICO with multiple sizes
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    img.save("wizard_hat.ico", format="ICO", sizes=sizes)
    img.save("wizard_hat.png", format="PNG")
    print("Icon written: wizard_hat.ico")


if __name__ == "__main__":
    make_icon()
