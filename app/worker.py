"""Persistent match/auction worker. Run separately: python -m app.worker"""
import asyncio, os, random, logging
from datetime import timedelta
from sqlalchemy import select, and_, func
from aiogram import Bot
from .config import BOT_TOKEN, REDIS_URL
from .db import Session, init_db
from .models import Match, User, OwnedCard, Card, ClanMember, Clan, TournamentEntry, DailyTournament, Auction, EconomyLedger
from .game import simulate, team_power, chemistry, league_for_rating
from .services import now, ensure_season, ensure_daily_tournament, progress_after_match, settle_auction, expire_trade_offers

logging.basicConfig(level=os.getenv('LOG_LEVEL','INFO').upper(), format='%(asctime)s %(levelname)s [match-worker] %(message)s')
logger=logging.getLogger('kosh.match_worker')

TICK=float(os.getenv('MATCH_SECOND_PER_MINUTE','1.5'))
if TICK<=0: TICK=1.5

def goalie(cards): return next((c.ovr for c in cards if c.position=='G'),70)

async def current_lineup(session,user_id):
    rows=(await session.scalars(select(OwnedCard).where(OwnedCard.user_id==user_id,OwnedCard.slot!=''))).all()
    cards=[]
    for row in rows:
        c=await session.get(Card,row.card_id)
        if c: cards.append(c)
    return cards

async def choose_ai_roster(session,desired_power):
    """Choose a complete real-club six from imported cards; no synthetic club is presented as real."""
    clubs=(await session.scalars(select(Card.club).where(Card.active.is_(True),Card.source!='demo',Card.club.notin_(['','—','DEMO'])).distinct())).all()
    candidates=[]
    for club in clubs:
        roster=[]
        for pos in ('LW','C','RW','LD','RD','G'):
            card=await session.scalar(select(Card).where(Card.active.is_(True),Card.club==club,Card.position==pos,Card.source!='demo').order_by(Card.season.desc(),Card.ovr.desc()).limit(1))
            if card: roster.append(card)
        if len(roster)==6:
            candidates.append((abs(team_power(roster)-desired_power),club,roster))
    if not candidates: return None
    candidates.sort(key=lambda x:x[0])
    pool=candidates[:min(5,len(candidates))]
    chosen=random.choice(pool)
    return chosen[1],chosen[2]

async def send(bot, chat_id, text):
    if chat_id and bot:
        try: await bot.send_message(chat_id,text)
        except Exception:
            logger.exception('Telegram send failed: chat_id=%s text=%r', chat_id, text[:160])  # event remains in DB; user can fetch it using /match

