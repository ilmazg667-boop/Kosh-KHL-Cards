import unittest

from app.models import Card
from app.seed import _sync_seed_cards


def make_card(*, season, club, name='Игрок', player_key='khl:42', ovr=80, active=True, image_url=''):
    return Card(
        player_name=name, player_key=player_key, position='RW', ovr=ovr,
        rarity='Редкий', country='Россия', club=club, league='КХЛ',
        season=season, image_url=image_url, source='legacy116', active=active,
    )


class FakeSession:
    def __init__(self):
        self.added = []

    def add(self, card):
        self.added.append(card)


class SeedSyncTests(unittest.TestCase):
    def test_same_player_different_season_and_club_keeps_both_editions(self):
        old = make_card(season='2025/2026', club='Барыс')
        new = make_card(season='2026/2027', club='Трактор')
        session = FakeSession()

        _sync_seed_cards([new], [old], session)

        self.assertEqual(session.added, [new])
        self.assertEqual(old.club, 'Барыс')
        self.assertEqual(old.season, '2025/2026')
        self.assertEqual(new.club, 'Трактор')

    def test_exact_edition_is_updated_in_place(self):
        existing = make_card(season='2026/2027', club='Барыс', name='Old name')
        incoming = make_card(season='2026/2027', club='Барыс', name='New name', active=False)
        session = FakeSession()

        _sync_seed_cards([incoming], [existing], session)

        self.assertEqual(session.added, [])
        self.assertEqual(existing.player_name, 'New name')
        self.assertFalse(existing.active)

    def test_duplicate_rows_in_seed_file_are_added_only_once(self):
        first = make_card(season='2026/2027', club='Барыс')
        duplicate = make_card(season='2026/2027', club='Барыс')
        session = FakeSession()

        _sync_seed_cards([first, duplicate], [], session)

        self.assertEqual(session.added, [first])

    def test_rating_change_is_a_separate_edition(self):
        old = make_card(season='2026/2027', club='Барыс', ovr=80)
        new = make_card(season='2026/2027', club='Барыс', ovr=82)
        session = FakeSession()

        _sync_seed_cards([new], [old], session)

        self.assertEqual(session.added, [new])
        self.assertEqual(old.ovr, 80)


    def test_image_url_change_updates_same_edition_without_duplicate(self):
        old = make_card(season='2026/2027', club='Барыс')
        new = make_card(season='2026/2027', club='Барыс', image_url='https://cdn.example/card.jpg')
        session = FakeSession()

        _sync_seed_cards([new], [old], session)

        self.assertEqual(session.added, [])
        self.assertEqual(old.image_url, new.image_url)

    def test_seed_preserves_telegram_upload_even_when_csv_has_an_image_url(self):
        old = make_card(season='2026/2027', club='Барыс', image_url='tgfile:uploaded-photo')
        new = make_card(season='2026/2027', club='Барыс', image_url='https://cdn.example/card.jpg')
        session = FakeSession()

        _sync_seed_cards([new], [old], session)

        self.assertEqual(session.added, [])
        self.assertEqual(old.image_url, 'tgfile:uploaded-photo')
