import random
from datetime import datetime, timezone, timedelta, date
from zoneinfo import ZoneInfo
from sqlalchemy import select, func, desc, or_
from sqlalchemy.exc import IntegrityError
from .models import (User, Card, OwnedCard, MarketListing, TradeOffer, Auction, AuctionBid,
    DailyTournament, TournamentEntry, Clan, ClanMember, BattlePassProgress, QuestProgress,
    Season, AuditLog, Match, PackConfig, ClanInvite, EconomyLedger)
from .game import POSITIONS, PACKS, RARITIES, RARITY_RANGES, rarity_pick, adjust_pack_odds, league_for_rating
from .seed import read_csv_cards
from .economy import available_bid_balance, auction_payout_and_fee

UTC=timezone.utc
MOSCOW=ZoneInfo('Europe/Moscow')

def now(): return datetime.now(MOSCOW)
def season_key(dt=None):
    dt=dt or now(); q=((dt.month-1)//3)+1
    return f'{dt.year}-Q{q}'

async def get_or_create_user(session, tg_user):
    user=await session.get(User, tg_user.id)
    if user:
        user.username=(tg_user.username or '')
        user.first_name=(tg_user.first_name or '')
        user.last_name=(tg_user.last_name or '')
        return user, False
    user=User(id=tg_user.id, username=(tg_user.username or ''), first_name=(tg_user.first_name or ''), last_name=(tg_user.last_name or ''), coins=5000, gems=20)
    session.add(user); await session.flush()
    # Give a new user a complete starter lineup from the active real catalogue.
    # Choose the weakest available card for each position to avoid demo cards.
    starter=[]
    for pos in POSITIONS:
        card=await session.scalar(select(Card).where(
            Card.active.is_(True), Card.source!='demo', Card.position==pos
        ).order_by(Card.ovr.asc(), Card.id.asc()).limit(1))
        if card:
            starter.append((pos,card))
    for pos,card in starter:
        session.add(OwnedCard(user_id=user.id,card_id=card.id,slot=pos))
    await session.commit()
    return user, True

async def ensure_daily_tournament(session):
    day=now().date().isoformat()
    t=await session.scalar(select(DailyTournament).where(DailyTournament.tournament_date==day))
    if not t:
        t=DailyTournament(tournament_date=day,status='open'); session.add(t); await session.commit()
    return t

async def ensure_season(session):
    key=season_key(); active=await session.scalar(select(Season).where(Season.active.is_(True)))
    if active and active.season_key==key: return active
    if active:
        active.active=False
        users=(await session.scalars(select(User))).all()
        for u in users: u.rating=0
    # Quarter-aligned season schedule, three months long.
    dt=now(); start_month=((dt.month-1)//3)*3+1
    starts=datetime(dt.year,start_month,1,tzinfo=MOSCOW)
    if start_month==10: ends=datetime(dt.year+1,1,1,tzinfo=MOSCOW)
    else: ends=datetime(dt.year,start_month+3,1,tzinfo=MOSCOW)
    s=Season(season_key=key,starts_at=starts,ends_at=ends,active=True); session.add(s); await session.commit()
    return s

async def lineup(session, user_id):
    owned=(await session.scalars(select(OwnedCard).where(OwnedCard.user_id==user_id))).all()
    result=[]
    for oc in owned:
        if oc.slot in POSITIONS:
            c=await session.get(Card,oc.card_id)
            if c: result.append((oc,c))
    return result

async def audit(session, actor, action, target=0, details=''):
    session.add(AuditLog(actor_id=actor,action=action,target_id=target,details=details))

async def claim_daily(session, user):
    user=await session.scalar(select(User).where(User.id==user.id).with_for_update())
    today=now().date().isoformat()
    if user.daily_claimed==today: return None
    coins=random.randint(800,1600)
    bonus=random.random()<0.18
    if bonus: coins+=random.randint(300,700)
    gems=random.randint(1,3) if random.random()<0.16 else 0
    user.daily_claimed=today; user.coins+=coins; user.gems+=gems
    await session.commit()
    return coins,gems,bonus

def pack_card_exclusion():
    # Exclusive Semён Koshelev variants are granted only through their
    # dedicated Battle Pass reward, never as random pack drops.
    return ~((Card.player_name=='Семён Кошелев') & Card.ovr.in_([95,99]))


async def open_pack(session,user,pack_name):
    if pack_name not in PACKS: raise ValueError('Неизвестный тип пака.')
    user=await session.scalar(select(User).where(User.id==user.id).with_for_update())
    cfg=await session.get(PackConfig,pack_name)
    data={'price':cfg.price,'currency':cfg.currency,'odds':cfg.odds} if cfg else PACKS[pack_name]
    currency=data['currency']; price=data['price']
    if getattr(user,currency)<price: raise ValueError('Недостаточно валюты.')
    adjusted_odds=adjust_pack_odds(pack_name, data['odds'])
    rarity=rarity_pick(adjusted_odds)
    excluded=pack_card_exclusion()
    cards=(await session.scalars(select(Card).where(Card.active.is_(True),Card.rarity==rarity,Card.source!='demo',excluded))).all()
    if not cards:
        # Never issue artificial DEMO cards: use the nearest real rarity available.
        available=(await session.scalars(select(Card.rarity).where(
            Card.active.is_(True), Card.source!='demo', excluded
        ).distinct())).all()
        if not available:
            raise ValueError('В базе пока нет подходящих карточек для паков. Эксклюзивные карточки выдаются отдельно.')
        target=RARITY_RANGES[rarity]
        target_mid=(target[0]+target[1])/2
        nearest=min(available, key=lambda value: abs(
            (RARITY_RANGES.get(value, target)[0]+RARITY_RANGES.get(value, target)[1])/2-target_mid
        ))
        cards=(await session.scalars(select(Card).where(
            Card.active.is_(True), Card.rarity==nearest, Card.source!='demo', excluded
        ))).all()
    card=random.choice(cards)
    setattr(user,currency,getattr(user,currency)-price)
    owned=OwnedCard(user_id=user.id,card_id=card.id,slot='')
    session.add(owned); await progress_pack_quest(session,user.id); await session.commit(); await session.refresh(owned)
    return owned,card

async def auto_best_lineup(session,user_id):
    """Fill every position with the highest-OVR available owned card."""
    owned=(await session.scalars(select(OwnedCard).where(OwnedCard.user_id==user_id).with_for_update())).all()
    cards_by_id={}
    for oc in owned:
        card=await session.get(Card,oc.card_id)
        if card:
            cards_by_id[oc.id]=card
    fixed={oc.slot:oc for oc in owned if oc.slot in POSITIONS and oc.locked}
    chosen={}
    for pos in POSITIONS:
        if pos in fixed:
            chosen[pos]=fixed[pos]
            continue
        candidates=[oc for oc in owned if not oc.locked and cards_by_id.get(oc.id) and cards_by_id[oc.id].position==pos]
        if candidates:
            chosen[pos]=max(candidates,key=lambda oc:(cards_by_id[oc.id].ovr,oc.id))
    if len(chosen)!=len(POSITIONS):
        missing=[pos for pos in POSITIONS if pos not in chosen]
        raise ValueError('Не хватает карточек для полного состава. Нет позиций: '+', '.join(missing)+'.')
    changed=False
    for oc in owned:
        target=next((pos for pos,item in chosen.items() if item.id==oc.id),'')
        if oc.slot!=target:
            if oc.locked:
                raise ValueError('Нельзя изменить состав: одна из карточек заблокирована.')
            oc.slot=target
            changed=True
    if changed:
        await session.commit()
    return changed


async def equip_card(session,user_id,owned_id,slot):
    if slot not in POSITIONS: raise ValueError('Неизвестная позиция.')
    oc=await session.scalar(select(OwnedCard).where(OwnedCard.id==owned_id).with_for_update())
    if not oc or oc.user_id!=user_id: raise ValueError('Карточка не найдена в вашей коллекции.')
    if oc.locked: raise ValueError('Карточка заблокирована торговой операцией.')
    card=await session.get(Card,oc.card_id)
    if card.position!=slot: raise ValueError(f'На позицию {slot} подходит только карточка той же позиции. Эта карточка: {card.position}.')
    existing=await session.scalar(select(OwnedCard).where(OwnedCard.user_id==user_id,OwnedCard.slot==slot).with_for_update())
    if existing and existing.id!=oc.id: existing.slot=''
    oc.slot=slot
    try: await session.commit()
    except IntegrityError:
        await session.rollback(); raise ValueError('Не удалось изменить состав. Повторите попытку.')
    return card

async def start_pve_match(session,user,league):
    from .game import PVE_REWARDS, team_power
    user=await session.scalar(select(User).where(User.id==user.id).with_for_update())
    if league not in PVE_REWARDS: raise ValueError('Неизвестная лига соперника.')
    active=await session.scalar(select(Match.id).where(or_(Match.home_user_id==user.id,Match.away_user_id==user.id),Match.status.in_(['queued','searching','challenge','running'])))
    if active: raise ValueError('У вас уже есть активный матч или поиск соперника.')
    items=await lineup(session,user.id); cards=[c for _,c in items]
    if len(items)!=6 or {c.position for c in cards}!=set(POSITIONS): raise ValueError('Сначала соберите полный состав из шести разных позиций.')
    p=team_power(cards); goalie=next(c.ovr for c in cards if c.position=='G')
    opp=random.randint(max(65,p-10),min(100,p+10)); opp_g=random.randint(max(65,opp-4),min(100,opp+4))
    m=Match(mode='pve',status='queued',home_user_id=user.id,away_user_id=0,
        home_name=user.first_name or f'Игрок {user.id}',away_name=f'AI · {league}',home_power=p,
        away_power=opp,home_goalie=goalie,away_goalie=opp_g,league=league,reward=PVE_REWARDS[league])
    session.add(m); await session.commit(); await session.refresh(m); return m

async def start_pvp_queue(session,user,ranked=False,mode=None):
    user=await session.scalar(select(User).where(User.id==user.id).with_for_update())
    items=await lineup(session,user.id); cards=[c for _,c in items]
    if len(items)!=6 or {c.position for c in cards}!=set(POSITIONS): raise ValueError('Сначала соберите полный состав из шести игроков.')
    already=await session.scalar(select(Match.id).where(or_(Match.home_user_id==user.id,Match.away_user_id==user.id),Match.status.in_(['queued','searching','challenge','running'])))
    if already: raise ValueError('У вас уже есть активный поиск или вызов.')
    from .game import team_power
    p=team_power(cards); g=next(c.ovr for c in cards if c.position=='G')
    match_mode='clan' if mode=='clan' else ('ranked' if ranked else 'pvp')
    if match_mode not in ('pvp','ranked','clan'): raise ValueError('Неизвестный PvP-режим.')
    # Join the oldest compatible waiting player immediately instead of relying
    # only on the background worker's polling interval.
    candidate_modes=[match_mode]
    candidates=(await session.scalars(
        select(Match).where(Match.status=='searching',Match.mode.in_(candidate_modes),
            Match.home_user_id!=user.id).order_by(Match.created_at,Match.id).with_for_update()
    )).all()
    for candidate in candidates:
        if match_mode=='clan':
            mine=await session.scalar(select(ClanMember).where(ClanMember.user_id==user.id))
            theirs=await session.scalar(select(ClanMember).where(ClanMember.user_id==candidate.home_user_id))
            if not mine or not theirs or mine.clan_id==theirs.clan_id:
                continue
        candidate.mode=match_mode
        candidate.away_user_id=user.id
        candidate.away_name=user.first_name or str(user.id)
        candidate.away_power=p
        candidate.away_goalie=g
        candidate.status='queued'
        await session.commit()
        await session.refresh(candidate)
        return candidate

    m=Match(mode=match_mode,status='searching',home_user_id=user.id,
        home_name=user.first_name or str(user.id),home_power=p,home_goalie=g)
    session.add(m); await session.commit(); await session.refresh(m); return m

async def create_market_listing(session,user_id,owned_id,price):
    if price<1 or price>100_000_000: raise ValueError('Цена должна быть от 1 до 100 000 000 монет.')
    oc=await session.scalar(select(OwnedCard).where(OwnedCard.id==owned_id).with_for_update())
    if not oc or oc.user_id!=user_id or oc.slot or oc.locked: raise ValueError('Можно выставлять только свободную карточку из вашей коллекции.')
    oc.locked=True; listing=MarketListing(seller_id=user_id,owned_card_id=oc.id,price=price)
    session.add(listing)
    try: await session.commit()
    except IntegrityError: await session.rollback(); raise ValueError('Эта карточка уже участвует в торговой операции.')
    return listing

async def buy_market_listing(session,buyer,listing_id):
    listing=await session.scalar(select(MarketListing).where(MarketListing.id==listing_id).with_for_update())
    if not listing or listing.status!='active': raise ValueError('Лот уже недоступен.')
    if listing.seller_id==buyer.id: raise ValueError('Нельзя купить собственный лот.')
    buyer=await session.scalar(select(User).where(User.id==buyer.id).with_for_update())
    if buyer.coins<listing.price: raise ValueError('Недостаточно монет.')
    oc=await session.scalar(select(OwnedCard).where(OwnedCard.id==listing.owned_card_id).with_for_update())
    seller=await session.scalar(select(User).where(User.id==listing.seller_id).with_for_update())
    if not oc or not seller or oc.user_id!=seller.id: raise ValueError('Ошибка владения карточкой. Обратитесь к администратору.')
    buyer.coins-=listing.price; seller.coins+=listing.price
    oc.user_id=buyer.id; oc.locked=False; listing.buyer_id=buyer.id; listing.status='sold'
    await session.commit(); return oc

async def create_trade(session,sender_id,receiver_id):
    if sender_id==receiver_id: raise ValueError('Нельзя обмениваться с самим собой.')
    receiver=await session.get(User,receiver_id)
    if not receiver or receiver.is_banned: raise ValueError('Игрок не найден или заблокирован. Он должен хотя бы раз запустить бота.')
    offer=TradeOffer(sender_id=sender_id,receiver_id=receiver_id,status='pending')
    session.add(offer); await session.commit(); await session.refresh(offer); return offer

async def set_trade_card(session,offer_id,user_id,owned_id):
    offer=await session.scalar(select(TradeOffer).where(TradeOffer.id==offer_id).with_for_update())
    if not offer or offer.status!='negotiating' or user_id not in (offer.sender_id,offer.receiver_id): raise ValueError('Обмен сейчас недоступен.')
    oc=await session.scalar(select(OwnedCard).where(OwnedCard.id==owned_id).with_for_update())
    if not oc or oc.user_id!=user_id or oc.slot or oc.locked: raise ValueError('Карточка недоступна: нужна свободная карточка.')
    mine=offer.give_card_ids if user_id==offer.sender_id else offer.receive_card_ids
    if owned_id in mine: raise ValueError('Эта карточка уже добавлена в обмен.')
    oc.locked=True; mine.append(owned_id)
    offer.sender_confirmed=False; offer.receiver_confirmed=False
    await session.commit(); return offer

async def set_trade_currency(session,offer_id,user_id,kind,amount):
    if kind not in ('coins','gems') or amount<0: raise ValueError('Некорректная сумма.')
    offer=await session.scalar(select(TradeOffer).where(TradeOffer.id==offer_id).with_for_update())
    if not offer or offer.status!='negotiating' or user_id not in (offer.sender_id,offer.receiver_id): raise ValueError('Обмен сейчас недоступен.')
    user=await session.scalar(select(User).where(User.id==user_id).with_for_update())
    if amount>getattr(user,kind): raise ValueError(f'Недостаточно {"монет" if kind=="coins" else "алмазов"}.')
    prefix='sender' if user_id==offer.sender_id else 'receiver'
    setattr(offer,f'{prefix}_{kind}',amount)
    offer.sender_confirmed=False; offer.receiver_confirmed=False
    await session.commit(); return offer

async def confirm_trade(session,offer_id,user_id):
    offer=await session.scalar(select(TradeOffer).where(TradeOffer.id==offer_id).with_for_update())
    if not offer or offer.status!='negotiating' or user_id not in (offer.sender_id,offer.receiver_id): raise ValueError('Обмен сейчас недоступен.')
    if user_id==offer.sender_id: offer.sender_confirmed=True
    else: offer.receiver_confirmed=True
    if not (offer.sender_confirmed and offer.receiver_confirmed):
        await session.commit(); return False
    sender=await session.scalar(select(User).where(User.id==offer.sender_id).with_for_update())
    receiver=await session.scalar(select(User).where(User.id==offer.receiver_id).with_for_update())
    if sender.coins<offer.sender_coins or sender.gems<offer.sender_gems or receiver.coins<offer.receiver_coins or receiver.gems<offer.receiver_gems:
        offer.sender_confirmed=False; offer.receiver_confirmed=False; await session.commit()
        raise ValueError('Баланс изменился. Проверьте суммы и подтвердите обмен заново.')
    ids=list(offer.give_card_ids or [])+list(offer.receive_card_ids or [])
    rows=(await session.scalars(select(OwnedCard).where(OwnedCard.id.in_(ids)).order_by(OwnedCard.id).with_for_update())).all() if ids else []
    by_id={x.id:x for x in rows}
    if len(by_id)!=len(set(ids)) or any(not by_id[i].locked or by_id[i].slot for i in ids): raise ValueError('Одна из карточек больше недоступна.')
    for cid in offer.give_card_ids:
        oc=by_id[cid]
        if oc.user_id!=sender.id: raise ValueError('Карточка отправителя изменила владельца.')
        oc.user_id=receiver.id; oc.locked=False
    for cid in offer.receive_card_ids:
        oc=by_id[cid]
        if oc.user_id!=receiver.id: raise ValueError('Карточка получателя изменила владельца.')
        oc.user_id=sender.id; oc.locked=False
    sender.coins+=offer.receiver_coins-offer.sender_coins; receiver.coins+=offer.sender_coins-offer.receiver_coins
    sender.gems+=offer.receiver_gems-offer.sender_gems; receiver.gems+=offer.sender_gems-offer.receiver_gems
    offer.status='accepted'; await session.commit(); return True

async def resolve_trade(session,offer_id,receiver_id,accept):
    offer=await session.scalar(select(TradeOffer).where(TradeOffer.id==offer_id).with_for_update())
    if not offer or offer.status!='pending' or offer.receiver_id!=receiver_id: raise ValueError('Предложение недоступно.')
    offer.status='negotiating' if accept else 'declined'
    await session.commit(); return offer

async def submit_auction_card(session,user_id,owned_id):
    oc=await session.scalar(select(OwnedCard).where(OwnedCard.id==owned_id).with_for_update())
    if not oc or oc.user_id!=user_id or oc.locked: raise ValueError('Эта карточка недоступна для аукциона.')
    # A card can be selected directly from the lineup; remove it from its slot before locking.
    oc.slot=''
    oc.locked=True
    auction=Auction(owner_id=user_id,owned_card_id=owned_id,start_price=0,status='submitted',ends_at=now()+timedelta(days=1))
    session.add(auction); await session.commit(); await session.refresh(auction); return auction

async def place_bid(session,user,auction_id,amount):
    # Lock the auction first so two concurrent bids cannot both replace the same
    # leader. Then lock affected wallets in a stable ID order to reduce deadlocks.
    a=await session.scalar(select(Auction).where(Auction.id==auction_id).with_for_update().execution_options(populate_existing=True))
    end=a.ends_at if a and a.ends_at else now()
    if end.tzinfo is None: end=end.replace(tzinfo=UTC)
    if not a or a.status!='active' or end<=now(): raise ValueError('Аукцион не активен.')
    if a.owner_id==user.id: raise ValueError('Владелец не может ставить на собственную карточку.')
    minimum=a.start_price if a.highest_bid==0 else a.highest_bid+a.bid_step
    if amount<1 or amount>a.max_bid or amount<minimum: raise ValueError('Ставка не соответствует минимальному шагу или лимиту.')
    previous=await session.scalar(select(AuctionBid).where(AuctionBid.auction_id==a.id).order_by(AuctionBid.amount.desc(),AuctionBid.created_at.desc(),AuctionBid.id.desc()).with_for_update().execution_options(populate_existing=True))
    lock_ids=sorted({user.id, previous.bidder_id} if previous else {user.id})
    locked_users={}
    for uid in lock_ids:
        locked_users[uid]=await session.scalar(select(User).where(User.id==uid).with_for_update().execution_options(populate_existing=True))
    bidder=locked_users.get(user.id)
    if not bidder: raise ValueError('Игрок не найден.')
    # The current winner's escrow is credited back as part of the same transaction.
    # This lets that same player raise their bid using the released old stake.
    available=available_bid_balance(bidder.coins, previous.amount if previous else 0, bool(previous and previous.bidder_id==bidder.id))
    if available<amount: raise ValueError('Недостаточно монет для ставки.')
    if previous:
        old_user=locked_users.get(previous.bidder_id)
        if not old_user: raise ValueError('Предыдущий участник аукциона не найден.')
        old_user.coins+=previous.amount
        session.add(EconomyLedger(user_id=old_user.id,currency='coins',amount=previous.amount,reason='auction_bid_refund',reference_id=a.id))
    bidder.coins-=amount
    session.add(EconomyLedger(user_id=bidder.id,currency='coins',amount=-amount,reason='auction_bid_escrow',reference_id=a.id))
    a.highest_bid=amount; a.winner_id=bidder.id
    session.add(AuctionBid(auction_id=a.id,bidder_id=bidder.id,amount=amount)); await session.commit()
    return a

async def settle_auction(session,a):
    # Reload and lock the authoritative row; the worker may have fetched a stale
    # object and multiple workers must not pay out the same auction twice.
    auction=await session.scalar(select(Auction).where(Auction.id==a.id).with_for_update().execution_options(populate_existing=True))
    if not auction or auction.status!='active': return False
    end=auction.ends_at
    if end.tzinfo is None: end=end.replace(tzinfo=UTC)
    if end>now(): return False
    oc=await session.scalar(select(OwnedCard).where(OwnedCard.id==auction.owned_card_id).with_for_update().execution_options(populate_existing=True))
    user_ids=sorted({auction.owner_id,auction.winner_id} if auction.winner_id else {auction.owner_id})
    locked_users={}
    for uid in user_ids:
        locked_users[uid]=await session.scalar(select(User).where(User.id==uid).with_for_update().execution_options(populate_existing=True))
    owner=locked_users.get(auction.owner_id)
    winner=locked_users.get(auction.winner_id) if auction.winner_id else None
    card_is_still_owned=(oc is not None and owner is not None and oc.user_id==owner.id and oc.locked)
    if auction.winner_id and auction.highest_bid>=auction.min_price and winner and owner and card_is_still_owned:
        payout,fee=auction_payout_and_fee(auction.highest_bid)
        oc.user_id=winner.id; oc.locked=False; owner.coins+=payout
        session.add(EconomyLedger(user_id=owner.id,currency='coins',amount=payout,reason='auction_seller_payout',reference_id=auction.id))
        session.add(EconomyLedger(user_id=0,currency='coins',amount=fee,reason='auction_admin_fee',reference_id=auction.id))
    else:
        # Refund escrow if the reserve is not met or the listing is no longer valid.
        if auction.winner_id and winner:
            winner.coins+=auction.highest_bid
            session.add(EconomyLedger(user_id=winner.id,currency='coins',amount=auction.highest_bid,reason='auction_bid_refund',reference_id=auction.id))
        if oc and owner and oc.user_id==owner.id: oc.locked=False
    auction.status='completed'; await session.commit(); return True

async def expire_trade_offers(session):
    cutoff=now().astimezone(UTC)-timedelta(days=7)
    offers=(await session.scalars(select(TradeOffer).where(TradeOffer.status=='pending',TradeOffer.created_at<cutoff))).all()
    for offer in offers:
        for cid in offer.give_card_ids+offer.receive_card_ids:
            oc=await session.get(OwnedCard,cid)
            if oc: oc.locked=False
        offer.status='expired'
    if offers: await session.commit()

async def ensure_daily_tournament_participants(session):
    """Every existing, non-banned bot player is included in today's tournament automatically."""
    t=await ensure_daily_tournament(session)
    if t.status!='open':
        return t
    users=(await session.scalars(select(User.id).where(User.is_banned.is_(False)))).all()
    existing=set((await session.scalars(
        select(TournamentEntry.user_id).where(TournamentEntry.tournament_id==t.id)
    )).all())
    for user_id in users:
        if user_id not in existing:
            session.add(TournamentEntry(tournament_id=t.id,user_id=user_id))
    await session.flush()
    return t

async def save_pack_config(session,name,price,currency,odds):
    if name not in PACKS: raise ValueError('Неизвестный тип пака.')
    if price<1 or currency not in ('coins','gems'): raise ValueError('Некорректная цена/валюта.')
    if not odds or sum(int(p) for _,p in odds)!=100: raise ValueError('Сумма вероятностей должна быть ровно 100%.')
    if any(r not in RARITIES or int(p)<0 for r,p in odds): raise ValueError('Редкость или вероятность указана неверно.')
    cfg=await session.get(PackConfig,name)
    data=[(r,int(p)) for r,p in odds]
    if cfg: cfg.price=price; cfg.currency=currency; cfg.odds=data
    else: session.add(PackConfig(name=name,price=price,currency=currency,odds=data))
    await session.commit(); return cfg or await session.get(PackConfig,name)

async def create_clan_invite(session,inviter_id,invitee_id):
    member=await session.scalar(select(ClanMember).where(ClanMember.user_id==inviter_id))
    if not member or member.role not in ('owner','admin'): raise ValueError('Приглашать могут владелец и администратор клана.')
    target=await session.get(User,invitee_id)
    if not target or target.is_banned: raise ValueError('Игрок не найден или заблокирован.')
    if await session.scalar(select(ClanMember.id).where(ClanMember.user_id==invitee_id)): raise ValueError('Игрок уже состоит в клане.')
    clan_size=await session.scalar(select(func.count(ClanMember.id)).where(ClanMember.clan_id==member.clan_id))
    if clan_size>=25: raise ValueError('В клане максимум 25 участников.')
    existing=await session.scalar(select(ClanInvite).where(ClanInvite.invitee_id==invitee_id,ClanInvite.status=='pending'))
    if existing: raise ValueError('У игрока уже есть ожидающее приглашение.')
    invite=ClanInvite(clan_id=member.clan_id,inviter_id=inviter_id,invitee_id=invitee_id)
    session.add(invite); await session.commit(); await session.refresh(invite); return invite

async def resolve_clan_invite(session,invite_id,invitee_id,accept):
    inv=await session.get(ClanInvite,invite_id)
    if not inv or inv.invitee_id!=invitee_id or inv.status!='pending': raise ValueError('Приглашение недоступно.')
    if accept:
        if await session.scalar(select(ClanMember.id).where(ClanMember.user_id==invitee_id)): raise ValueError('Вы уже состоите в клане.')
        clan_size=await session.scalar(select(func.count(ClanMember.id)).where(ClanMember.clan_id==inv.clan_id))
        if clan_size>=25: raise ValueError('В клане уже 25 участников. Приглашение нельзя принять.')
        session.add(ClanMember(clan_id=inv.clan_id,user_id=invitee_id,role='member')); inv.status='accepted'
    else: inv.status='declined'
    await session.commit(); return inv

async def leave_clan(session,user_id):
    mem=await session.scalar(select(ClanMember).where(ClanMember.user_id==user_id))
    if not mem: raise ValueError('Вы не состоите в клане.')
    if mem.role=='owner': raise ValueError('Владелец должен передать роль владельца другому участнику перед выходом.')
    await session.delete(mem); await session.commit()

async def transfer_clan_owner(session,actor_id,target_id):
    actor=await session.scalar(select(ClanMember).where(ClanMember.user_id==actor_id))
    target=await session.scalar(select(ClanMember).where(ClanMember.user_id==target_id))
    if not actor or actor.role!='owner' or not target or target.clan_id!=actor.clan_id or target.user_id==actor_id:
        raise ValueError('Передать владение можно только участнику вашего клана.')
    actor.role='admin'; target.role='owner'
    clan=await session.get(Clan,actor.clan_id); clan.owner_id=target_id
    await session.commit(); return clan

async def set_clan_role(session,actor_id,target_id,role):
    actor=await session.scalar(select(ClanMember).where(ClanMember.user_id==actor_id))
    target=await session.scalar(select(ClanMember).where(ClanMember.user_id==target_id))
    if not actor or actor.role!='owner' or not target or target.clan_id!=actor.clan_id: raise ValueError('Недостаточно прав или участник не найден.')
    if role not in ('admin','member'): raise ValueError('Роль должна быть admin или member.')
    if target.role=='owner': raise ValueError('Нельзя изменить роль владельца этой командой.')
    target.role=role; await session.commit()

async def kick_clan_member(session,actor_id,target_id):
    actor=await session.scalar(select(ClanMember).where(ClanMember.user_id==actor_id))
    target=await session.scalar(select(ClanMember).where(ClanMember.user_id==target_id))
    if not actor or not target or actor.clan_id!=target.clan_id:
        raise ValueError('Игрок не состоит в вашем клане.')
    if target.role=='owner' or actor.role not in ('owner','admin'):
        raise ValueError('Недостаточно прав для исключения участника.')
    if actor.role=='admin' and target.role!='member':
        raise ValueError('Администратор может исключать только обычных участников.')
    if actor_id==target_id:
        raise ValueError('Используйте команду выхода из клана.')
    await session.delete(target)
    await session.commit()

async def create_clan(session,user_id,name):
    name=name.strip()
    if len(name)<3 or len(name)>30: raise ValueError('Название клана: от 3 до 30 символов.')
    member=await session.scalar(select(ClanMember).where(ClanMember.user_id==user_id))
    if member: raise ValueError('Вы уже состоите в клане.')
    clan=Clan(name=name,owner_id=user_id); session.add(clan); await session.flush()
    session.add(ClanMember(clan_id=clan.id,user_id=user_id,role='owner'))
    try: await session.commit()
    except IntegrityError: await session.rollback(); raise ValueError('Такое название клана уже занято.')
    return clan

async def join_clan(session,user_id,clan_id):
    old=await session.scalar(select(ClanMember).where(ClanMember.user_id==user_id))
    clan=await session.get(Clan,clan_id)
    if old: raise ValueError('Сначала выйдите из текущего клана.')
    if not clan: raise ValueError('Клан не найден.')
    count=await session.scalar(select(func.count(ClanMember.id)).where(ClanMember.clan_id==clan.id))
    if count>=25: raise ValueError('В клане уже 25 участников.')
    session.add(ClanMember(clan_id=clan.id,user_id=user_id,role='member')); await session.commit(); return clan

BP_MAX_LEVEL=100
BP_MAX_XP=BP_MAX_LEVEL*100+5*BP_MAX_LEVEL*(BP_MAX_LEVEL-1)

def bp_xp_required(level):
    level=max(0,min(BP_MAX_LEVEL,int(level)))
    return level*100+5*level*(level-1)

def bp_level_from_xp(xp):
    xp=max(0,int(xp or 0))
    level=0
    while level<BP_MAX_LEVEL and xp>=bp_xp_required(level+1):
        level+=1
    return level

def ensure_bp_xp_curve(bp):
    # Convert legacy linear XP once while preserving the old level and remainder.
    if not getattr(bp,'xp_curve',False):
        old_xp=max(0,int(bp.xp or 0))
        old_level=min(BP_MAX_LEVEL,old_xp//100)
        remainder=old_xp%100 if old_level<BP_MAX_LEVEL else 0
        bp.xp=bp_xp_required(old_level)+remainder
        bp.xp_curve=True
    return bp

async def _increment_quest(session,user_id,key,period,amount=1,cap=None):
    q=await session.scalar(select(QuestProgress).where(QuestProgress.user_id==user_id,QuestProgress.quest_key==key,QuestProgress.period_key==period).with_for_update())
    if not q: q=QuestProgress(user_id=user_id,quest_key=key,period_key=period,progress=0,claimed=False); session.add(q)
    if not q.claimed: q.progress=q.progress+amount if cap is None else min(cap,q.progress+amount)
    return q

async def progress_after_match(session,user_id,won,goals=0,rating=0):
    today=now().date().isoformat(); week=f'{now().date().isocalendar().year}-W{now().date().isocalendar().week}'; month=now().strftime('%Y-%m'); season=season_key()
    for key,period,amount,cap in [
        ('play3',today,1,5),('win1',today,1 if won else 0,3),('score5goals',today,goals,10),
        ('play20',week,1,35),('win10',week,1 if won else 0,20),
        ('play60month',month,1,100),('win20month',month,1 if won else 0,40),
        ('play100',season,1,200),('win50',season,1 if won else 0,100),
        ('reach_khl',season,1 if rating>=3001 else 0,1)]:
        if amount: await _increment_quest(session,user_id,key,period,amount,cap)
    bp=await session.scalar(select(BattlePassProgress).where(BattlePassProgress.user_id==user_id,BattlePassProgress.season==season))
    if not bp: bp=BattlePassProgress(user_id=user_id,season=season,xp=0,premium=False,claimed_free=[],claimed_premium=[],xp_curve=True); session.add(bp)
    ensure_bp_xp_curve(bp)
    bp.xp=min(BP_MAX_XP,bp.xp+100)

async def progress_pack_quest(session,user_id):
    await _increment_quest(session,user_id,'open50packs',season_key(),1,100)

async def claim_quest(session,user_id,key):
    today=now().date().isoformat(); week=f'{now().date().isocalendar().year}-W{now().date().isocalendar().week}'; month=now().strftime('%Y-%m'); season=season_key()
    target={'play3':(today,5,2000,3,200),'win1':(today,3,3000,5,250),'score5goals':(today,10,4000,6,300),
        'play20':(week,35,10000,12,400),'win10':(week,20,15000,18,500),
        'play60month':(month,100,30000,30,700),'win20month':(month,40,40000,40,800),
        'play100':(season,200,75000,60,1200),'win50':(season,100,100000,80,1500),'reach_khl':(season,1,25000,25,600),'open50packs':(season,100,50000,50,1000)}
    if key not in target: raise ValueError('Задание не найдено.')
    period,need,reward_coins,reward_gems,quest_xp=target[key]
    u=await session.scalar(select(User).where(User.id==user_id).with_for_update())
    q=await session.scalar(select(QuestProgress).where(QuestProgress.user_id==user_id,QuestProgress.quest_key==key,QuestProgress.period_key==period).with_for_update())
    if not q or q.progress<need: raise ValueError('Задание ещё не выполнено.')
    if q.claimed: raise ValueError('Награда уже получена.')
    u.coins+=reward_coins; u.gems+=reward_gems; q.claimed=True
    bp=await session.scalar(select(BattlePassProgress).where(BattlePassProgress.user_id==user_id,BattlePassProgress.season==season))
    if not bp: bp=BattlePassProgress(user_id=user_id,season=season,xp=0,premium=False,claimed_free=[],claimed_premium=[],xp_curve=True); session.add(bp)
    ensure_bp_xp_curve(bp)
    bp.xp=min(BP_MAX_XP,bp.xp+quest_xp); await session.commit(); return (reward_coins,reward_gems,quest_xp)

async def claim_bp_reward(session,user_id,level,premium=False):
    if not 1<=level<=100: raise ValueError('Уровень должен быть от 1 до 100.')
    bp=await session.scalar(select(BattlePassProgress).where(BattlePassProgress.user_id==user_id,BattlePassProgress.season==season_key()).with_for_update())
    if not bp: raise ValueError('Сначала сыграйте матч, чтобы начать Battle Pass.')
    ensure_bp_xp_curve(bp)
    current=bp_level_from_xp(bp.xp)
    if current<level: raise ValueError('Этот уровень ещё не достигнут.')
    if premium and not bp.premium: raise ValueError('Premium-линия не активирована.')
    claimed=list(bp.claimed_premium if premium else bp.claimed_free)
    if level in claimed: raise ValueError('Награда уже получена.')
    u=await session.scalar(select(User).where(User.id==user_id).with_for_update())
    reward_text=''
    if level==100:
        target_ovr=99 if premium else 95
        card=await session.scalar(select(Card).where(Card.player_name=='Семён Кошелев',Card.ovr==target_ovr,Card.source!='demo'))
        if not card:
            raise ValueError(f'Карточка Семёна Кошелева {target_ovr} OVR ещё не загружена. Администратору нужно импортировать карточку до выдачи награды.')
        session.add(OwnedCard(user_id=user_id,card_id=card.id,slot=''))
        reward_text=f'Карточка Семён Кошелев · {target_ovr} OVR'
    elif premium and level%10==0:
        milestone_rarities={10:'Эпический',20:'Мифический',30:'Мифический',40:'Легендарный',50:'Легендарный',60:'Хроматический',70:'Хроматический',80:'Легенда',90:'Легенда',100:'Икона'}
        target_rarity=milestone_rarities.get(level,'Эпический')
        excluded=pack_card_exclusion()
        cards=(await session.scalars(select(Card).where(Card.active.is_(True),Card.rarity==target_rarity,Card.source!='demo',excluded))).all()
        if not cards:
            cards=(await session.scalars(select(Card).where(Card.active.is_(True),Card.rarity==target_rarity,excluded))).all()
        if not cards:
            cards=(await session.scalars(select(Card).where(Card.active.is_(True),Card.source!='demo',excluded))).all()
        if not cards:
            cards=(await session.scalars(select(Card).where(Card.active.is_(True),excluded))).all()
        if not cards: raise ValueError('Бонусная карточка пока недоступна: в базе нет активных карточек.')
        card=random.choice(cards); session.add(OwnedCard(user_id=user_id,card_id=card.id,slot=''))
        reward_text=f'🎴 Бонусная карточка: {card.player_name} · {card.ovr} OVR · {card.rarity}'
    elif (premium and level%5==0) or (not premium and level%10==0):
        pack_name='Премиум' if premium else 'Обычный'
        odds=adjust_pack_odds(pack_name, PACKS[pack_name]['odds']); rarity=rarity_pick(odds)
        cards=(await session.scalars(select(Card).where(Card.active.is_(True),Card.rarity==rarity,Card.source!='demo'))).all()
        if not cards:
            cards=(await session.scalars(select(Card).where(Card.active.is_(True),Card.rarity==rarity,Card.source=='demo'))).all()
        if not cards: raise ValueError(f'Награда-пак «{pack_name}» пока недоступна: нет карточек редкости {rarity}.')
        card=random.choice(cards); session.add(OwnedCard(user_id=user_id,card_id=card.id,slot=''))
        reward_text=f'🎴 Пак «{pack_name}»: {card.player_name} · {card.ovr} OVR · {card.rarity}'
    elif premium:
        gems=5+level//5; u.gems+=gems; reward_text=f'{gems} 💎'
    else:
        coins=500+level*100; u.coins+=coins; reward_text=f'{coins} 🪙'
    claimed.append(level)
    if premium: bp.claimed_premium=claimed
    else: bp.claimed_free=claimed
    await session.commit()
    return reward_text

def _card_import_identities(card):
    edition = (
        (card.season or '').strip(),
        (card.club or '').strip().casefold(),
        (card.league or '').strip().casefold(),
        (card.position or '').strip().upper(),
        (card.rarity or '').strip().casefold(),
        int(card.ovr),
        (card.image_url or '').strip(),
    )
    name_identity = ('name', (card.player_name or '').strip().casefold(), *edition)
    player_key = (card.player_key or '').strip().casefold()
    player_identity = ('key', player_key, *edition) if player_key else None
    return name_identity, player_identity


async def import_cards(session,path):
    cards=read_csv_cards(path)
    existing=(await session.scalars(select(Card))).all()
    known_names=set()
    known_player_keys=set()
    known_unkeyed_names=set()
    for card in existing:
        name_identity, player_identity = _card_import_identities(card)
        known_names.add(name_identity)
        if player_identity:
            known_player_keys.add(player_identity)
        else:
            known_unkeyed_names.add(name_identity)

    added=[]
    for card in cards:
        name_identity, player_identity = _card_import_identities(card)
        duplicate=(
            player_identity in known_player_keys or name_identity in known_unkeyed_names
            if player_identity else name_identity in known_names
        )
        if duplicate:
            continue
        added.append(card)
        known_names.add(name_identity)
        if player_identity:
            known_player_keys.add(player_identity)
        else:
            known_unkeyed_names.add(name_identity)

    if added:
        session.add_all(added)
        await session.commit()
    return len(added)

async def import_chemistry(session,path):
    import csv
    rows=[]
    with open(path,newline='',encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            a=r['player_key_a'].strip(); b=r['player_key_b'].strip(); games=int(r['shared_games'])
            if not a or not b or a==b or games<0: raise ValueError(f'Invalid chemistry row: {r}')
            a,b=sorted((a,b)); rows.append((a,b,games))
    from .models import ChemistryPair
    for a,b,games in rows:
        old=await session.scalar(select(ChemistryPair).where(ChemistryPair.player_key_a==a,ChemistryPair.player_key_b==b))
        if old: old.shared_games=games
        else: session.add(ChemistryPair(player_key_a=a,player_key_b=b,shared_games=games))
    await session.commit(); return len(rows)
