"""Generate chkm8's Gmail avatar: a pixel-art robot wearing a chess-king crown on a checkerboard."""
from PIL import Image, ImageDraw, ImageFilter

S = 1024
img = Image.new("RGB", (S, S), "#0f1220")
d = ImageDraw.Draw(img)

# checkerboard background (checkmate theme), faded
sq = S // 8
for r in range(8):
    for c in range(8):
        col = "#1b2036" if (r + c) % 2 == 0 else "#12162a"
        d.rectangle([c * sq, r * sq, (c + 1) * sq, (r + 1) * sq], fill=col)

# soft glow behind the robot
glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
gd = ImageDraw.Draw(glow)
gd.ellipse([170, 190, 854, 874], fill=(0, 255, 170, 70))
glow = glow.filter(ImageFilter.GaussianBlur(90))
img.paste(glow, (0, 0), glow)
d = ImageDraw.Draw(img)

steel, steel_dk, steel_lt = "#9aa7c7", "#5d6a8c", "#c9d3ec"
neon = "#00ffb0"
gold, gold_dk = "#ffcc33", "#c9961a"

# neck + shoulders
d.rounded_rectangle([430, 760, 594, 840], 18, fill=steel_dk)
d.rounded_rectangle([240, 820, 784, 1060], 90, fill=steel)
d.rounded_rectangle([260, 840, 764, 1060], 80, outline=steel_lt, width=10)
# chest light: a tiny green candle (we're bullish, NFA)
d.rectangle([497, 880, 527, 1000], fill=neon)
d.rectangle([508, 860, 516, 1020], fill=neon)

# ears / bolts
for x in (215, 749):
    d.rounded_rectangle([x, 470, x + 60, 610], 20, fill=steel_dk)
    d.ellipse([x + 15, 520, x + 45, 550], fill=neon)

# head
d.rounded_rectangle([270, 360, 754, 780], 70, fill=steel)
d.rounded_rectangle([290, 380, 734, 760], 58, outline=steel_lt, width=10)

# visor
d.rounded_rectangle([320, 450, 704, 600], 50, fill="#0a0d18")
# eyes: one normal, one winking (snarky)
d.ellipse([380, 480, 470, 570], fill=neon)
d.ellipse([405, 495, 440, 530], fill="#eafff7")
d.rounded_rectangle([555, 515, 655, 540], 12, fill=neon)  # wink

# grin: a little candle chart mouth
d.rounded_rectangle([380, 640, 644, 715], 30, fill="#0a0d18")
bars = [(400, 690, 660), (440, 700, 670), (480, 680, 650), (520, 690, 662), (560, 672, 655), (600, 668, 652)]
for i, (x, lo, hi) in enumerate(bars):
    col = neon if i % 2 == 0 else "#ff4d6d"
    d.rectangle([x, hi, x + 24, lo], fill=col)

# chess king crown on top
d.rectangle([340, 300, 684, 360], fill=gold)
d.polygon([(340, 300), (380, 210), (430, 300)], fill=gold)
d.polygon([(594, 300), (644, 210), (684, 300)], fill=gold)
d.polygon([(450, 300), (512, 190), (574, 300)], fill=gold)
d.rectangle([340, 340, 684, 360], fill=gold_dk)
for x in (380, 512, 644):
    d.ellipse([x - 14, 310, x + 14, 338], fill="#ff4d6d" if x == 512 else neon)
# king's cross antenna
d.rectangle([496, 70, 528, 200], fill=gold)
d.rectangle([456, 105, 568, 137], fill=gold)

img.save("/Users/checkmate_agent/chkm8/assets/robot_pfp.png")
print("ok")
