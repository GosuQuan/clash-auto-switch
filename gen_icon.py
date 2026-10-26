# -*- coding: utf-8 -*-
"""生成 Clash 自动切换器应用图标 icon.ico（含 256/128/64/48/32/16 多尺寸）。"""
from PIL import Image, ImageDraw, ImageFilter

S = 1024          # 画布尺寸（4x 超采样后缩小）
SS = 1024 * 2     # 超采样画布
img = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# ---------- 圆角方块底板：垂直渐变 靛蓝 -> 天蓝 ----------
top = (79, 70, 229)      # indigo-600
bot = (14, 165, 233)     # sky-500
grad = Image.new("RGBA", (SS, SS))
gd = ImageDraw.Draw(grad)
for y in range(SS):
    t = y / (SS - 1)
    c = tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)) + (255,)
    gd.line([(0, y), (SS, y)], fill=c)

mask = Image.new("L", (SS, SS), 0)
md = ImageDraw.Draw(mask)
R = int(SS * 0.225)
md.rounded_rectangle([0, 0, SS - 1, SS - 1], radius=R, fill=255)
img.paste(grad, (0, 0), mask)

# ---------- 顶部柔光 ----------
glow = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
gld = ImageDraw.Draw(glow)
gld.ellipse([SS*0.05, -SS*0.35, SS*0.95, SS*0.30], fill=(255, 255, 255, 70))
glow = glow.filter(ImageFilter.GaussianBlur(SS * 0.06))
img = Image.composite(Image.alpha_composite(img.crop((0, 0, SS, SS)), glow),
                      img, mask)
d = ImageDraw.Draw(img)

# ---------- 左下深色晕影增加立体感 ----------
sh = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
shd = ImageDraw.Draw(sh)
shd.ellipse([-SS*0.15, SS*0.72, SS*0.75, SS*1.30], fill=(10, 20, 60, 90))
sh = sh.filter(ImageFilter.GaussianBlur(SS * 0.08))
img = Image.composite(Image.alpha_composite(img.crop((0, 0, SS, SS)), sh), img, mask)
d = ImageDraw.Draw(img)

# ---------- 闪电主体（白，带轻微投影） ----------
bolt = [
    (0.575, 0.085), (0.315, 0.560), (0.475, 0.560),
    (0.395, 0.930), (0.720, 0.405), (0.540, 0.405),
]
def px(pts):
    return [(x * SS, y * SS) for x, y in pts]

# 投影
shadow = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
sd = ImageDraw.Draw(shadow)
sd.polygon([(x + SS*0.012, y + SS*0.018) for x, y in px(bolt)], fill=(10, 25, 80, 120))
shadow = shadow.filter(ImageFilter.GaussianBlur(SS * 0.015))
img = Image.alpha_composite(img, shadow)
d = ImageDraw.Draw(img)
# 本体
d.polygon(px(bolt), fill=(255, 255, 255, 255))

# ---------- 右侧双弧线：自动巡检/切换意象（完整收在图标内） ----------
arc_w = int(SS * 0.040)
d.arc([SS*0.60, SS*0.20, SS*0.92, SS*0.60], start=-68, end=50,
      fill=(255, 255, 255, 235), width=arc_w)
d.arc([SS*0.66, SS*0.30, SS*0.98, SS*0.70], start=-62, end=46,
      fill=(255, 255, 255, 120), width=arc_w)

# 弧线端点小圆点（与弧线端对齐）
def dot(cx, cy, r, a):
    d.ellipse([(cx-r)*SS, (cy-r)*SS, (cx+r)*SS, (cy+r)*SS], fill=(255, 255, 255, a))
dot(0.820, 0.215, 0.030, 235)
dot(0.863, 0.553, 0.024, 120)

# ---------- 缩小生成多尺寸 ico ----------
final = img.resize((S, S), Image.LANCZOS)
sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
final.save("icon.ico", sizes=sizes)
final.resize((256, 256), Image.LANCZOS).save("icon-preview.png")
print("icon.ico 生成完毕")
