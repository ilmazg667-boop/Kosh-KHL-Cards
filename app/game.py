import math, random
from statistics import mean
from sqlalchemy import select, or_, and_

POSITIONS = ['LW','C','RW','LD','RD','G']
RARITIES = ['Обычный','Редкий','Элитный','Сверхредкий','Эпический','Мифический','Легендарный','Хроматический','Легенда','Икона']
# Non-overlapping OVR bands: rarity is determined by rating, never catalogue order.
RARITY_RANGES = {'Обычный':(50,73),'Редкий':(74,77),'Элитный':(78,81),'Сверхредкий':(82,85),'Эпический':(86,89),'Мифический':(90,92),'Легендарный':(93,95),'Хроматический':(96,97),'Легенда':(98,99),'Икона':(100,100)}
# Explicit one-off exception retained from the catalogue; add exceptional editions here.
RARITY_OVR_OVERRIDES = {'legacy116:054':'Легендарный'}

def rarity_for_ovr(ovr, player_key=''):
    key = (player_key or '').strip()
    if key in RARITY_OVR_OVERRIDES:
        return RARITY_OVR_OVERRIDES[key]
    rating = int(ovr)
    for rarity, (low, high) in RARITY_RANGES.items():
        if low <= rating <= high:
            return rarity
    raise ValueError(f'No rarity configured for OVR {rating}')
PACKS = {
 'Обычный': {'price':6000,'currency':'coins','odds':[('Обычный',51.9),('Редкий',18),('Элитный',10),('Сверхредкий',7),('Эпический',5),('Мифический',4),('Легендарный',3),('Хроматический',1),('Легенда',0.1),('Икона',0)]},
 'Премиум': {'price':18000,'currency':'coins','odds':[('Редкий',45),('Элитный',22),('Сверхредкий',15),('Эпический',9),('Мифический',5),('Легендарный',2),('Хроматический',1),('Икона',1)]},
 'Элитный': {'price':42000,'currency':'coins','odds':[('Сверхредкий',38),('Эпический',28),('Мифический',18),('Легендарный',10),('Хроматический',3),('Легенда',2),('Икона',1)]},
 'Мифический': {'price':96000,'currency':'coins','odds':[('Эпический',35),('Мифический',35),('Легендарный',20),('Хроматический',6),('Легенда',3),('Икона',1)]},
 'Легендарный': {'price':360,'currency':'gems','odds':[('Мифический',25),('Легендарный',45),('Хроматический',18),('Легенда',8),('Икона',4)]},
 'Хроматический': {'price':900,'currency':'gems','odds':[('Легендарный',25),('Хроматический',45),('Легенда',20),('Икона',10)]},
}
LEAGUES = [('ЮХЛ',0,1000),('МХЛ',1001,2000),('ВХЛ',2001,3000),('КХЛ',3001,5000),('НХЛ',5001,10**9)]
PVE_REWARDS = {'ЮХЛ':1200,'МХЛ':2000,'ВХЛ':2500,'КХЛ':3500,'НХЛ':5000}

def team_power(cards):
    return int(mean([c.ovr for c in cards]) + 0.5) if cards else 0

def league_for_rating(rating):
    for name, low, high in LEAGUES:
        if low <= rating <= high: return name
    return 'НХЛ'

def adjust_pack_odds(pack_name, odds):
    """Reduce high-rarity odds consistently for pack openings and displayed rates."""
    factors = {
        'Обычный': 1.25, 'Редкий': 1.15, 'Элитный': 1.05,
        'Сверхредкий': 0.80, 'Эпический': 0.50, 'Мифический': 0.30,
        'Легендарный': 0.18, 'Хроматический': 0.10, 'Легенда': 0.05,
        'Икона': 0.025,
    }
    weighted = [(rarity, float(weight) * factors.get(rarity, 1.0)) for rarity, weight in odds]
    total = sum(weight for _, weight in weighted)
    if total <= 0:
        return odds
    adjusted = [(rarity, round(weight * 100 / total, 2)) for rarity, weight in weighted]
    if adjusted:
        rarity, weight = adjusted[-1]
        adjusted[-1] = (rarity, round(weight + 100 - sum(p for _, p in adjusted), 2))
    return adjusted


