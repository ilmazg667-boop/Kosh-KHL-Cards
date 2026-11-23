import csv
from pathlib import Path
from sqlalchemy import select, delete
from .models import Card
from .game import POSITIONS, RARITY_RANGES

def read_csv_cards(path):
    rows=[]
    with open(path, newline='', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            name=r['player_name'].strip(); pos=r['position'].strip().upper(); rarity=r['rarity'].strip()
            ovr=int(r['ovr'])
            if not name or pos not in POSITIONS or rarity not in RARITY_RANGES or not 50 <= ovr <= 100:
                raise ValueError(f'Invalid card row: {r}')
            rows.append(Card(player_name=name, player_key=r.get('player_key','').strip(), position=pos, ovr=ovr,
                rarity=rarity, country=r.get('country','—').strip() or '—', club=r.get('club','—').strip() or '—',
                league=r.get('league','—').strip() or '—', season=r.get('season','').strip(),
                image_url=r.get('image_url','').strip(), source=r.get('source','import').strip() or 'import',
                active=r.get('active','true').strip().lower() not in ('0','false','no','off')))
    return rows

async def seed_cards(session):
    """Import the real player catalogue and remove all artificial demo cards."""
    await session.execute(delete(Card).where(Card.source=='demo'))
    legacy_path = Path(__file__).resolve().parent.parent / 'data' / 'legacy_players.csv'
    if legacy_path.exists():
        legacy_cards = read_csv_cards(legacy_path)
        existing_cards = (await session.scalars(
            select(Card).where(Card.source=='legacy116')
        )).all()
        existing_by_key = {card.player_key: card for card in existing_cards}
        for card in legacy_cards:
            current = existing_by_key.get(card.player_key)
            if current is None:
                session.add(card)
            else:
                current.player_name = card.player_name
                current.position = card.position
                current.ovr = card.ovr
                current.rarity = card.rarity
                current.country = card.country
                current.club = card.club
                current.league = card.league
                current.season = card.season
                current.image_url = card.image_url
                current.active = card.active
    await session.commit()
