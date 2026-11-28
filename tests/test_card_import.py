import csv
import tempfile
import unittest
from pathlib import Path

from app.models import Card
from app.services import import_cards

FIELDS = [
    "player_name", "player_key", "position", "ovr", "rarity", "country",
    "club", "league", "season", "image_url", "source", "active",
]


def card_row(player_key="khl:46550", club="Барыс", season="2026/2027", player_name="Джастин Бэйли"):
    return {
        "player_name": player_name, "player_key": player_key, "position": "RW",
        "ovr": "88", "rarity": "Эпический", "country": "—", "club": club,
        "league": "КХЛ", "season": season, "image_url": "",
        "source": "official KHL source", "active": "true",
    }


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


class ScalarResult:
    def __init__(self, cards):
        self.cards = cards

    def all(self):
        return self.cards


class FakeSession:
    def __init__(self, existing):
        self.existing = list(existing)
        self.added = []
        self.commits = 0

    async def scalars(self, _query):
        return ScalarResult(self.existing)

    def add_all(self, cards):
        self.added.extend(cards)

    async def commit(self):
        self.commits += 1


def make_card(row):
    return Card(
        player_name=row["player_name"], player_key=row["player_key"],
        position=row["position"], ovr=int(row["ovr"]), rarity=row["rarity"],
        country=row["country"], club=row["club"], league=row["league"],
        season=row["season"], image_url=row["image_url"], source=row["source"],
        active=True,
    )


class CardImportTests(unittest.IsolatedAsyncioTestCase):
    async def test_skips_existing_and_in_file_duplicates_but_keeps_versions(self):
        current = card_row()
        historic = card_row(club="Сочи", season="2025/2026")
        keyless_duplicate = card_row(player_key="")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cards.csv"
            write_csv(path, [current, current, keyless_duplicate, historic, historic])
            session = FakeSession([make_card(current)])
            self.assertEqual(await import_cards(session, path), 1)
            self.assertEqual(len(session.added), 1)
            self.assertEqual(session.added[0].club, "Сочи")
            self.assertEqual(session.added[0].season, "2025/2026")
            self.assertEqual(session.commits, 1)

            second = FakeSession([make_card(current), *session.added])
            self.assertEqual(await import_cards(second, path), 0)
            self.assertEqual(second.added, [])
            self.assertEqual(second.commits, 0)

    async def test_same_name_with_distinct_player_keys_is_not_collapsed(self):
        one = card_row(player_key="khl:100")
        two = card_row(player_key="khl:101")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cards.csv"
            write_csv(path, [one, two])
            session = FakeSession([])
            self.assertEqual(await import_cards(session, path), 2
)


    async def test_changed_image_url_updates_existing_edition_without_duplicate(self):
        existing_row = card_row()
        incoming = card_row()
        incoming["image_url"] = "https://cdn.example/card.jpg"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cards.csv"
            write_csv(path, [incoming])
            existing = make_card(existing_row)
            session = FakeSession([existing])

            self.assertEqual(await import_cards(session, path), 1)
            self.assertEqual(session.added, [])
            self.assertEqual(existing.image_url, incoming["image_url"])
            self.assertEqual(session.commits, 1)

    async def test_import_preserves_manual_telegram_image(self):
        existing_row = card_row()
        existing_row["image_url"] = "tgfile:uploaded-photo"
        incoming = card_row()
        incoming["image_url"] = "https://cdn.example/card.jpg"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cards.csv"
            write_csv(path, [incoming])
            existing = make_card(existing_row)
            session = FakeSession([existing])

            self.assertEqual(await import_cards(session, path), 0)
            self.assertEqual(session.added, [])
            self.assertEqual(existing.image_url, "tgfile:uploaded-photo")
            self.assertEqual(session.commits, 0)

    async def test_duplicate_rows_with_different_images_add_one_card(self):
        first = card_row()
        second = card_row()
        second["image_url"] = "https://cdn.example/card.jpg"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cards.csv"
            write_csv(path, [first, second])
            session = FakeSession([])

            self.assertEqual(await import_cards(session, path), 1)
            self.assertEqual(len(session.added), 1)
            self.assertEqual(session.added[0].image_url, second["image_url"])
            self.assertEqual(session.commits, 1)