async def prepare_match(match_id,bot=None):
    logger.info('Preparing match #%s', match_id)
    async with Session() as s:
        m=await s.get(Match,match_id)
        if not m or m.status not in ('queued','running'):
            logger.warning('Match #%s skipped: missing or status=%s', match_id, m.status if m else 'missing')
            return
        if m.status=='running' and m.events: return
        if m.mode in ('pvp','ranked','clan') and not m.away_user_id:
            m.status='searching'; await s.commit(); return
        home=await current_lineup(s,m.home_user_id)
        if len(home)!=6 or {c.position for c in home}!={'LW','C','RW','LD','RD','G'}:
            m.status='cancelled'; await s.commit(); await send(bot,m.home_user_id,'Матч отменён: сначала соберите полный состав из шести позиций.'); return
        m.home_power=team_power(home); m.home_goalie=goalie(home)
        m.home_card_ids=[c.id for c in home]
        home_chem=await chemistry(s,home); away_chem=0; away=[]
        if m.mode!='pve' and m.away_user_id:
            away=await current_lineup(s,m.away_user_id)
            if len(away)!=6 or {c.position for c in away}!={'LW','C','RW','LD','RD','G'}:
                m.status='cancelled'; await s.commit(); await send(bot,m.home_user_id,'Матч отменён: у соперника неполный состав.'); return
            m.away_power=team_power(away); m.away_goalie=goalie(away)
            m.away_card_ids=[c.id for c in away]
            away_chem=await chemistry(s,away)
        elif m.mode in ('pve','bot'):
            chosen=await choose_ai_roster(s,m.home_power)
            if chosen:
                m.away_name,away=chosen; m.away_power=team_power(away); m.away_goalie=goalie(away)
                m.away_card_ids=[c.id for c in away]; away_chem=await chemistry(s,away)
            else:
                m.away_name='KHL Bot' if m.mode=='bot' else f'AI · {m.league}'; m.away_power=random.randint(max(65,m.home_power-10),min(100,m.home_power+10))
                m.away_goalie=random.randint(max(65,m.away_power-4),min(100,m.away_power+4)); m.away_card_ids=[]
        home_names=[f'{c.player_name} · {c.ovr} OVR' for c in home]; away_names=[f'{c.player_name} · {c.ovr} OVR' for c in away]
        hs,as_,ot,so,events,hg,ag=simulate(m.home_power,m.away_power,m.home_goalie,m.away_goalie,home_chem,away_chem,home_names,away_names)
        if m.mode=='pve' and hs==as_:
            # Every PvE match has a winner; OT/skill is represented by tie-break goal.
            if random.random()<.5: hs+=1
            else: as_+=1
        m.home_score=hs; m.away_score=as_; m.shootout=so; m.overtime=ot; m.events=events
        m.home_goals=hg; m.away_goals=ag; m.minute=0; m.event_index=0; m.status='running'
        m.updated_at=now(); await s.commit()
        logger.info('Match #%s started: mode=%s home=%s away=%s score=%s:%s events=%s', m.id, m.mode, m.home_user_id, m.away_user_id, hs, as_, len(events))
        def roster_text(label, roster, fallback):
            if not roster:
                return f'{label}: {fallback}'
            return label+'\n'+'\n'.join(f'{c.position} — {c.player_name} · {c.ovr} OVR' for c in roster)
        lineup_message=(f'🏒 МАТЧ #{m.id} НАЧИНАЕТСЯ!\n\n'
            +roster_text(f'🔴 {m.home_name} · {m.home_power} OVR',home,'Состав не найден')
            +'\n\n'
            +roster_text(f'🔵 {m.away_name} · {m.away_power} OVR',away,'🤖 Состав бота'))
        await send(bot,m.home_user_id,lineup_message)
        if m.away_user_id:
            await send(bot,m.away_user_id,lineup_message)

