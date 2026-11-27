from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1280, 800
BG = (12, 31, 39)
PANEL = (17, 45, 53)
PANEL_LIGHT = (24, 59, 67)
ACCENT = (245, 169, 72)
MINT = (255, 207, 128)
WHITE = (235, 246, 242)
MUTED = (148, 183, 183)


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
    image = Image.new('RGB', (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)

    # Left information panel replaces the coach card.
    draw.rectangle((0, 0, WIDTH - 1, HEIGHT - 1), fill=BG)
    draw.rounded_rectangle((24, 24, 302, 776), radius=28, fill=PANEL, outline=(58, 72, 111), width=2)
    draw.rounded_rectangle((46, 48, 56, 100), radius=5, fill=MINT)
    draw.text((72, 48), 'СОСТАВ', font=_font(34, True), fill=WHITE)
    draw.text((72, 91), 'КОМАНДА', font=_font(16, True), fill=MUTED)

    # Team details in a clean information card.
    draw.rounded_rectangle((44, 155, 282, 710), radius=22, fill=PANEL_LIGHT, outline=(68, 84, 132), width=2)
    draw.text((66, 181), 'НАЗВАНИЕ', font=_font(14, True), fill=MUTED)
    name = str(team_name or 'Моя команда')
    draw.multiline_text((66, 207), name, font=_fit(draw, name, 190, 25, True), fill=WHITE, spacing=4)
    draw.line((66, 270, 260, 270), fill=(71, 87, 130), width=2)

    draw.text((66, 296), 'СИЛА СОСТАВА', font=_font(14, True), fill=MUTED)
    draw.text((62, 322), str(strength), font=_font(66, True), fill=MINT)
    draw.text((190, 365), 'OVR', font=_font(17, True), fill=MINT)
    draw.line((66, 414, 260, 414), fill=(71, 87, 130), width=2)

    draw.text((66, 440), 'СЫГРАННОСТЬ', font=_font(14, True), fill=MUTED)
    chemistry_text = str(chemistry)
    draw.text((66, 466), chemistry_text, font=_fit(draw, chemistry_text, 190, 34, True), fill=WHITE)
    draw.line((66, 526, 260, 526), fill=(71, 87, 130), width=2)

    draw.text((66, 552), 'ИГРОКОВ В СОСТАВЕ', font=_font(13, True), fill=MUTED)
    draw.text((66, 580), f'{len(rows)} / 6', font=_font(38, True), fill=WHITE)
    draw.rounded_rectangle((66, 644, 260, 654), radius=5, fill=(48, 61, 91))
    progress = max(0, min(1, len(rows) / 6))
    draw.rounded_rectangle((66, 644, 66 + int(194 * progress), 654), radius=5, fill=MINT)

    # Rink area, visually separated from the team information.
    draw.rounded_rectangle((320, 24, 1256, 776), radius=42, fill=(15, 39, 47), outline=(66, 111, 115), width=3)
    draw.rounded_rectangle((332, 36, 1244, 764), radius=34, outline=(43, 78, 84), width=2)
    # Subtle ice texture.
    for x in range(350, 1240, 38):
        draw.line((x, 40, x, 760), fill=(20, 48, 56), width=1)

    # Hockey markings inside the rink.
    draw.line((800, 38, 800, 762), fill=(70, 105, 110), width=4)
    draw.line((500, 38, 500, 762), fill=(43, 83, 91), width=3)
    draw.line((1100, 38, 1100, 762), fill=(43, 83, 91), width=3)
    draw.ellipse((680, 285, 920, 525), outline=(70, 105, 110), width=3)
    draw.ellipse((788, 393, 812, 417), fill=ACCENT)
    for cx, cy in ((430, 210), (430, 590), (1140, 210), (1140, 590)):
        draw.ellipse((cx - 58, cy - 58, cx + 58, cy + 58), outline=(45, 82, 89), width=2)
        draw.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), fill=MINT)

    # Goal crease behind the goalie at the bottom of the rink.
    draw.arc((680, 650, 920, 850), 180, 360, fill=MINT, width=4)
    draw.rounded_rectangle((748, 735, 852, 766), radius=8, outline=WHITE, width=3)

    # Three forwards across the top, two defenders in the middle, goalie at the bottom.
    draw.text((800, 65), 'НАПАДЕНИЕ', font=_font(17, True), fill=MINT, anchor='mm')
    draw.text((800, 305), 'ЗАЩИТА', font=_font(17, True), fill=MINT, anchor='mm')
    draw.text((800, 545), 'ВРАТАРЬ', font=_font(17, True), fill=MINT, anchor='mm')

    bays = {
        'LW': (385, 92, 555, 302),
        'C':  (715, 92, 885, 302),
        'RW': (1045, 92, 1215, 302),
        'LD': (535, 330, 705, 540),
        'RD': (895, 330, 1065, 540),
        'G':  (715, 570, 885, 780),
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
                    card_image.thumbnail((x2 - x1 - 8, y2 - y1 - 8), Image.Resampling.LANCZOS)
                    px = x1 + ((x2 - x1) - card_image.width) // 2
                    py = y1 + ((y2 - y1) - card_image.height) // 2
                    image.paste(card_image, (px, py))
                    draw.rounded_rectangle((x1 + 4, y1 + 4, x2 - 4, y2 - 4), radius=12, outline=ACCENT, width=3)
                except Exception:
                    image_data = None
            if not image_data and placeholder_image_bytes:
                try:
                    placeholder = Image.open(BytesIO(placeholder_image_bytes)).convert('RGB')
                    placeholder.thumbnail((x2 - x1 - 8, y2 - y1 - 8), Image.Resampling.LANCZOS)
                    px = x1 + ((x2 - x1) - placeholder.width) // 2
                    py = y1 + ((y2 - y1) - placeholder.height) // 2
                    image.paste(placeholder, (px, py))
                    draw.rounded_rectangle((x1 + 3, y1 + 3, x2 - 3, y2 - 3), radius=10, outline=ACCENT, width=3)
                    image_data = b'placeholder-used'
                except Exception:
                    image_data = None
            if not image_data:
                draw.rounded_rectangle((x1 + 7, y1 + 7, x2 - 7, y2 - 7), radius=14, fill=PANEL_LIGHT, outline=ACCENT, width=2)
                draw.ellipse((cx - 31, cy - 50, cx + 31, cy + 12), outline=MINT, width=3)
                draw.arc((cx - 22, cy - 41, cx + 22, cy + 3), 35, 305, fill=WHITE, width=4)
                draw.text((cx, cy + 36), 'НЕТ ФОТО', font=_font(15, True), fill=MINT, anchor='mm')
                player_name = str(card.player_name)
                draw.text((cx, cy + 61), player_name, font=_fit(draw, player_name, x2 - x1 - 18, 14, True), fill=WHITE, anchor='mm')
                draw.text((cx, cy + 83), f'{card.ovr} OVR', font=_font(15, True), fill=WHITE, anchor='mm')
        else:
            placeholder_used = False
            if placeholder_image_bytes:
                try:
                    placeholder = Image.open(BytesIO(placeholder_image_bytes)).convert('RGB')
                    placeholder.thumbnail((x2 - x1 - 8, y2 - y1 - 8), Image.Resampling.LANCZOS)
                    px = x1 + ((x2 - x1) - placeholder.width) // 2
                    py = y1 + ((y2 - y1) - placeholder.height) // 2
                    image.paste(placeholder, (px, py))
                    draw.rounded_rectangle((x1 + 3, y1 + 3, x2 - 3, y2 - 3), radius=10, outline=ACCENT, width=3)
                    placeholder_used = True
                except Exception:
                    placeholder_used = False
            if not placeholder_used:
                draw.rounded_rectangle((x1 + 7, y1 + 7, x2 - 7, y2 - 7), radius=14, fill=(25, 37, 64), outline=(74, 88, 132), width=2)
                draw.text((cx, cy), bay_labels[pos], font=_font(34, True), fill=MUTED, anchor='mm')

    output = BytesIO()
    image.save(output, format='PNG', optimize=True)
    return output.getvalue()
