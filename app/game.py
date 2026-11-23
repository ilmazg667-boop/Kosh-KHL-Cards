import math, random
from statistics import mean
from sqlalchemy import select, or_, and_

POSITIONS = ['LW','C','RW','LD','RD','G']
RARITIES = ['Обычный','Редкий','Сверхредкий','Эпический','Мифический','Легендарный','Хроматический','Легенда']
RARITY_RANGES = {'Обычный':(70,80),'Редкий':(74,83),'Сверхредкий':(78,87),'Эпический':(82,91),'Мифический':(86,94),'Легендарный':(90,97),'Хроматический':(94,99),'Легенда':(97,100)}
PACKS = {
 'Обычный': {'price':500,'currency':'coins','odds':[('Обычный',70),('Редкий',22),('Сверхредкий',7),('Эпический',1)]},
 'Премиум': {'price':1500,'currency':'coins','odds':[('Редкий',55),('Сверхредкий',30),('Эпический',12),('Мифический',3)]},
 'Элитный': {'price':3500,'currency':'coins','odds':[('Сверхредкий',45),('Эпический',30),('Мифический',18),('Легендарный',7)]},
 'Мифический': {'price':8000,'currency':'coins','odds':[('Эпический',40),('Мифический',40),('Легендарный',17),('Хроматический',3)]},
 'Легендарный': {'price':30,'currency':'gems','odds':[('Мифический',35),('Легендарный',45),('Хроматический',17),('Легенда',3)]},
 'Хроматический': {'price':75,'currency':'gems','odds':[('Легендарный',35),('Хроматический',55),('Легенда',10)]},
}
LEAGUES = [('ЮХЛ',0,1000),('МХЛ',1001,2000),('ВХЛ',2001,3000),('КХЛ',3001,5000),('НХЛ',5001,10**9)]
PVE_REWARDS = {'ЮХЛ':1200,'МХЛ':2000,'ВХЛ':2500,'КХЛ':3500,'НХЛ':5000}

def team_power(cards):
    return int(mean([c.ovr for c in cards]) + 0.5) if cards else 0

def league_for_rating(rating):
    for name, low, high in LEAGUES:
        if low <= rating <= high: return name
    return 'НХЛ'

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
    home_rate=max(0.7, 2.75 + (home_power-away_power)*0.055 + (home_chem-away_chem)*0.006 + (home_goalie-away_goalie)*0.010)
    away_rate=max(0.7, 2.75 + (away_power-home_power)*0.055 + (away_chem-home_chem)*0.006 + (away_goalie-home_goalie)*0.010)
    home, away=poisson(home_rate), poisson(away_rate)
    events=[]; home_goals=[]; away_goals=[]
    for side, count, rate in [('home',home,home_rate),('away',away,away_rate)]:
        used=set()
        for _ in range(count):
            minute=random.randint(1,60)
            while minute in used and len(used)<60: minute=random.randint(1,60)
            used.add(minute)
            names=home_names if side=='home' else away_names
            names=names or [f'Игрок {i}' for i in range(1,7)]
            scorer=random.choice(names)
            helpers=[n for n in names if n!=scorer] or names
            assists=random.sample(helpers,k=min(2,len(helpers)))
            assist=assists[0]; assist2=assists[1] if len(assists)>1 else None
            team_name=home_team_name if side=='home' else away_team_name
            goal={'minute':minute,'scorer':scorer,'assist':assist,'assist2':assist2,'team':team_name}
            (home_goals if side=='home' else away_goals).append(goal)
            assist_text=f"; ассисты: {assist} и {assist2}" if assist2 else f"; ассист: {assist}"
            events.append({'minute':minute,'type':'goal','side':side,'text':f"🥅 ГООООЛ! {scorer} ({team_name}){assist_text}"})
    # Hockey teams typically produce dozens of shots, not just a handful.
    # Every non-goal shot is counted as a goalie save in the final box score.
    for side in ('home','away'):
        team_name=home_team_name if side=='home' else away_team_name
        goalie_name=away_goalie_name if side=='home' else home_goalie_name
        for _ in range(random.randint(27,36)):
            minute=random.randint(1,60)
            events.append({'minute':minute,'type':'shot','side':side,
                'text':f"🎯 Бросок команды {team_name}! 🧤 {goalie_name} отражает шайбу."})
    # Generate penalties and matching power-play opportunities (opposite team).
    for penalized_side in ('home','away'):
        penalized_team=home_team_name if penalized_side=='home' else away_team_name
        pp_side='away' if penalized_side=='home' else 'home'
        pp_team=away_team_name if penalized_side=='home' else home_team_name
        names=home_names if penalized_side=='home' else away_names
        for _ in range(random.randint(1,3)):
            minute=random.randint(1,60)
            player=random.choice(names) if names else 'Игрок команды'
            events.append({'minute':minute,'type':'penalty','side':penalized_side,
                'text':f"⚠️ {player} ({penalized_team}) удалён на 2 минуты."})
            events.append({'minute':minute,'type':'powerplay','side':pp_side,
                'text':f"🔵 Большинство у команды {pp_team}."})
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
            home_hits=sum(random.random()<min(.65,max(.2,.42+(home_power-away_goalie)*.008)) for _ in range(3))
            away_hits=sum(random.random()<min(.65,max(.2,.42+(away_power-home_goalie)*.008)) for _ in range(3))
            while home_hits==away_hits:
                home_hits+=int(random.random()<.42); away_hits+=int(random.random()<.42)
            if home_hits>away_hits:
                home+=1
            else:
                away+=1
            events.append({'minute':65,'type':'shootout','text':f'🎯 Буллиты: {home_hits} — {away_hits}.'})
        else:
            names=home_names if ot_side=='home' else away_names
            names=names or [f'Игрок {i}' for i in range(1,7)]
            scorer=random.choice(names)
            helpers=[n for n in names if n!=scorer] or names
            assists=random.sample(helpers,k=min(2,len(helpers)))
            assist=assists[0]; assist2=assists[1] if len(assists)>1 else None
            ot_team_name=home_team_name if ot_side=='home' else away_team_name
            goal={'minute':65,'scorer':scorer,'assist':assist,'assist2':assist2,'team':ot_team_name}
            if ot_side=='home':
                home+=1; home_goals.append(goal)
            else:
                away+=1; away_goals.append(goal)
            assist_text=f"; ассисты: {assist} и {assist2}" if assist2 else f"; ассист: {assist}"
            events.append({'minute':65,'type':'goal','side':ot_side,'text':f'🥅 ЗОЛОТОЙ ГОЛ В ОВЕРТАЙМЕ! {scorer} ({ot_team_name}){assist_text}'})
            events.append({'minute':65,'type':'overtime','text':'🏒 Овертайм завершён. Победитель определён.'})
    events.sort(key=lambda e:e['minute'])
    events.insert(0,{'minute':0,'type':'start','text':'🏒 Команды на точке вбрасывания. Судья готов — первое вбрасывание! Матч начался, 60 минут игрового времени.'})
    events.append({'minute':65 if ot else 60,'type':'final','text':f'🏁 Финальная сирена! Итоговый счёт: {home}:{away}.'})
    return home, away, ot, shootout, events, home_goals, away_goals