async def finish_match(match_id,bot):
    async with Session() as s:
        m=await s.get(Match,match_id)
        if not m or m.status=='completed': return
        m.status='completed'; m.finished_at=now(); m.minute=65 if any(e.get('minute')==65 for e in (m.events or [])) else 60
        winner=m.home_user_id if m.home_score>m.away_score else m.away_user_id
        loser=m.away_user_id if winner==m.home_user_id else m.home_user_id
        home=await s.get(User,m.home_user_id); away=await s.get(User,m.away_user_id) if m.away_user_id else None
        is_ot=m.shootout
        overtime=m.overtime
        if m.mode in ('pve','tournament'):
            won=m.home_score>m.away_score
            reward=m.reward if won else int(m.reward*.5)
            if home: home.coins+=reward; home.games+=1; home.wins+=int(won); home.losses+=int(not won)
            if won and not is_ot and home: home.gems+=3
            if home: await progress_after_match(s,home.id,won,len(m.home_goals or []))
        else:
            if home: home.games+=1; home.wins+=int(winner==home.id); home.losses+=int(winner!=home.id)
            if away: away.games+=1; away.wins+=int(winner==away.id); away.losses+=int(winner!=away.id)
            if m.mode=='ranked' and home and away:
                diff=away.rating-home.rating
                raw=16 + max(-6,min(6,int(diff/250))) + min(6,abs(m.home_score-m.away_score))
                delta=max(5,min(28,raw))
                home.rating=max(0,home.rating+(delta if winner==home.id else -delta))
                away.rating=max(0,away.rating+(-delta if winner==home.id else delta))
                home.lifetime_rating=max(home.lifetime_rating,home.rating); away.lifetime_rating=max(away.lifetime_rating,away.rating)
                m.rating_change=delta if winner==home.id else -delta
            if home and winner==home.id and not is_ot: home.gems+=3
            if away and winner==away.id and not is_ot: away.gems+=3
            if m.mode=='pvp' and home and away:
                if winner==home.id: home.coins+=1000; away.coins+=500
                else: away.coins+=1000; home.coins+=500
            elif m.mode=='bot' and home:
                home.coins+=1000 if winner==home.id else 500
            if m.bet and home and away:
                stake=m.bet
                if winner==home.id: home.coins+=stake*2
                else: away.coins+=stake*2
            if m.mode=='clan':
                for uid, pts in [(home.id if home else 0,3 if winner==(home.id if home else -1) else 0),(away.id if away else 0,3 if winner==(away.id if away else -1) else 0)]:
                    member=await s.scalar(select(ClanMember).where(ClanMember.user_id==uid)) if uid else None
                    if member:
                        clan=await s.get(Clan,member.clan_id); clan.points+=pts; clan.rating=max(0,clan.rating+(10 if pts else -2))
            if home: await progress_after_match(s,home.id,winner==home.id,len(m.home_goals or []),home.rating)
            if away: await progress_after_match(s,away.id,winner==away.id,len(m.away_goals or []),away.rating)
            tournament_day=(m.created_at.date().isoformat() if m.created_at else now().date().isoformat())
            t=await s.scalar(select(DailyTournament).where(DailyTournament.tournament_date==tournament_day))
            if t:
                for uid,win,score,opp in [(m.home_user_id,winner==m.home_user_id,m.home_score,m.away_score),(m.away_user_id,winner==m.away_user_id,m.away_score,m.home_score)]:
                    if not uid: continue
                    entry=await s.scalar(select(TournamentEntry).where(TournamentEntry.tournament_id==t.id,TournamentEntry.user_id==uid))
                    if entry:
                        if win: entry.points+=2; entry.wins+=1
                        elif m.shootout: entry.points+=1
                        entry.goals+=score
        await s.commit()
        await send(bot,m.home_user_id,format_result(m,home,away,for_home=True))
        if away: await send(bot,m.away_user_id,format_result(m,home,away,for_home=False))


def format_result(m,home_user,away_user,for_home=True):
    own=m.home_score if for_home else m.away_score; opp=m.away_score if for_home else m.home_score
    own_goals=m.home_goals if for_home else m.away_goals; opp_goals=m.away_goals if for_home else m.home_goals
    title=f"🏒 Матч #{m.id} завершён\n{m.home_name} {m.home_score}:{m.away_score} {m.away_name}"
    if m.mode=='ranked': title+=f"\n⭐ Изменение рейтинга: {m.rating_change if for_home else -m.rating_change:+d}"
    title+=f"\n🥅 Голы: {own_goals} за вас / {opp_goals} соперника"
    home_events=m.events or []
    def stat(kind,side): return sum(1 for e in home_events if e.get('type')==kind and e.get('side')==side)
    side='home' if for_home else 'away'; other='away' if for_home else 'home'
    title+=f"\n🎯 Броски: {stat('shot',side)}:{stat('shot',other)} · 🧤 Сейвы: {stat('save',side)}:{stat('save',other)}"
    title+=f"\n⚠️ Удаления: {stat('penalty',side)}:{stat('penalty',other)} · 🔵 Большинство: {stat('powerplay',side)}:{stat('powerplay',other)}"
    scorers=[g.get('scorer') for g in (own_goals or [])]
    if scorers:
        from collections import Counter
        top=Counter(scorers).most_common(1)[0][0]; title+=f"\n⭐ Лучший игрок: {top}"
    title+=f"\n🪙 Награда начислена автоматически."
    title+='\n\nПосмотреть трансляцию: /match'
    return title

