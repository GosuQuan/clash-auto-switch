# -*- coding: utf-8 -*-
"""把 docs/shots/frame*.png 裁剪、合成为 README 用的演示 GIF 和主视觉 PNG。"""
import glob
import os

from PIL import Image

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs")
SHOTS = os.path.join(DIR, "shots")

BG = (13, 17, 23)          # 页面背景色 #0d1117

def content_height(img):
    """找到最后一行非背景像素，返回内容底部 y。"""
    gray = img.convert("L")
    w, h = gray.size
    px = gray.load()
    bottom = h
    for y in range(h - 1, -1, -1):
        row_has_content = False
        for x in range(0, w, 8):
            if abs(px[x, y] - 17) > 12:      # 与 #111 背景的差异
                row_has_content = True
                break
        if row_has_content:
            bottom = min(h, y + 24)          # 留 24px 边距
            break
    return bottom

frames = []
for p in sorted(glob.glob(os.path.join(SHOTS, "frame*.png"))):
    img = Image.open(p).convert("RGB")
    ch = content_height(img)
    img = img.crop((0, 0, img.width, ch))
    frames.append(img)
    print(os.path.basename(p), "->", img.size)

# 统一尺寸（取最小高度）
min_h = min(im.height for im in frames)
frames = [im.crop((0, 0, im.width, min_h)) for im in frames]

# 主视觉：第一帧，宽 1100
hero = frames[0].resize((1100, int(min_h * 1100 / frames[0].width)), Image.LANCZOS)
hero.save(os.path.join(DIR, "dashboard.png"), optimize=True)

# GIF：宽 960，控制体积
gif_frames = []
for im in frames:
    g = im.resize((960, int(im.height * 960 / im.width)), Image.LANCZOS)
    gif_frames.append(g.quantize(colors=128, method=Image.MEDIANCUT))

durations = [1800, 2200, 2600]
gif_frames[0].save(
    os.path.join(DIR, "demo.gif"), save_all=True, append_images=gif_frames[1:],
    duration=durations + [durations[-1]] * (len(gif_frames) - len(durations)),
    loop=0, optimize=True)

kb = os.path.getsize(os.path.join(DIR, "demo.gif")) / 1024
print(f"docs/demo.gif  {kb:.0f} KB   docs/dashboard.png  "
      f"{os.path.getsize(os.path.join(DIR, 'dashboard.png'))/1024:.0f} KB")
