from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import math

WIDTH, HEIGHT = 1280, 900
BG = (18, 10, 15)
ORANGE = (255, 133, 37)
CREAM = (255, 243, 230)

def _font(size, bold=False):
    path = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()

def _fit(draw, text, max_width, start, bold=False):
    size = start
    while size > 16:
        font = _font(size, bold)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font
        size -= 2
    return _font(16, bold)

def render_team_art_image(rows, strength, chemistry):
    image = Image.new('RGB', (WIDTH, HEIGHT), BG)
    pixels = image.load()
    for y in range(HEIGHT):
        for x in range(WIDTH):
            glow = max(0, 1 - math.sqrt(((x-970)/720)**2 + ((y-420)/560)**2))
            diag = ((x + y) % 170) < 2
            pixels[x, y] = (
                min(255, int(18 + glow*42 + (8 if diag else 0))),
                min(255, int(10 + glow*13)),
                min(255, int(15 + glow*8))
            )
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((24, 24, WIDTH-24, HEIGHT-24), radius=30, outline=(104, 56, 35), width=2)
    draw.line((55, 58, 245, 58), fill=ORANGE, width=5)
    draw.text((64, 78), 'KOSH KHL CARDS', font=_font(27, True), fill=ORANGE)
    draw.text((64, 119), 'МОЯ КОМАНДА', font=_font(48, True), fill=CREAM)
    draw.text((66, 181), f'СИЛА СОСТАВА  {strength} OVR     •     СЫГРАННОСТЬ  {chemistry}/40',
              font=_font(21, True), fill=(220, 169, 139))
    draw.line((64, 225, WIDTH-64, 225), fill=(110, 65, 48), width=2)

    by_position = {card.position: card for _, card in rows}
    positions = ['LW', 'C', 'RW', 'LD', 'RD', 'G']
    labels = {
        'LW': 'ЛЕВЫЙ КРАЙНИЙ', 'C': 'ЦЕНТР', 'RW': 'ПРАВЫЙ КРАЙНИЙ',
        'LD': 'ЛЕВЫЙ ЗАЩИТНИК', 'RD': 'ПРАВЫЙ ЗАЩИТНИК', 'G': 'ВРАТАРЬ'
    }
    card_w, card_h = 350, 260
    gap_x, gap_y = 30, 28
    start_x, start_y = 64, 255
    for index, position in enumerate(positions):
        col, row = index % 3, index // 3
        x = start_x + col * (card_w + gap_x)
        y = start_y + row * (card_h + gap_y)
        card = by_position.get(position)
        draw.rounded_rectangle((x, y, x+card_w, y+card_h), radius=22,
                               fill=(31, 22, 25), outline=ORANGE if card else (91, 62, 52), width=3 if card else 2)
        draw.rounded_rectangle((x+14, y+14, x+card_w-14, y+card_h-14), radius=16,
                               outline=(86, 55, 43), width=1)
        draw.text((x+28, y+26), position, font=_font(24, True), fill=ORANGE)
        draw.text((x+card_w-28, y+30), labels[position], font=_fit(draw, labels[position], 225, 14, True),
                  fill=(195, 153, 130), anchor='ra')
        if card:
            initials = ''.join(part[0] for part in str(card.player_name).split()[:2]).upper() or '★'
            cx, cy = x + card_w//2, y + 116
            draw.ellipse((cx-48, cy-48, cx+48, cy+48), fill=(48, 34, 37), outline=ORANGE, width=3)
            draw.text((cx, cy), initials[:3], font=_fit(draw, initials[:3], 76, 42, True), fill=CREAM, anchor='mm')
            name = str(card.player_name)
            draw.text((x+card_w//2, y+177), name, font=_fit(draw, name, card_w-42, 25, True),
                      fill=CREAM, anchor='mm')
            draw.text((x+card_w//2, y+218), f'{card.ovr} OVR  •  {card.rarity}',
                      font=_fit(draw, f'{card.ovr} OVR  •  {card.rarity}', card_w-42, 18, True),
                      fill=(255, 176, 91), anchor='mm')
        else:
            cx, cy = x + card_w//2, y + 127
            draw.ellipse((cx-38, cy-38, cx+38, cy+38), outline=(117, 80, 63), width=2)
            draw.line((cx-18, cy, cx+18, cy), fill=(117, 80, 63), width=3)
            draw.line((cx, cy-18, cx, cy+18), fill=(117, 80, 63), width=3)
            draw.text((cx, y+190), 'СЛОТ СВОБОДЕН', font=_font(17, True),
                      fill=(177, 135, 116), anchor='mm')
            draw.text((cx, y+218), 'КАРТОЧКА ПОЯВИТСЯ ЗДЕСЬ', font=_font(12),
                      fill=(133, 103, 92), anchor='mm')

    draw.text((64, 850), 'СОСТАВ ОБНОВЛЯЕТСЯ АВТОМАТИЧЕСКИ', font=_font(16, True), fill=(194, 137, 108))
    output = BytesIO()
    image.save(output, format='PNG', optimize=True)
    return output.getvalue()