async def process_match(match_id,bot):
    logger.info('Processing match #%s', match_id)
    await prepare_match(match_id,bot)
    while True:
        async with Session() as s:
            m=await s.get(Match,match_id)
            if not m or m.status!='running': return
            next_min=min(65 if any(e.get('minute')==65 for e in m.events) else 60,m.minute+1)
            available=[e for e in m.events if e['minute']<=next_min]
            to_send=available[m.event_index:]
            m.minute=next_min; m.event_index=len(available); m.updated_at=now()
            done=m.minute>= (65 if any(e.get('minute')==65 for e in m.events) else 60)
            await s.commit()
            chat_ids=[m.home_user_id]+([m.away_user_id] if m.away_user_id else [])
            messages=[e['text'] for e in to_send]
        for msg in messages:
            for uid in chat_ids: await send(bot,uid,msg)
        if done: break
        await asyncio.sleep(TICK)
    await finish_match(match_id,bot)
    logger.info('Finished processing match #%s', match_id)

async def close_old_tournaments(session,bot):
    from .models import DailyTournament, TournamentEntry, OwnedCard, Card
    cutoff=now().date().isoformat()
    olds=(await session.scalars(select(DailyTournament).where(DailyTournament.tournament_date<cutoff,DailyTournament.status=='open'))).all()
    notifications=[]
    for old in olds:
        # Finish games/searches/challenges created on that date before locking the leaderboard.
        from datetime import datetime, timezone
        day_start=datetime.fromisoformat(old.tournament_date).replace(tzinfo=timezone.utc)
        day_end=day_start+timedelta(days=1)
        unfinished=await session.scalar(select(Match.id).where(Match.created_at>=day_start,Match.created_at<day_end,Match.status.in_(['searching','challenge','queued','running'])).limit(1))
        if unfinished:
            continue
        winner=await session.scalar(select(TournamentEntry).where(TournamentEntry.tournament_id==old.id).order_by(TournamentEntry.points.desc(),TournamentEntry.wins.desc(),TournamentEntry.goals.desc()).limit(1))
        if winner:
            pool=(await session.scalars(select(Card).where(Card.ovr>=80,Card.ovr<=90,Card.active.is_(True),Card.source!='demo'))).all()
            weights={80:20,81:17,82:14,83:12,84:10,85:8,86:7,87:5,88:3,89:2,90:1}
            by_ovr={ovr:[c for c in pool if c.ovr==ovr] for ovr in weights}
            available=[ovr for ovr,group in by_ovr.items() if group]
            if available:
                chosen_ovr=random.choices(available,weights=[weights[ovr] for ovr in available],k=1)[0]
                card=random.choice(by_ovr[chosen_ovr])
                session.add(OwnedCard(user_id=winner.user_id,card_id=card.id,slot=''))
                notifications.append((winner.user_id,f'🏆 Вы заняли 1-е место в турнире {old.tournament_date}! Получена карточка {card.player_name} · {card.ovr} OVR.'))
            else:
                notifications.append((winner.user_id,'🏆 Вы заняли 1-е место в турнире, но карточки 80–90 OVR ещё не загружены. Администратор должен импортировать призовой пул.'))
        old.status='completed'; old.rewarded_at=now()
    if olds:
        await session.commit()
    # Deliver only after the reward and completed status are durable, preventing duplicate prizes on restart.
    for user_id,message in notifications:
        await send(bot,user_id,message)

async def expire_challenges(session,bot):
    from .models import Match, User
    cutoff=now()-timedelta(minutes=30)
    rows=(await session.scalars(select(Match).where(Match.status=='challenge',Match.created_at<cutoff))).all()
    for m in rows:
        u=await session.get(User,m.home_user_id)
        if u: u.coins+=m.bet
        m.status='expired'
        await send(bot,m.home_user_id,f'⌛ Вызов #{m.id} истёк без ответа. Ставка возвращена.')
    if rows: await session.commit()

async def expire_match_searches(session,bot):
    cutoff=now()-timedelta(minutes=10)
    rows=(await session.scalars(select(Match).where(Match.status=='searching',Match.created_at<cutoff))).all()
    for match in rows:
        match.status='cancelled'
        await send(bot,match.home_user_id,f'⌛ Поиск соперника #{match.id} завершён без подбора за 10 минут. Попробуйте запустить поиск ещё раз.')
    if rows: await session.commit()

