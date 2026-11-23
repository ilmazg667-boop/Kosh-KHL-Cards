import csv
from pathlib import Path
from sqlalchemy import select
from .models import Card
from .game import POSITIONS, RARITY_RANGES

# This development-only catalogue is intentionally labelled DEMO. Import verified
# players through /importcards or data/cards.csv before public launch.
SEED = [
 ('DEMO Forward LW','LW',70,'Обычный'),('DEMO Forward C','C',70,'Обычный'),('DEMO Forward RW','RW',70,'Обычный'),
 ('DEMO Defender LD','LD',70,'Обычный'),('DEMO Defender RD','RD',70,'Обычный'),('DEMO Goalie','G',70,'Обычный'),
]

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
    """Idempotently ensure demo starter cards and a clearly labelled test pack pool."""
    existing=(await session.scalars(select(Card).where(Card.source=='demo'))).all()
    known={(c.player_name,c.position,c.rarity) for c in existing}
    for name,pos,ovr,rarity in SEED:
        key=(name,pos,rarity)
        if key not in known:
            session.add(Card(player_name=name, player_key='', position=pos, ovr=ovr, rarity=rarity,
                country='—', club='DEMO', league='DEMO', source='demo'))
            known.add(key)
    # A clearly artificial pool keeps a fresh test install playable. Real cards
    # are preferred by open_pack whenever a real card of that rarity is present.
    for rarity,(low,high) in RARITY_RANGES.items():
        for pos in POSITIONS:
            if rarity=='Обычный':
                continue  # the six starter cards above cover this rarity
            key=(f'DEMO {rarity} {pos}',pos,rarity)
            if key not in known:
                n=low + ((len(pos)+len(rarity)) % (high-low+1))
                session.add(Card(player_name=key[0], player_key='', position=pos, ovr=n, rarity=rarity,
                    country='—', club='DEMO', league='DEMO', source='demo'))
                known.add(key)
    # Import the 116 legacy catalogue cards idempotently on every startup.
    legacy_path = Path(__file__).resolve().parent.parent / 'data' / 'legacy_players.csv'
    if legacy_path.exists():
        legacy_cards = read_csv_cards(legacy_path)
        existing_legacy = set((await session.scalars(
            select(Card.player_key).where(Card.source=='legacy116')
        )).all())
        for card in legacy_cards:
            if card.player_key not in existing_legacy:
                session.add(card)
                existing_legacy.add(card.player_key)
    await session.commit()
