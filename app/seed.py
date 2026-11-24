import csv
from pathlib import Path
from sqlalchemy import select, update
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

def _card_seed_identity(card):
    """Match a particular card edition, not every edition of one player."""
    edition = (
        (card.season or '').strip(),
        (card.club or '').strip().casefold(),
        (card.league or '').strip().casefold(),
        (card.position or '').strip().upper(),
        (card.rarity or '').strip().casefold(),
        int(card.ovr),
        (card.image_url or '').strip(),
    )
    player_key = (card.player_key or '').strip().casefold()
    if player_key:
        return ('key', player_key, *edition)
    return ('name', (card.player_name or '').strip().casefold(), *edition)

def _sync_seed_cards(legacy_cards, existing_cards, session):
    """Idempotently sync exact editions while preserving a player's other editions."""
    existing_by_identity = {_card_seed_identity(card): card for card in existing_cards}
    for card in legacy_cards:
        identity = _card_seed_identity(card)
        current = existing_by_identity.get(identity)
        if current is None:
            session.add(card)
            existing_by_identity[identity] = card
        else:
            current.player_name = card.player_name
            current.player_key = card.player_key
            current.position = card.position
            current.ovr = card.ovr
            current.rarity = card.rarity
            current.country = card.country
            current.club = card.club
            current.league = card.league
            current.season = card.season
            current.image_url = card.image_url
            current.active = card.active

async def seed_cards(session):
    """Import the real player catalogue and remove all artificial demo cards."""
    # Keep old demo rows for existing collection references, but make them inactive.
    await session.execute(update(Card).where(Card.source=='demo').values(active=False))
    legacy_path = Path(__file__).resolve().parent.parent / 'data' / 'legacy_players.csv'
    if legacy_path.exists():
        legacy_cards = read_csv_cards(legacy_path)
        existing_cards = (await session.scalars(
            select(Card).where(Card.source=='legacy116')
        )).all()
        _sync_seed_cards(legacy_cards, existing_cards, session)
    await session.commit()