async def matchmaking(bot=None):
    notifications=[]
    async with Session() as s:
        waiting=(await s.scalars(
            select(Match).where(Match.status=='searching').order_by(Match.created_at,Match.id)
        )).all()
        if waiting:
            logger.info('Matchmaking found %s searching requests: %s', len(waiting), [(m.id, m.home_user_id, m.mode) for m in waiting])
        paired=set()

        # First pair waiting players. Do this BEFORE the 30-second bot fallback.
        for first in waiting:
            if first.id in paired:
                continue
            for second in waiting:
                if second.id==first.id or second.id in paired:
                    continue
                if second.home_user_id==first.home_user_id:
                    continue
                first_group='clan' if first.mode=='clan' else 'pvp'
                second_group='clan' if second.mode=='clan' else 'pvp'
                if first_group!=second_group:
                    continue
                if first_group=='clan':
                    c1=await s.scalar(select(ClanMember).where(ClanMember.user_id==first.home_user_id))
                    c2=await s.scalar(select(ClanMember).where(ClanMember.user_id==second.home_user_id))
                    if not c1 or not c2 or c1.clan_id==c2.clan_id:
                        continue

                # One row is the real match; remove the other player's queue placeholder.
                first.mode='clan' if first_group=='clan' else 'pvp'
                first.away_user_id=second.home_user_id
                first.away_name=second.home_name
                first.away_power=second.home_power
                first.away_goalie=second.home_goalie
                first.status='queued'
                notifications.append((first.home_user_id,
                    f'✅ Соперник найден! Матч #{first.id}: {first.home_name} — {second.home_name}. Сейчас отправим составы.'))
                notifications.append((second.home_user_id,
                    f'✅ Соперник найден! Ваш матч #{first.id}: {first.home_name} — {second.home_name}. Сейчас отправим составы.'))
                await s.delete(second)
                paired.update({first.id,second.id})
                break

        # Only searches still unmatched after pairing become bot matches at 30 seconds.
        cutoff=now()-timedelta(seconds=30)
        for match in waiting:
            if match.id in paired or match.status!='searching':
                continue
            created=match.created_at
            if created and created.tzinfo is None:
                created=created.replace(tzinfo=now().tzinfo)
            if not created or created>cutoff:
                continue
            match.mode='bot'
            match.away_user_id=0
            match.away_name='KHL Bot'
            match.away_power=max(60,min(100,match.home_power+random.randint(-5,5)))
            match.away_goalie=max(60,min(100,match.away_power+random.randint(-3,3)))
            match.status='queued'
            notifications.append((match.home_user_id,
                f'🤖 За 30 секунд соперник не найден. Матч #{match.id} начинается против бота!'))

        await s.commit()

    for user_id,message in notifications:
        await send(bot,user_id,message)
    if notifications:
        logger.info('Matchmaking sent %s notifications', len(notifications))

async def guarded_process_match(match_id,bot,redis_client=None):
    lock=None
    if redis_client is not None:
        try:
            lock=redis_client.lock(f'kosh:match:{match_id}',timeout=7200,blocking_timeout=0)
            acquired=await lock.acquire(blocking=False)
            if not acquired: return
        except Exception:
            lock=None
    try:
        await process_match(match_id,bot)
    except Exception:
        logger.exception('Unhandled error while processing match #%s', match_id)
        raise
    finally:
        if lock is not None:
            try: await lock.release()
            except Exception: pass

