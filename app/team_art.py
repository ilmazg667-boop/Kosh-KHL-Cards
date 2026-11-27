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
    # Full-frame hockey rink: no side panel, only a compact team badge in the corner.
    image = Image.new('RGB', (WIDTH, HEIGHT), (24, 34, 57))
    draw = ImageDraw.Draw(image)

    # Ice surface fills the complete image.
    draw.rectangle((0, 0, WIDTH - 1, HEIGHT - 1), fill=(24, 34, 57))
    for x in range(0, WIDTH, 40):
        draw.line((x, 0, x, HEIGHT), fill=(29, 41, 64), width=1)
    draw.rounded_rectangle((8, 8, WIDTH - 9, HEIGHT - 9), radius=44, outline=ORANGE, width=4)

    # Full-rink hockey markings.
    draw.line((420, 10, 420, HEIGHT - 10), fill=(48, 103, 214), width=9)
    draw.line((860, 10, 860, HEIGHT - 10), fill=(48, 103, 214), width=9)
    draw.line((640, 10, 640, HEIGHT - 10), fill=(196, 51, 56), width=4)
    draw.ellipse((500, 260, 780, 540), outline=(198, 54, 57), width=4)
    draw.ellipse((625, 385, 655, 415), fill=(219, 74, 73))
    for cx, cy in ((235, 225), (235, 575), (1045, 225), (1045, 575)):
        draw.ellipse((cx - 100, cy - 100, cx + 100, cy + 100), outline=(198, 54, 57), width=3)
        draw.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), fill=(219, 74, 73))
    draw.arc((1090, 300, 1270, 500), 90, 270, fill=(63, 128, 245), width=4)
    draw.rounded_rectangle((1190, 355, 1250, 445), radius=10, outline=(224, 230, 245), width=4)

    # Compact team badge in the upper-left corner.
    name = str(team_name or 'Моя команда')
    draw.rounded_rectangle((22, 18, 360, 112), radius=18, fill=(12, 19, 34), outline=(112, 61, 34), width=2)
    draw.text((42, 30), name, font=_fit(draw, name, 285, 25, True), fill=CREAM)
    draw.text((42, 70), f'СИЛА СОСТАВА  {strength} OVR', font=_font(19, True), fill=ORANGE)

    # Six enlarged card bays spread across the full rink.
    bays = {
        'LW': (350, 78, 510, 290),
        'LD': (760, 132, 920, 344),
        'C':  (560, 294, 720, 506),
        'G':  (1050, 294, 1210, 506),
        'RD': (760, 456, 920, 668),
        'RW': (350, 510, 510, 722),
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
