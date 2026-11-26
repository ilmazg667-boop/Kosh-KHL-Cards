from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1280, 800
BG = (13, 9, 14)
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

def render_team_art_image(rows, strength, chemistry, team_name='Моя команда', placeholder_image_bytes=None):
    # Layout follows the user's supplied 1280x800 half-rink reference.
    image = Image.new('RGB', (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)

    # Dark arena background and left information panel.
    draw.rounded_rectangle((40, 40, 360, 760), radius=24, fill=(14, 10, 15), outline=(112, 61, 34), width=2)
    name = str(team_name or 'Моя команда')
    draw.text((64, 70), 'KOSH KHL CARDS', font=_font(22, True), fill=ORANGE)
    draw.multiline_text((64, 132), name, font=_fit(draw, name, 270, 30, True), fill=CREAM, spacing=7)
    draw.line((64, 235, 330, 235), fill=(100, 61, 44), width=2)
    draw.text((64, 275), 'СИЛА СОСТАВА', font=_font(17, True), fill=(194, 145, 119))
    draw.text((64, 310), f'{strength} OVR', font=_font(42, True), fill=CREAM)
    draw.text((64, 395), 'СЫГРАННОСТЬ', font=_font(17, True), fill=(194, 145, 119))
    draw.text((64, 430), f'{chemistry}/40', font=_font(36, True), fill=CREAM)
    draw.rounded_rectangle((64, 490, 330, 506), radius=8, fill=(69, 48, 42))
    filled = max(0, min(40, int(chemistry)))
    if filled:
        draw.rounded_rectangle((64, 490, 64 + int(266 * filled / 40), 506), radius=8, fill=ORANGE)
    draw.line((64, 550, 330, 550), fill=(100, 61, 44), width=2)
    draw.text((64, 585), 'СХЕМА', font=_font(16, True), fill=(194, 145, 119))
    draw.text((64, 620), '1–2–2 + ВРАТАРЬ', font=_font(18, True), fill=CREAM)
    draw.multiline_text((64, 690), 'Состав меняется\nпосле каждой замены', font=_font(14), fill=(177, 135, 116), spacing=5)

    # User's dark-blue half rink, with orange perimeter and original markings.
    rink = (400, 40, 1240, 760)
    draw.rounded_rectangle(rink, radius=36, fill=(24, 34, 57), outline=ORANGE, width=4)
    left, top, right, bottom = rink
    # Subtle vertical ice texture.
    for x in range(left + 12, right - 8, 40):
        draw.line((x, top + 5, x, bottom - 5), fill=(31, 43, 67), width=1)
    draw.line((608, top + 3, 608, bottom - 3), fill=(48, 103, 214), width=13)
    draw.arc((285, 278, 530, 530), 270, 90, fill=(58, 111, 231), width=4)
    draw.line((1152, 78, 1152, 722), fill=(196, 51, 56), width=3)
    draw.ellipse((858, 92, 1110, 344), outline=(198, 54, 57), width=4)
    draw.ellipse((858, 464, 1110, 716), outline=(198, 54, 57), width=4)
    for cx, cy in ((984, 218), (984, 590)):
        draw.ellipse((cx - 10, cy - 10, cx + 10, cy + 10), fill=(219, 74, 73))
    draw.ellipse((1060, 315, 1230, 493), outline=(63, 128, 245), width=3)
    draw.rounded_rectangle((1150, 367, 1212, 441), radius=10, outline=(224, 230, 245), width=4)

    # Six dashed position bays exactly follow the reference image.
    bays = {
        'LW': (630, 65, 778, 262),
        'LD': (910, 119, 1058, 317),
        'C':  (730, 305, 878, 503),
        'G':  (1074, 305, 1222, 503),
        'RD': (910, 491, 1058, 689),
        'RW': (630, 545, 778, 743),
    }
    bay_labels = {'LW': 'LW', 'LD': 'LD', 'C': 'C', 'G': 'G', 'RD': 'RD', 'RW': 'RW'}
    by_position = {card.position: card for _, card in rows}

    for pos, (x1, y1, x2, y2) in bays.items():
        card = by_position.get(pos)
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        if card:
            image_data = next((data for data, item in rows if item.id == card.id and data), None)
            if image_data:
                try:
                    card_image = Image.open(BytesIO(image_data)).convert('RGB')
                    card_image.thumbnail((x2 - x1 - 14, y2 - y1 - 14), Image.Resampling.LANCZOS)
                    px = x1 + ((x2 - x1) - card_image.width) // 2
                    py = y1 + ((y2 - y1) - card_image.height) // 2
                    image.paste(card_image, (px, py))
                    draw.rounded_rectangle((x1 + 4, y1 + 4, x2 - 4, y2 - 4), radius=10, outline=ORANGE, width=2)
                except Exception:
                    image_data = None
            if not image_data and placeholder_image_bytes:
                try:
                    placeholder = Image.open(BytesIO(placeholder_image_bytes)).convert('RGB')
                    placeholder.thumbnail((x2 - x1 - 8, y2 - y1 - 8), Image.Resampling.LANCZOS)
                    px = x1 + ((x2 - x1) - placeholder.width) // 2
                    py = y1 + ((y2 - y1) - placeholder.height) // 2
                    image.paste(placeholder, (px, py))
                    draw.rounded_rectangle((x1 + 3, y1 + 3, x2 - 3, y2 - 3), radius=8, outline=ORANGE, width=2)
                    image_data = b'placeholder-used'
                except Exception:
                    image_data = None
            if not image_data:
                draw.rounded_rectangle((x1 + 7, y1 + 7, x2 - 7, y2 - 7), radius=12, fill=(31, 43, 66), outline=ORANGE, width=2)
                draw.ellipse((cx - 31, cy - 50, cx + 31, cy + 12), outline=ORANGE, width=3)
                draw.arc((cx - 22, cy - 41, cx + 22, cy + 3), 35, 305, fill=CREAM, width=4)
                draw.text((cx, cy + 36), 'ЗАГРУЗКА', font=_font(15, True), fill=ORANGE, anchor='mm')
                player_name = str(card.player_name)
                draw.text((cx, cy + 61), player_name, font=_fit(draw, player_name, x2 - x1 - 18, 14, True), fill=CREAM, anchor='mm')
                draw.text((cx, cy + 83), f'{card.ovr} OVR', font=_font(15, True), fill=CREAM, anchor='mm')
        else:
            # Empty lineup slot: show the configured team placeholder when available.
            placeholder_used = False
            if placeholder_image_bytes:
                try:
                    placeholder = Image.open(BytesIO(placeholder_image_bytes)).convert('RGB')
                    placeholder.thumbnail((x2 - x1 - 8, y2 - y1 - 8), Image.Resampling.LANCZOS)
                    px = x1 + ((x2 - x1) - placeholder.width) // 2
                    py = y1 + ((y2 - y1) - placeholder.height) // 2
                    image.paste(placeholder, (px, py))
                    draw.rounded_rectangle((x1 + 3, y1 + 3, x2 - 3, y2 - 3), radius=8, outline=ORANGE, width=2)
                    placeholder_used = True
                except Exception:
                    placeholder_used = False
            if not placeholder_used:
                draw.text((cx, cy), bay_labels[pos], font=_font(34, True), fill=(173, 148, 137), anchor='mm')

    output = BytesIO()
    image.save(output, format='PNG', optimize=True)
    return output.getvalue()