async def run_worker():
    logger.info('Worker starting; tick=%ss bot_token_configured=%s redis_configured=%s', TICK, bool(BOT_TOKEN), bool(REDIS_URL))
    await init_db()
    logger.info('Database initialized')
    bot=Bot(BOT_TOKEN) if BOT_TOKEN else None
    if bot is None:
        raise RuntimeError('BOT_TOKEN is missing; worker cannot send match updates')
    redis_client=None
    try:
        from redis.asyncio import Redis
        if REDIS_URL:
            redis_client=Redis.from_url(REDIS_URL,decode_responses=True)
            await redis_client.ping()
            logger.info('Redis connected; distributed match locks enabled')
        else:
            logger.warning('REDIS_URL is empty; running without distributed match locks')
    except Exception:
        logger.exception('Redis unavailable; continuing without distributed match locks')
        redis_client=None
    cycle=0
    active_tasks={}
    try:
        while True:
            cycle+=1
            try:
                async with Session() as s:
                    await ensure_season(s); await ensure_daily_tournament(s)
                    await expire_challenges(s,bot)
                    await expire_match_searches(s,bot)
                    await expire_trade_offers(s)
                    await close_old_tournaments(s,bot)
                    expired=(await s.scalars(select(Auction).where(Auction.status=='active',Auction.ends_at<=now()))).all()
                    for a in expired:
                        oc=await s.get(OwnedCard,a.owned_card_id); card=await s.get(Card,oc.card_id) if oc else None
                        settled=await settle_auction(s,a)
                        if not settled: continue
                        owner_id=a.owner_id; winner_id=a.winner_id; bid=a.highest_bid; minimum=a.min_price
                        oc_after=await s.get(OwnedCard,a.owned_card_id)
                        sold=bool(winner_id and bid>=minimum and oc_after and oc_after.user_id==winner_id and not oc_after.locked)
                        if sold:
                            payout=bid*90//100
                            await send(bot,owner_id,f'🔨 Аукцион #{a.id} завершён: карточка {card.player_name if card else ""} продана. Вам начислено {payout} 🪙 (комиссия 10%).')
                            await send(bot,winner_id,f'🏆 Вы выиграли аукцион #{a.id}. Карточка {card.player_name if card else ""} передана в коллекцию.')
                        else:
                            await send(bot,owner_id,f'🔨 Аукцион #{a.id} завершён без продажи. Карточка не передана; ставка возвращена.')
                            if winner_id: await send(bot,winner_id,f'Аукцион #{a.id}: минимальная цена не достигнута или лот недоступен. Ставка {bid} 🪙 возвращена.')
                await matchmaking(bot)
                async with Session() as s:
                    queued=(await s.scalars(select(Match.id).where(Match.status=='queued').limit(20))).all()
                    running=(await s.scalars(select(Match.id).where(Match.status=='running').limit(20))).all()
                    searching_count=await s.scalar(select(func.count(Match.id)).where(Match.status=='searching'))
                ids=list(dict.fromkeys(queued+running))
                # Run matches concurrently without blocking the matchmaking poll.
                # This keeps accepting/pairing new players while other matches play.
                for mid in ids:
                    task=active_tasks.get(mid)
                    if task is None or task.done():
                        active_tasks[mid]=asyncio.create_task(
                            guarded_process_match(mid,bot,redis_client),
                            name=f"match-{mid}"
                        )
                for mid,task in list(active_tasks.items()):
                    if task.done():
                        try:
                            task.result()
                        except asyncio.CancelledError:
                            pass
                        except Exception:
                            logger.exception('Match task #%s failed',mid)
                        active_tasks.pop(mid,None)
                if ids:
                    logger.info('Cycle %s: queued=%s running=%s searching=%s active_tasks=%s',cycle,queued,running,searching_count,len(active_tasks))
                elif cycle==1 or cycle%30==0:
                    logger.info('Worker alive; searching=%s queued=0 running=0 active_tasks=%s',searching_count,len(active_tasks))
                await asyncio.sleep(2)
            except asyncio.CancelledError:
                logger.info('Worker shutdown requested')
                raise
            except Exception:
                logger.exception('Worker cycle %s failed; retrying in 5 seconds',cycle)
                await asyncio.sleep(5)
    finally:
        for task in active_tasks.values():
            task.cancel()
        if active_tasks:
            await asyncio.gather(*active_tasks.values(),return_exceptions=True)
        await bot.session.close()

if __name__=='__main__':
    if not BOT_TOKEN: raise RuntimeError('Set BOT_TOKEN in .env before starting the worker')
    asyncio.run(run_worker())
