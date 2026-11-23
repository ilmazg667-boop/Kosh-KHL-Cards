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
    while size > 12:
        font = _font(size, bold)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font
        size -= 2
    return _font(12, bold)

def render_team_art_image(rows, strength, chemistry, team_name='Моя команда'):
    image = Image.new('RGB', (WIDTH, HEIGHT), BG)
    pixels = image.load()
    for y in range(HEIGHT):
        for x in range(WIDTH):
            glow = max(0, 1 - math.sqrt(((x-900)/760)**2 + ((y-420)/600)**2))
            pixels[x, y] = (min(255, int(16 + glow*38)), min(255, int(9 + glow*13)), min(255, int(14 + glow*8)))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((20, 20, WIDTH-20, HEIGHT-20), radius=28, outline=(104, 56, 35), width=2)

    # Team information panel on the left
    draw.rounded_rectangle((42, 48, 355, 850), radius=24, fill=(29, 19, 22), outline=(130, 70, 42), width=3)
    draw.text((68, 78), 'KOSH KHL CARDS', font=_font(24, True), fill=ORANGE)
    draw.text((68, 132), 'КОМАНДА', font=_font(30, True), fill=CREAM)
    name = str(team_name or 'Моя команда')
    draw.multiline_text((68, 190), name, font=_fit(draw, name, 245, 29, True), fill=ORANGE, spacing=8)
    draw.line((68, 290, 328, 290), fill=(110, 65, 48), width=2)
    draw.text((68, 326), 'СИЛА СОСТАВА', font=_font(17, True), fill=(194, 145, 119))
    draw.text((68, 360), f'{strength} OVR', font=_font(42, True), fill=CREAM)
    draw.text((68, 440), 'СЫГРАННОСТЬ', font=_font(17, True), fill=(194, 145, 119))
    draw.text((68, 474), f'{chemistry}/40', font=_font(36, True), fill=CREAM)
    filled = max(0, min(40, int(chemistry)))
    draw.rounded_rectangle((68, 532, 328, 548), radius=8, fill=(69, 48, 42))
    if filled:
        draw.rounded_rectangle((68, 532, 68+int(260*filled/40), 548), radius=8, fill=ORANGE)
    draw.line((68, 590, 328, 590), fill=(110, 65, 48), width=2)
    draw.text((68, 628), 'СХЕМА', font=_font(16, True), fill=(194, 145, 119))
    draw.text((68, 660), '1–2–2 + ВРАТАРЬ', font=_font(19, True), fill=CREAM)
    draw.multiline_text((68, 730), 'Состав обновляется\nпосле каждой замены', font=_font(15), fill=(177, 135, 116), spacing=7)

    # Right half of the ice rink
    rink = (390, 48, 1238, 850)
    draw.rounded_rectangle(rink, radius=34, fill=(220, 237, 239), outline=(255, 255, 255), width=5)
    left, top, right, bottom = rink
    midx = (left+right)//2
    midy = (top+bottom)//2
    blue = (74, 145, 190)
    red = (194, 65, 65)
    pale = (173, 203, 211)
    # Ice markings and half-rink boundary
    draw.line((midx, top+8, midx, bottom-8), fill=red, width=5)
    draw.arc((midx-175, midy-175, midx+175, midy+175), 90, 270, fill=blue, width=5)
    draw.line((left+18, top+18, right-18, top+18), fill=pale, width=3)
    draw.line((left+18, bottom-18, right-18, bottom-18), fill=pale, width=3)
    # Faceoff circles on the defensive side
    circle_r = 118
    circle_centers = [(midx+185, top+235), (midx+185, bottom-235)]
    for cx,cy in circle_centers:
        draw.ellipse((cx-circle_r,cy-circle_r,cx+circle_r,cy+circle_r), outline=blue, width=5)
        draw.ellipse((cx-8,cy-8,cx+8,cy+8), fill=red)
    # Goal and crease at the far end
    gx = right-52
    gy = midy
    draw.arc((gx-72,gy-105,gx+35,gy+105), 90, 270, fill=blue, width=6)
    draw.rounded_rectangle((right-68,gy-42,right-18,gy+42), radius=8, outline=red, width=5)
    # Player tokens
    by_position = {card.position: card for _,card in rows}
    placements = {
        'LW': (midx-125, midy-190), 'C': (midx-5, midy),
        'RW': (midx-125, midy+190),
        'LD': (midx+185, top+235), 'RD': (midx+185, bottom-235),
        'G': (right-76, midy)
    }
    labels = {'LW':'ЛКН','C':'ЦЕНТР','RW':'ПКН','LD':'ЛЗ','RD':'ПЗ','G':'ВР'}
    for pos,(cx,cy) in placements.items():
        card = by_position.get(pos)
        radius = 66 if pos != 'G' else 58
        fill = (39, 29, 31) if card else (202, 220, 222)
        outline = ORANGE if card else (113, 151, 160)
        draw.ellipse((cx-radius,cy-radius,cx+radius,cy+radius), fill=fill, outline=outline, width=5)
        if card:
            initials = ''.join(part[0] for part in str(card.player_name).split()[:2]).upper() or '★'
            draw.text((cx,cy-12), initials[:3], font=_fit(draw,initials[:3],radius*1.55,34,True), fill=CREAM, anchor='mm')
            draw.text((cx,cy+29), f'{card.ovr}', font=_font(20,True), fill=ORANGE, anchor='mm')
            name_label = str(card.player_name)
            draw.rounded_rectangle((cx-95,cy+radius+8,cx+95,cy+radius+43), radius=10, fill=(29,19,22))
            draw.text((cx,cy+radius+25), name_label, font=_fit(draw,name_label,178,14,True), fill=CREAM, anchor='mm')
        else:
            draw.text((cx,cy-8), '+', font=_font(46,False), fill=(113,151,160), anchor='mm')
            draw.text((cx,cy+25), labels[pos], font=_font(15,True), fill=(67,103,112), anchor='mm')
    draw.text((left+28,top+28), 'АТАКА', font=_font(17,True), fill=(60,112,143))
    draw.text((right-120,bottom-52), 'ВОРОТА', font=_font(15,True), fill=(60,112,143))
    output = BytesIO()
    image.save(output, format='PNG', optimize=True)
    return output.getvalue()