def rarity_pick(odds):
    return random.choices([r for r,w in odds],[w for r,w in odds],k=1)[0]

async def chemistry(session, cards):
    """Uses only imported shared-game records; club membership itself gives no bonus."""
    from .models import ChemistryPair
    keys = sorted({c.player_key for c in cards if c.player_key})
    if len(keys) < 2: return 0
    pairs=[]
    for i,a in enumerate(keys):
        for b in keys[i+1:]:
            pairs.append(and_(ChemistryPair.player_key_a==a, ChemistryPair.player_key_b==b))
    if not pairs: return 0
    records=(await session.scalars(select(ChemistryPair).where(or_(*pairs)))).all()
    # Every qualifying pair contributes a small bonus; cap as requested at 40.
    return min(40, sum(min(4, r.shared_games // 10) for r in records))

def poisson(lam):
    # Knuth algorithm; suitable for the low expected goals of hockey.
    limit=math.exp(-lam); k=0; p=1.0
    while p > limit:
        k += 1; p *= random.random()
    return k-1

def simulate(home_power, away_power, home_goalie, away_goalie, home_chem=0, away_chem=0, home_names=None, away_names=None, home_goalie_name='Вратарь', away_goalie_name='Вратарь', home_team_name='Команда 1', away_team_name='Команда 2'):
    """Returns score + goal/event log; small edges matter, randomness remains significant."""
    events=[]; home_goals=[]; away_goals=[]
    penalties=[]
    # Frequent minor penalties; major and misconduct penalties are rare.
    penalty_types=[('2 минуты',82,2),('4 минуты',10,4),('5 минут',6,5),('5+20 минут',2,5)]
    reasons=['за задержку игры','за подножку','за игру высоко поднятой клюшкой','за задержку соперника','за грубость','за удар клюшкой']
    for penalized_side in ('home','away'):
        penalized_team=home_team_name if penalized_side=='home' else away_team_name
        pp_side='away' if penalized_side=='home' else 'home'
        pp_team=away_team_name if penalized_side=='home' else home_team_name
        names=home_names if penalized_side=='home' else away_names
        for _ in range(random.choices([2,3,4],[45,40,15],k=1)[0]):
            minute=random.randint(1,58)
            label,duration=random.choices([(x[0],x[2]) for x in penalty_types],[x[1] for x in penalty_types],k=1)[0]
            player=random.choice(names) if names else 'Игрок команды'
            reason=random.choice(reasons)
            end_minute=min(60,minute+duration)
            penalties.append({'side':penalized_side,'pp_side':pp_side,'minute':minute,'end':end_minute,'duration':duration,'label':label,'player':player,'reason':reason,'team':penalized_team,'pp_team':pp_team})
            events.append({'minute':minute,'type':'penalty','side':penalized_side,'duration':duration,
                'text':f'⚠️ <b>{player}</b> ({penalized_team}) удалён на {label} {reason}. 🔵 Большинство: {pp_team}.'})
            events.append({'minute':minute,'type':'powerplay','side':pp_side,'duration':duration,
                'text':f'🔵 {pp_team} получает большинство на {duration} игровых минут.'})
    def powerplay_at(side,minute):
        return any(p['pp_side']==side and p['minute']<=minute<p['end'] for p in penalties)
    home_pp_minutes=sum(max(0,p['end']-p['minute']) for p in penalties if p['pp_side']=='home')
    away_pp_minutes=sum(max(0,p['end']-p['minute']) for p in penalties if p['pp_side']=='away')
    home_sh_minutes=sum(max(0,p['end']-p['minute']) for p in penalties if p['side']=='home')
    away_sh_minutes=sum(max(0,p['end']-p['minute']) for p in penalties if p['side']=='away')
    # A power play boosts the advantaged team and suppresses scoring by the shorthanded team.
    # Five-minute major penalties keep their full duration even if the advantaged team scores.
    home_rate=max(0.45, 3.4 + (home_power-away_power)*0.055 + (home_chem-away_chem)*0.006 + (80-away_goalie)*0.018 + home_pp_minutes*0.075 - home_sh_minutes*0.11)
    away_rate=max(0.45, 3.4 + (away_power-home_power)*0.055 + (away_chem-home_chem)*0.006 + (80-home_goalie)*0.018 + away_pp_minutes*0.075 - away_sh_minutes*0.11)
    home, away=poisson(home_rate), poisson(away_rate)
    for side, count, rate in [('home',home,home_rate),('away',away,away_rate)]:
        used=set()
        for _ in range(count):
            def scoring_weight(m):
                if powerplay_at(side,m):
                    return 2.5
                # Shorthanded goals remain possible, but are substantially rarer.
                if any(p['side']==side and p['minute']<=m<p['end'] for p in penalties):
                    return 0.2
                return 1.0
            minute=random.choices(range(1,61),weights=[scoring_weight(m) for m in range(1,61)],k=1)[0]
            while minute in used and len(used)<60: minute=random.choices(range(1,61),weights=[scoring_weight(m) for m in range(1,61)],k=1)[0]
            used.add(minute)
            names=home_names if side=='home' else away_names
            names=names or [f'Игрок {i}' for i in range(1,7)]
            scorer=random.choice(names)
            helpers=[n for n in names if n!=scorer] or names
            assists=random.sample(helpers,k=min(2,len(helpers)))
            assist=assists[0]; assist2=assists[1] if len(assists)>1 else None
            team_name=home_team_name if side=='home' else away_team_name
            finishes=['мощным щелчком','точным кистевым броском','броском в одно касание','броском с неудобной руки','после сольного прохода','добиванием с пятака','рикошетом от защитника']
            finish=random.choice(finishes)
            goal={'minute':minute,'scorer':scorer,'assist':assist,'assist2':assist2,'team':team_name,'powerplay':powerplay_at(side,minute),'finish':finish}
            (home_goals if side=='home' else away_goals).append(goal)
            assist_text=f"; ассисты: <b>{assist}</b> и <b>{assist2}</b>" if assist2 else f"; ассист: <b>{assist}</b>"
            pp_label=' 🔵 <b>РЕАЛИЗОВАННОЕ БОЛЬШИНСТВО!</b>' if goal['powerplay'] else ''
            events.append({'minute':minute,'type':'goal','side':side,'text':f"🥅 ГООООЛ! <b>{scorer}</b> ({team_name}) забивает {finish}{assist_text}{pp_label}"})
    # Hockey teams typically produce dozens of shots, not just a handful.
    # Every non-goal shot is counted as a goalie save in the final box score.
    for side in ('home','away'):
        team_name=home_team_name if side=='home' else away_team_name
        goalie_name=away_goalie_name if side=='home' else home_goalie_name
        for _ in range(random.randint(27,36)):
            minute=random.randint(1,60)
            events.append({'minute':minute,'type':'shot','side':side,
                'text':f"🎯 Бросок команды {team_name}! 🧤 {goalie_name} отражает шайбу."})
    # Penalty events and power-play opportunities were created before goal simulation.
    # Add non-shot play-by-play only; shot events remain in stats but aren't sent individually.
    for _ in range(random.randint(8,14)):
        minute=random.randint(1,60)
        side=random.choice(['home','away'])
        team_name=home_team_name if side=='home' else away_team_name
        kind=random.choice(['attack','danger'])
        text=(f"🏒 {team_name} выходит из зоны и начинает атаку." if kind=='attack'
              else f"🔥 Опасный момент у ворот команды {team_name}!")
        events.append({'minute':minute,'type':kind,'side':side,'text':text})
    events.sort(key=lambda e:e['minute'])
    ot=False; shootout=False
    if home==away:
        ot=True
        events.append({'minute':60,'type':'period','text':'⏱ Основное время завершено. Овертайм 3 на 3 — 5 минут.'})
        ot_side='home' if random.random()<0.55 else 'away'
        if random.random()<0.28:
            # No OT goal: resolve the tied game with a separate shootout sequence.
            shootout=True
            events.append({'minute':65,'type':'overtime','text':'⏱ Овертайм завершён без гола.'})
            events.append({'minute':65,'type':'shootout','text':'🎯 Начинается серия буллитов!'})
            home_hits=away_hits=0
            # Shootout shots are logged one at a time, alternating sides.
            home_chance=min(.65,max(.2,.42+(home_power-away_goalie)*.008))
            away_chance=min(.65,max(.2,.42+(away_power-home_goalie)*.008))
            shot_no=0
            while shot_no<10:
                side='home' if shot_no%2==0 else 'away'
                chance=home_chance if side=='home' else away_chance
                scored=random.random()<chance
                if scored:
                    if side=='home': home_hits+=1
                    else: away_hits+=1
                team_name=home_team_name if side=='home' else away_team_name
                events.append({'minute':65,'type':'shootout_shot','side':side,'scored':scored,
                    'shootout_home':home_hits,'shootout_away':away_hits,
                    'text':f"🎯 Буллит {shot_no//2+1} — {team_name}: {'🥅 ГОЛ!' if scored else '❌ Не забил.'}"})
                shot_no+=1
                if shot_no>=6 and home_hits!=away_hits and abs(home_hits-away_hits)>((10-shot_no+1)//2):
                    break
            # Sudden-death pairs until a winner.
            while home_hits==away_hits:
                for side in ('home','away'):
                    chance=home_chance if side=='home' else away_chance
                    scored=random.random()<chance
                    if scored:
                        if side=='home': home_hits+=1
                        else: away_hits+=1
                    team_name=home_team_name if side=='home' else away_team_name
                    events.append({'minute':65,'type':'shootout_shot','side':side,'scored':scored,
                        'shootout_home':home_hits,'shootout_away':away_hits,
                        'text':f"🎯 Буллит (внезапная смерть) — {team_name}: {'🥅 ГОЛ!' if scored else '❌ Не забил.'}"})
                    if home_hits!=away_hits: break
            if home_hits>away_hits: home+=1
            else: away+=1
            events.append({'minute':65,'type':'shootout','text':f'🏁 Серия буллитов завершена: {home_hits} — {away_hits}.'})
        else:
            names=home_names if ot_side=='home' else away_names
            names=names or [f'Игрок {i}' for i in range(1,7)]
            scorer=random.choice(names)
            helpers=[n for n in names if n!=scorer] or names
            assists=random.sample(helpers,k=min(2,len(helpers)))
            assist=assists[0]; assist2=assists[1] if len(assists)>1 else None
            ot_team_name=home_team_name if ot_side=='home' else away_team_name
            ot_finish=random.choice(['мощным щелчком','точным кистевым броском','броском в одно касание','после сольного прохода','добиванием с пятака'])
            goal={'minute':65,'scorer':scorer,'assist':assist,'assist2':assist2,'team':ot_team_name,'powerplay':False,'finish':ot_finish}
            if ot_side=='home':
                home+=1; home_goals.append(goal)
            else:
                away+=1; away_goals.append(goal)
            assist_text=f"; ассисты: <b>{assist}</b> и <b>{assist2}</b>" if assist2 else f"; ассист: <b>{assist}</b>"
            events.append({'minute':65,'type':'goal','side':ot_side,'text':f'🥅 ЗОЛОТОЙ ГОЛ В ОВЕРТАЙМЕ! <b>{scorer}</b> ({ot_team_name}) забивает {ot_finish}{assist_text}'})
            events.append({'minute':65,'type':'overtime','text':'🏒 Овертайм завершён. Победитель определён.'})
    events.sort(key=lambda e:e['minute'])
    events.insert(0,{'minute':0,'type':'start','text':'🏒 Команды на точке вбрасывания. Судья готов — первое вбрасывание! Матч начался, 60 минут игрового времени.'})
    events.append({'minute':65 if ot else 60,'type':'final','text':f'🏁 Финальная сирена! Итоговый счёт: {home}:{away}.'})
    return home, away, ot, shootout, events, home_goals, away_goals
