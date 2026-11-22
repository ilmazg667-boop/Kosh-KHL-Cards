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

    def test_all_eight_rarities_have_expected_ranges(self):
        self.assertEqual(len(RARITIES), 8)
        self.assertEqual(RARITY_RANGES['Обычный'], (70,80))
        self.assertEqual(RARITY_RANGES['Легенда'], (97,100))
        self.assertEqual(RARITIES[0], 'Обычный')
        self.assertEqual(RARITIES[-1], 'Легенда')

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
