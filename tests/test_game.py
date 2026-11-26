import unittest
from types import SimpleNamespace

from app.game import PACKS, LEAGUES, PVE_REWARDS, RARITY_RANGES, RARITIES, league_for_rating, simulate, team_power

class GameLogicTests(unittest.TestCase):
    def test_all_pack_odds_sum_to_one_hundred(self):
        for pack in PACKS.values():
            self.assertEqual(sum(weight for _, weight in pack['odds']), 100)

    def test_league_boundaries(self):
        expected = [(0,'ЮХЛ'),(1000,'ЮХЛ'),(1001,'МХЛ'),(2000,'МХЛ'),
                    (2001,'ВХЛ'),(3000,'ВХЛ'),(3001,'КХЛ'),(5000,'КХЛ'),(5001,'НХЛ')]
        for rating, league in expected:
            self.assertEqual(league_for_rating(rating), league)

    def test_team_power_uses_rounded_average(self):
        cards = [SimpleNamespace(ovr=x) for x in (70,71,72,73,74,75)]
        self.assertEqual(team_power(cards), 73)
        cards = [SimpleNamespace(ovr=x) for x in (70,70,70,70,70,71)]
        self.assertEqual(team_power(cards), 70)

    def test_pve_reward_schedule_matches_design(self):
        self.assertEqual(PVE_REWARDS, {'ЮХЛ':1200,'МХЛ':2000,'ВХЛ':2500,'КХЛ':3500,'НХЛ':5000})

    def test_all_ten_rarities_have_non_overlapping_ovr_ranges(self):
        expected = {
            'Обычный': (50,73), 'Редкий': (74,77), 'Элитный': (78,81),
            'Сверхредкий': (82,85), 'Эпический': (86,89), 'Мифический': (90,92),
            'Легендарный': (93,95), 'Хроматический': (96,97),
            'Легенда': (98,99), 'Икона': (100,100),
        }
        self.assertEqual(RARITY_RANGES, expected)
        self.assertEqual(len(RARITIES), 10)
        self.assertEqual(set(RARITIES), set(expected))
        self.assertEqual(RARITIES[0], 'Обычный')
        self.assertEqual(RARITIES[-1], 'Икона')

    def test_rarity_is_derived_from_ovr_with_explicit_catalogue_override(self):
        from app.game import rarity_for_ovr

        boundaries = [
            (50, 'Обычный'), (73, 'Обычный'), (74, 'Редкий'), (77, 'Редкий'),
            (78, 'Элитный'), (81, 'Элитный'), (82, 'Сверхредкий'), (85, 'Сверхредкий'),
            (86, 'Эпический'), (89, 'Эпический'), (90, 'Мифический'), (92, 'Мифический'),
            (93, 'Легендарный'), (95, 'Легендарный'), (96, 'Хроматический'),
            (97, 'Хроматический'), (98, 'Легенда'), (99, 'Легенда'), (100, 'Икона'),
        ]
        for ovr, rarity in boundaries:
            with self.subTest(ovr=ovr):
                self.assertEqual(rarity_for_ovr(ovr), rarity)

        self.assertEqual(rarity_for_ovr(96, 'legacy116:054'), 'Легендарный')
        with self.assertRaises(ValueError):
            rarity_for_ovr(49)
        with self.assertRaises(ValueError):
            rarity_for_ovr(101)

    def test_simulation_finishes_with_winner_and_final_event(self):
        for _ in range(250):
            home, away, overtime, shootout, events, home_goals, away_goals = simulate(
                82, 80, 84, 81, 8, 6,
                ['LW One','C One','RW One','LD One','RD One','Goalie One'],
                ['LW Two','C Two','RW Two','LD Two','RD Two','Goalie Two'],
            )
            self.assertGreaterEqual(home, 0)
            self.assertGreaterEqual(away, 0)
            self.assertNotEqual(home, away)
            self.assertEqual(events[0]['type'], 'start')
            self.assertEqual(events[-1]['type'], 'final')
            self.assertLessEqual(max(event['minute'] for event in events), 65)

if __name__ == '__main__':
    unittest.main()
