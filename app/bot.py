import asyncio, csv, os, random, shlex
from datetime import date
from pathlib import Path
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, desc, func, or_
from sqlalchemy.exc import IntegrityError
from .config import BOT_TOKEN, ADMIN_IDS, BATTLEPASS_PREMIUM_PRICE
from .db import Session, init_db
from .models import (User, Card, OwnedCard, Match, MarketListing, Auction, AuctionBid,
    TradeOffer, Clan, ClanMember, DailyTournament, TournamentEntry, BattlePassProgress,
    QuestProgress, Season, AuditLog, EconomyLedger, PromoCode, PromoRedemption, SupportMessage)
from .seed import seed_cards
from .game import PACKS, POSITIONS, RARITIES, RARITY_RANGES, PVE_REWARDS, team_power, league_for_rating
from .services import (get_or_create_user, lineup, claim_daily, open_pack, equip_card,
    start_pve_match, start_pvp_queue, create_market_listing, buy_market_listing,
    create_trade, resolve_trade, submit_auction_card, place_bid, make_tournament_entry,
    create_clan, join_clan, ensure_daily_tournament, ensure_season, progress_after_match,
    claim_quest, claim_bp_reward, import_cards, import_chemistry, audit, now, season_key, save_pack_config,
    create_clan_invite, resolve_clan_invite, leave_clan, set_clan_role, transfer_clan_owner)

def keyboard():
    kb=InlineKeyboardBuilder()
    items=[
        ('🏒 Моя команда','team'),('🎮 Играть','play'),
        ('✉️ Написать админу','support'),('🃏 Карточки','cards'),
        ('📦 Паки','packs'),('🏪 Рынок','market'),
        ('🔨 Аукционы','auctions'),('🏆 Рейтинг','leaderboard'),
        ('🎁 Ежедневная награда','daily'),('⚔️ Турнир','tournament'),
        ('🛡 Кланы','clans'),('🎟 Battle Pass','battlepass'),
        ('👤 Профиль','profile'),('🪙 Баланс','balance'),
        ('❔ Помощь','help')
    ]
    for label,key in items: kb.button(text=label,callback_data=f'player:{key}')
    kb.adjust(2)
    return kb.as_markup()
def admin(uid): return uid in ADMIN_IDS

async def send_section_photo(m, url, caption, reply_markup=None):
    try:
        await m.answer_photo(url, caption=caption, reply_markup=reply_markup)
    except Exception:
        await m.answer(caption, reply_markup=reply_markup)

PHOTO_ARENA='https://images.pexels.com/photos/20584514/pexels-photo-20584514.jpeg'
PHOTO_GOALIE='https://images.pexels.com/photos/6468596/pexels-photo-6468596.jpeg'
PHOTO_GAME='https://images.pexels.com/photos/8974638/pexels-photo-8974638.jpeg'
PHOTO_TRAINING='https://images.pexels.com/photos/6847600/pexels-photo-6847600.jpeg'

def pack_keyboard():
    kb=InlineKeyboardBuilder()
    for name,d in PACKS.items(): kb.button(text=f"{name} · {d['price']} {'🪙' if d['currency']=='coins' else '💎'}",callback_data=f'pack:{name}')
    kb.adjust(2); return kb.as_markup()

def pve_keyboard():
    kb=InlineKeyboardBuilder()
    kb.button(text='⚔️ PvP',callback_data='pvp_start')
    kb.button(text='🛡 Клановые битвы',callback_data='clan_start')
    kb.adjust(1); return kb.as_markup()

async def active_user(session,tg):
    u,_=await get_or_create_user(session,tg)
    if u.is_banned: raise ValueError('Ваш аккаунт заблокирован.')
    return u

async def owned_card_text(session,oc):
    c=await session.get(Card,oc.card_id)
    return f'#{oc.id} {c.player_name} · {c.position} · {c.ovr} OVR · {c.rarity}'

async def render_team(session,uid):
    rows=await lineup(session,uid); mapping={c.position:(o,c) for o,c in rows}
    lines=['🏒 МОЯ КОМАНДА']
    for pos in POSITIONS:
        item=mapping.get(pos)
        lines.append(f"{pos}: {item[1].player_name} · {item[1].ovr} OVR (карта #{item[0].id})" if item else f'{pos}: —')
    cards=[c for _,c in rows]
    lines.append(f'\n💪 Сила состава: {team_power(cards)} OVR')
    from .game import chemistry
    ch=await chemistry(session,cards); lines.append(f'🤝 Сыгранность: {ch}/40')
    lines.append('\nЗамена: /equip ID LW|C|RW|LD|RD|G')
    return '\n'.join(lines)

async def profile_text(u):
    return (f'👤 Профиль · {u.first_name or u.id}\n🪙 {u.coins} монет · 💎 {u.gems} алмазов\n'
            f'🏆 {league_for_rating(u.rating)} · ⭐ {u.rating} рейтинга\n'
            f'📊 Матчи: {u.games} · Победы/поражения: {u.wins}/{u.losses}\n'
            f'📈 Лучший рейтинг сезона: {u.lifetime_rating}')

async def get_match_for_user(session,uid):
    return await session.scalar(select(Match).where(or_(Match.home_user_id==uid,Match.away_user_id==uid),Match.status!='paired').order_by(Match.id.desc()))

async def main():
    if not BOT_TOKEN: raise RuntimeError('Укажите BOT_TOKEN в файле .env')
    await init_db()
    async with Session() as s:
        await seed_cards(s); await ensure_season(s); await ensure_daily_tournament(s)
    bot=Bot(BOT_TOKEN); dp=Dispatcher()

    @dp.message(Command('start'))
    async def start(m:Message):
        await m.answer('Меню обновлено: теперь управление через кнопки ниже.',reply_markup=ReplyKeyboardRemove())
        async with Session() as s:
            u,new=await get_or_create_user(s,m.from_user)
            await s.commit()
            if u.is_banned: await m.answer('Ваш аккаунт заблокирован.'); return
            welcome='Добро пожаловать в Kosh KHL Cards!\nСтартовый набор: 20 💎 · 5000 🪙 · команда из 6 карточек.' if new else 'С возвращением в Kosh KHL Cards!'
            await send_section_photo(m,PHOTO_ARENA,f'🏒 KOSH KHL CARDS\n\n{welcome}\n\nВыберите раздел в меню.',reply_markup=keyboard())


    support_steps={}
    @dp.message(F.text=='✉️ Написать админу')
    async def support_start(m:Message):
        kb=InlineKeyboardBuilder()
        for label,key in [('💡 Предложение','suggestion'),('⚠️ Жалоба','complaint'),('💬 Обращение','request')]:
            kb.button(text=label,callback_data=f'support_type:{key}')
        kb.adjust(1)
        await m.answer('Выберите тип сообщения:',reply_markup=kb.as_markup())

    @dp.callback_query(F.data.startswith('support_type:'))
    async def support_choose_type(c:CallbackQuery):
        category=c.data.split(':',1)[1]
        if category not in ('suggestion','complaint','request'):
            await c.answer('Неизвестный тип.',show_alert=True); return
        support_steps[c.from_user.id]={'category':category,'step':'body'}
        await c.message.answer('Напишите сообщение администратору одним сообщением. Для отмены отправьте /cancelmessage.')
        await c.answer()

    @dp.message(Command('cancelmessage'))
    async def support_cancel(m:Message):
        support_steps.pop(m.from_user.id,None)
        await m.answer('Отправка сообщения отменена.')

    @dp.message(lambda m: m.from_user.id in support_steps and support_steps[m.from_user.id].get('step')=='body')
    async def support_body(m:Message):
        body=(m.text or '').strip()
        if not body or len(body)>3000:
            await m.answer('Сообщение должно содержать текст и быть не длиннее 3000 символов.'); return
        support_steps[m.from_user.id]={'category':support_steps[m.from_user.id]['category'],'body':body,'step':'privacy'}
        kb=InlineKeyboardBuilder()
        kb.button(text='🙈 Анонимно',callback_data='support_privacy:1')
        kb.button(text='👤 С именем',callback_data='support_privacy:0')
        kb.button(text='❌ Отмена',callback_data='support_abort')
        kb.adjust(1)
        await m.answer('Как отправить сообщение администратору?',reply_markup=kb.as_markup())

    @dp.callback_query(F.data=='support_abort')
    async def support_abort(c:CallbackQuery):
        support_steps.pop(c.from_user.id,None)
        await c.message.answer('Отправка отменена.')
        await c.answer()

    @dp.callback_query(F.data.startswith('support_privacy:'))
    async def support_submit(c:CallbackQuery):
        state=support_steps.get(c.from_user.id)
        if not state or state.get('step')!='privacy':
            await c.answer('Начните отправку заново.',show_alert=True); return
        anonymous=c.data.endswith(':1')
        labels={'suggestion':'Предложение','complaint':'Жалоба','request':'Обращение'}
        async with Session() as s:
            item=SupportMessage(user_id=c.from_user.id,category=state['category'],body=state['body'],anonymous=anonymous)
            s.add(item); await s.commit(); await s.refresh(item)
        support_steps.pop(c.from_user.id,None)
        sender='Анонимный игрок' if anonymous else f'{c.from_user.full_name} · ID {c.from_user.id}'
        notice=f'📩 Новое сообщение #{item.id}\nТип: {labels[state["category"]]}\nОт: {sender}\n\n{state["body"]}'
        for admin_id in ADMIN_IDS:
            kb=InlineKeyboardBuilder()
            kb.button(text='📨 Открыть и ответить',callback_data=f'adm:message:{item.id}')
            try: await c.bot.send_message(admin_id,notice,reply_markup=kb.as_markup())
            except Exception: pass
        await c.message.answer('✅ Сообщение отправлено администрации. Спасибо!')
        await c.answer('Отправлено!')

    @dp.message(Command('help'))
    @dp.message(F.text=='❔ Помощь')
    async def help_cmd(m):
        await m.answer('Команды игрока:\n/equip ID POS — поставить карточку в состав\n/pve — матчи против AI\n/pvp — обычный PvP поиск\n/ranked — рейтинговый поиск\n/challenge ID [ставка] — вызов игрока\n/cancelchallenge — отменить исходящий вызов\n/sell ID PRICE — выставить на рынок\n/buy ID — купить лот\n/trade USER_ID MY_CARD_ID THEIR_CARD_ID — предложить обмен\n/auction_submit ID — передать карточку администрации\n/bid AUCTION_ID AMOUNT — ставка\n/tournament — ежедневный турнир\n/clan create НАЗВАНИЕ | /clan join ID | /clan invite USER_ID | /clan role USER_ID admin|member | /clan transfer USER_ID | /clan leave | /clanbattle\n/bp [premium|claim LEVEL] — Battle Pass\n/match — статус и текущая трансляция\n/leaderboard — рейтинг игроков\n/promo КОД — активировать промокод.')

    @dp.message(F.text=='🪙 Баланс')
    async def balance(m):
        async with Session() as s:
            try: u=await active_user(s,m.from_user)
            except ValueError as e: await m.answer(str(e)); return
            await m.answer(f'🪙 Монеты: {u.coins}\n💎 Алмазы: {u.gems}')

    @dp.message(F.text=='👤 Профиль')
    async def profile(m):
        async with Session() as s:
            try: u=await active_user(s,m.from_user)
            except ValueError as e: await m.answer(str(e)); return
            await m.answer(await profile_text(u))

    @dp.message(F.text=='🏒 Моя команда')
    async def team(m):
        async with Session() as s:
            try: await active_user(s,m.from_user)
            except ValueError as e: await m.answer(str(e)); return
            await send_section_photo(m,PHOTO_GOALIE,await render_team(s,m.from_user.id))

    @dp.message(Command('equip'))
    async def equip(m):
        try: _,oid,slot=m.text.split(); oid=int(oid); slot=slot.upper()
        except: await m.answer('Формат: /equip ID LW|C|RW|LD|RD|G'); return
        async with Session() as s:
            try:
                await active_user(s,m.from_user); c=await equip_card(s,m.from_user.id,oid,slot)
                await m.answer(f'✅ {c.player_name} установлен на позицию {slot}.\n\n'+await render_team(s,m.from_user.id))
            except ValueError as e: await m.answer(str(e))

    @dp.message(F.text=='🃏 Карточки')
    async def collection(m):
        async with Session() as s:
            try: await active_user(s,m.from_user)
            except ValueError as e: await m.answer(str(e)); return
            cards=(await s.scalars(select(OwnedCard).where(OwnedCard.user_id==m.from_user.id).order_by(OwnedCard.id.desc()).limit(60))).all()
            lines=['🃏 КОЛЛЕКЦИЯ (последние 60)']
            for oc in cards: lines.append(await owned_card_text(s,oc)+(f' · Слот {oc.slot}' if oc.slot else '')+(' · 🔒' if oc.locked else ''))
            lines.append('\nКарточки #ID можно выставить, обменять или поставить в состав.')
            await send_section_photo(m,PHOTO_GAME,'🃏 Ваша коллекция карточек.')
            await m.answer('\n'.join(lines))

    @dp.message(Command('card'))
    async def card_details(m):
        try: owned_id=int(m.text.split()[1])
        except: await m.answer('Формат: /card OWNED_CARD_ID'); return
        async with Session() as s:
            oc=await s.get(OwnedCard,owned_id)
            if not oc or oc.user_id!=m.from_user.id: await m.answer('Карточка не найдена.'); return
            c=await s.get(Card,oc.card_id)
            caption=f'🏒 {c.player_name}\n{c.ovr} OVR · {c.position} · {c.rarity}\n{c.country} · {c.club} · {c.league}\nСезон: {c.season or "—"} · ID: #{oc.id}'
            if c.image_url.startswith(('https://','http://')):
                try: await m.answer_photo(c.image_url,caption=caption)
                except Exception: await m.answer(caption+'\n(Фото недоступно по указанной ссылке.)')
            else: await m.answer(caption)

    @dp.message(F.text=='📦 Паки')
    async def packs(m):
        from .models import PackConfig
        lines=['📦 ПАКИ · шансы выпадения']
        kb=InlineKeyboardBuilder()
        async with Session() as s:
            for name,base in PACKS.items():
                cfg=await s.get(PackConfig,name)
                d={'price':cfg.price,'currency':cfg.currency,'odds':cfg.odds} if cfg else base
                lines.append(f'\n{name} — {d["price"]} {"🪙" if d["currency"]=="coins" else "💎"}\n' + ' · '.join(f'{r}: {p}%' for r,p in d['odds']))
                kb.button(text=f'{name} · {d["price"]} {"🪙" if d["currency"]=="coins" else "💎"}',callback_data=f'pack:{name}')
        kb.adjust(2)
        await send_section_photo(m,PHOTO_TRAINING,'📦 Выберите пак и посмотрите шансы выпадения.')
        await m.answer('\n'.join(lines),reply_markup=kb.as_markup())

    @dp.callback_query(F.data.startswith('pack:'))
    async def buy_pack(c:CallbackQuery):
        name=c.data.split(':',1)[1]
        async with Session() as s:
            try:
                u=await active_user(s,c.from_user); oc,card=await open_pack(s,u,name)
                await c.message.answer(f'🎉 ПАК ОТКРЫТ!\n{card.player_name}\n{card.position} · {card.ovr} OVR · {card.rarity}\nID карточки: #{oc.id}')
            except ValueError as e: await c.answer(str(e),show_alert=True); return
        await c.answer('Готово!')

    @dp.message(Command('pve'))
    @dp.message(F.text=='🎮 Играть')
    async def play(m): await send_section_photo(m,PHOTO_GAME,'🎮 Выберите режим матча:',reply_markup=pve_keyboard())

    @dp.callback_query(F.data=='pvp_start')
    async def queue_from_menu(c:CallbackQuery):
        ranked=False
        async with Session() as s:
            try:
                u=await active_user(s,c.from_user)
                match=await start_pvp_queue(s,u,ranked)
                if match.status=='queued' and match.away_user_id==u.id:
                    await c.bot.send_message(match.home_user_id,
                        f'✅ Соперник найден! Матч #{match.id}: {match.home_name} — {u.first_name or u.id}. Матч скоро начнётся.')
                    await c.message.answer(f'✅ Соперник найден! Матч #{match.id} начинается.')
                else:
                    kb=InlineKeyboardBuilder(); kb.button(text='❌ Отменить поиск',callback_data=f'match_cancel:{match.id}')
                    status_msg=await c.message.answer(f'🔎 Ищем соперника: 30 сек.\nID матча #{match.id}',reply_markup=kb.as_markup())
                    async def countdown():
                        for left in (25,20,15,10,5,0):
                            await asyncio.sleep(5)
                            async with Session() as check:
                                current=await check.get(Match,match.id)
                                if not current or current.status!='searching': return
                            try: await status_msg.edit_text(f'🔎 Ищем соперника: {left} сек.\nID матча #{match.id}',reply_markup=kb.as_markup())
                            except Exception: pass
                    asyncio.create_task(countdown())
            except ValueError as e: await c.answer(str(e),show_alert=True); return
        await c.answer()

    @dp.callback_query(F.data=='clan_start')
    async def clan_queue_from_menu(c:CallbackQuery):
        async with Session() as s:
            try:
                u=await active_user(s,c.from_user)
                member=await s.scalar(select(ClanMember).where(ClanMember.user_id==u.id))
                if not member: await c.answer('Сначала вступите в клан через /clan.',show_alert=True); return
                match=await start_pvp_queue(s,u,False,mode='clan')
                if match.status=='queued' and match.away_user_id==u.id:
                    await c.bot.send_message(match.home_user_id,
                        f'✅ Найден соперник из другого клана! Матч #{match.id} начинается.')
                    await c.message.answer(f'✅ Соперник найден! Клановый матч #{match.id} начинается.')
                else:
                    kb=InlineKeyboardBuilder(); kb.button(text='❌ Отменить поиск',callback_data=f'match_cancel:{match.id}')
                    status_msg=await c.message.answer(f'🛡 Поиск соперника: 30 сек.\nМатч #{match.id}',reply_markup=kb.as_markup())
                    async def countdown():
                        for left in (25,20,15,10,5,0):
                            await asyncio.sleep(5)
                            async with Session() as check:
                                current=await check.get(Match,match.id)
                                if not current or current.status!='searching': return
                            try: await status_msg.edit_text(f'🛡 Поиск соперника: {left} сек.\nМатч #{match.id}',reply_markup=kb.as_markup())
                            except Exception: pass
                    asyncio.create_task(countdown())
            except ValueError as e: await c.answer(str(e),show_alert=True); return
        await c.answer()

    @dp.callback_query(F.data.startswith('pve:'))
    async def pve(c:CallbackQuery):
        league=c.data.split(':',1)[1]
        async with Session() as s:
            try:
                u=await active_user(s,c.from_user); match=await start_pve_match(s,u,league)
                await c.message.answer(f'🏒 Матч #{match.id} поставлен в очередь.\n60 минут · автоматическая трансляция.\nМатч продолжится, даже если вы закроете Telegram.\nСтатус: /match')
            except ValueError as e: await c.answer(str(e),show_alert=True); return
        await c.answer()

    @dp.message(Command('pvp'))
    async def pvp(m):
        async with Session() as s:
            try:
                u=await active_user(s,m.from_user); match=await start_pvp_queue(s,u,False)
                await m.answer(f'🔎 Поиск соперника запущен. ID поиска #{match.id}. Когда соперник найдётся, матч начнётся автоматически. /match')
            except ValueError as e: await m.answer(str(e))

    @dp.message(Command('ranked'))
    async def ranked(m):
        async with Session() as s:
            try:
                u=await active_user(s,m.from_user); match=await start_pvp_queue(s,u,True)
                await m.answer(f'🏆 Поиск рейтингового соперника запущен. Лига: {league_for_rating(u.rating)} · рейтинг {u.rating}. ID #{match.id}.')
            except ValueError as e: await m.answer(str(e))

    @dp.message(Command('challenge'))
    async def challenge(m):
        try:
            parts=m.text.split(); target=int(parts[1]); bet=int(parts[2]) if len(parts)>2 else 0
            if bet<0 or bet>1_000_000: raise ValueError()
        except: await m.answer('Формат: /challenge USER_ID [ставка_в_монетах]'); return
        async with Session() as s:
            try:
                u=await active_user(s,m.from_user); target_user=await s.get(User,target)
                if not target_user or target_user.is_banned: raise ValueError('Игрок не найден или заблокирован.')
                if target==u.id: raise ValueError('Нельзя вызвать самого себя.')
                u=await s.scalar(select(User).where(User.id==u.id).with_for_update())
                if u.coins<bet: raise ValueError('Недостаточно монет для ставки.')
                rows=await lineup(s,u.id); cards=[c for _,c in rows]
                if len(rows)!=6 or {c.position for _,c in rows}!=set(POSITIONS): raise ValueError('Сначала соберите полный состав из шести разных позиций.')
                active=await s.scalar(select(Match.id).where(or_(Match.home_user_id==u.id,Match.away_user_id==u.id),Match.status.in_(['queued','searching','running','challenge'])))
                target_active=await s.scalar(select(Match.id).where(or_(Match.home_user_id==target,Match.away_user_id==target),Match.status.in_(['queued','searching','running','challenge'])))
                if active: raise ValueError('У вас уже есть активный матч.')
                if target_active: raise ValueError('У выбранного игрока уже есть активный матч.')
                from .game import team_power
                u.coins-=bet
                match=Match(mode='challenge',status='challenge',home_user_id=u.id,away_user_id=target,
                    home_name=u.first_name or str(u.id),away_name=target_user.first_name or str(target),
                    home_power=team_power(cards),home_goalie=next(c.ovr for c in cards if c.position=='G'),bet=bet)
                s.add(match); await s.commit(); await s.refresh(match)
                kb=InlineKeyboardBuilder(); kb.button(text='✅ Принять вызов',callback_data=f'chacc:{match.id}'); kb.button(text='❌ Отклонить',callback_data=f'chdec:{match.id}'); kb.adjust(1)
                try:
                    await m.bot.send_message(target,f'🏒 Вас вызывают на матч!\nИгрок: {u.first_name or u.id}\nСтавка: {bet} 🪙 с каждого. Матч #{match.id}',reply_markup=kb.as_markup())
                except Exception:
                    u.coins+=bet; match.status='delivery_failed'; await s.commit()
                    await m.answer('Не удалось доставить вызов игроку. Ставка возвращена; проверьте ID и убедитесь, что игрок запускал бота.')
                    return
                await m.answer(f'Вызов отправлен игроку {target}. Ваша ставка зарезервирована.')
            except ValueError as e: await m.answer(str(e))

    @dp.callback_query(F.data.startswith('chacc:'))
    async def challenge_accept(c:CallbackQuery):
        mid=int(c.data.split(':')[1])
        async with Session() as s:
            m=await s.scalar(select(Match).where(Match.id==mid).with_for_update())
            if not m or m.status!='challenge' or m.away_user_id!=c.from_user.id: await c.answer('Вызов уже недоступен.',show_alert=True); return
            try: u=await active_user(s,c.from_user)
            except ValueError as e: await c.answer(str(e),show_alert=True); return
            active=await s.scalar(select(Match.id).where(or_(Match.home_user_id==u.id,Match.away_user_id==u.id),Match.id!=mid,Match.status.in_(['queued','searching','running','challenge'])))
            if active: await c.answer('У вас уже есть другой активный матч.',show_alert=True); return
            u=await s.scalar(select(User).where(User.id==u.id).with_for_update())
            if u.coins<m.bet: await c.answer('Недостаточно монет для ставки.',show_alert=True); return
            rows=await lineup(s,u.id); cards=[x for _,x in rows]
            if len(rows)!=6 or {x.position for _,x in rows}!=set(POSITIONS): await c.answer('Сначала соберите полный состав из шести разных позиций.',show_alert=True); return
            from .game import team_power
            u.coins-=m.bet; m.away_power=team_power(cards); m.away_goalie=next(x.ovr for x in cards if x.position=='G'); m.status='queued'
            await s.commit()
            await c.message.answer(f'✅ Вызов #{mid} принят. Матч начинается!')
            await c.bot.send_message(m.home_user_id,f'✅ Игрок принял ваш вызов. Матч #{mid} начинается!')
        await c.answer()

    @dp.callback_query(F.data.startswith('chdec:'))
    async def challenge_decline(c:CallbackQuery):
        mid=int(c.data.split(':')[1])
        async with Session() as s:
            m=await s.scalar(select(Match).where(Match.id==mid).with_for_update())
            if not m or m.status!='challenge' or m.away_user_id!=c.from_user.id: await c.answer('Вызов уже недоступен.',show_alert=True); return
            u=await s.scalar(select(User).where(User.id==m.home_user_id).with_for_update()); u.coins+=m.bet; m.status='declined'; await s.commit()
            await c.bot.send_message(m.home_user_id,'Игрок отклонил вызов. Зарезервированная ставка возвращена.')
        await c.answer('Вызов отклонён.')

    @dp.message(Command('cancelchallenge'))
    async def cancel_challenge(m):
        async with Session() as s:
            row=await s.scalar(select(Match).where(Match.home_user_id==m.from_user.id,Match.status=='challenge').order_by(Match.id.desc()).with_for_update())
            if not row: await m.answer('Активного исходящего вызова нет.'); return
            owner=await s.scalar(select(User).where(User.id==m.from_user.id).with_for_update())
            owner.coins+=row.bet; row.status='cancelled'; await s.commit()
            try: await m.bot.send_message(row.away_user_id,f'Вызов #{row.id} отменён отправителем.')
            except Exception: pass
            await m.answer(f'Вызов #{row.id} отменён. Ставка {row.bet} 🪙 возвращена.')


    @dp.callback_query(F.data.startswith('match_cancel:'))
    async def match_cancel_button(c:CallbackQuery):
        try: mid=int(c.data.split(':',1)[1])
        except ValueError: await c.answer('Некорректный номер матча.',show_alert=True); return
        async with Session() as s:
            mt=await s.scalar(select(Match).where(Match.id==mid,Match.home_user_id==c.from_user.id,Match.status=='searching').with_for_update())
            if not mt:
                await c.answer('Этот поиск уже завершён или отменён.',show_alert=True); return
            mt.status='cancelled'; await s.commit()
        await c.message.edit_text(f'❌ Поиск матча #{mid} отменён.')
        await c.answer('Поиск отменён.')

    @dp.message(Command('match'))
    async def match_status(m):
        async with Session() as s:
            try: await active_user(s,m.from_user)
            except ValueError as e: await m.answer(str(e)); return
            mt=await get_match_for_user(s,m.from_user.id)
            if not mt: await m.answer('Матчей пока нет.'); return
            if mt.status=='searching': await m.answer(f'🔎 Поиск соперника · матч #{mt.id}.'); return
            if mt.status=='challenge': await m.answer(f'⏳ Ожидается ответ на вызов #{mt.id}.'); return
            if mt.status in ('queued','running'):
                events=mt.events or []; recent=events[max(0,mt.event_index-5):mt.event_index]
                await m.answer(f'🏒 Матч #{mt.id} · статус: {mt.status}\nСчёт: {mt.home_score}:{mt.away_score}\nМинута: {mt.minute}\n'+'\n'.join(e['text'] for e in recent))
            elif mt.status=='completed':
                await m.answer(f'🏁 Матч #{mt.id}: {mt.home_name} {mt.home_score}:{mt.away_score} {mt.away_name}\nRating Δ: {mt.rating_change}\n'+ '\n'.join(e['text'] for e in (mt.events or [])[-5:]))
            else: await m.answer(f'Последний матч #{mt.id}: {mt.status}.')

    @dp.message(Command('cancelmatch'))
    async def cancel_match(m):
        async with Session() as s:
            mt=await s.scalar(select(Match).where(Match.home_user_id==m.from_user.id,Match.status=='searching').order_by(Match.id.desc()))
            if not mt: await m.answer('Активного поиска нет.'); return
            mt.status='cancelled'; await s.commit(); await m.answer('Поиск соперника отменён.')

    @dp.message(Command('sell'))
    async def sell(m):
        try: _,oid,price=m.text.split(); oid=int(oid); price=int(price)
        except: await m.answer('Формат: /sell OWNED_CARD_ID PRICE'); return
        async with Session() as s:
            try:
                await active_user(s,m.from_user); listing=await create_market_listing(s,m.from_user.id,oid,price)
                await m.answer(f'🏪 Лот #{listing.id} выставлен за {price} 🪙.');
            except ValueError as e: await m.answer(str(e))

    @dp.message(Command('delist'))
    async def delist(m):
        try: lid=int(m.text.split()[1])
        except: await m.answer('Формат: /delist LISTING_ID'); return
        async with Session() as s:
            listing=await s.scalar(select(MarketListing).where(MarketListing.id==lid).with_for_update())
            if not listing or listing.seller_id!=m.from_user.id or listing.status!='active': await m.answer('Лот недоступен.'); return
            oc=await s.get(OwnedCard,listing.owned_card_id)
            if oc: oc.locked=False
            listing.status='cancelled'; await s.commit(); await m.answer('Лот снят с рынка.')

    @dp.message(Command('trade_cancel'))
    async def trade_cancel(m):
        try: oid=int(m.text.split()[1])
        except: await m.answer('Формат: /trade_cancel OFFER_ID'); return
        async with Session() as s:
            offer=await s.scalar(select(TradeOffer).where(TradeOffer.id==oid).with_for_update())
            if not offer or offer.sender_id!=m.from_user.id or offer.status!='pending': await m.answer('Предложение недоступно.'); return
            for cid in offer.give_card_ids+offer.receive_card_ids:
                oc=await s.get(OwnedCard,cid)
                if oc: oc.locked=False
            offer.status='cancelled'; await s.commit(); await m.answer('Предложение отменено.')

    @dp.message(F.text=='🏪 Рынок')
    @dp.message(Command('market'))
    async def market(m):
        async with Session() as s:
            listings=(await s.scalars(select(MarketListing).where(MarketListing.status=='active').order_by(desc(MarketListing.created_at)).limit(15))).all()
            if not listings: await m.answer('Рынок пока пуст. Выставить карточку: /sell ID PRICE'); return
            lines=['🏪 РЫНОК · последние 15 лотов']
            for l in listings:
                oc=await s.get(OwnedCard,l.owned_card_id); c=await s.get(Card,oc.card_id) if oc else None
                if c:
                    avg=await s.scalar(select(func.avg(MarketListing.price)).where(MarketListing.status=='active',MarketListing.owned_card_id.in_(select(OwnedCard.id).where(OwnedCard.card_id==c.id))))
                    seller=await s.get(User,l.seller_id)
                    lines.append(f'\nЛот #{l.id} · {c.player_name} · {c.ovr} OVR · {c.rarity}\nПродавец: {seller.first_name or seller.id if seller else l.seller_id}\nЦена: {l.price} 🪙 · средняя по этому игроку: {int(avg or 0)} 🪙\nРазмещён UTC: {l.created_at:%Y-%m-%d %H:%M}\nКупить: /buy {l.id}')
            await send_section_photo(m,PHOTO_ARENA,'🏪 Рынок карточек — выбирайте игроков для покупки.')
            await m.answer('\n'.join(lines))

    @dp.message(Command('buy'))
    async def buy(m):
        try: lid=int(m.text.split()[1])
        except: await m.answer('Формат: /buy LISTING_ID'); return
        async with Session() as s:
            try:
                u=await active_user(s,m.from_user); oc=await buy_market_listing(s,u,lid)
                c=await s.get(Card,oc.card_id); await m.answer(f'✅ Куплено: {c.player_name} · {c.ovr} OVR. Карточка #{oc.id}.')
            except ValueError as e: await m.answer(str(e))

    @dp.message(Command('trade'))
    async def trade(m):
        try: _,target,give,receive=m.text.split(); target=int(target); give=int(give); receive=int(receive)
        except: await m.answer('Формат: /trade USER_ID MY_CARD_ID THEIR_CARD_ID'); return
        async with Session() as s:
            try:
                await active_user(s,m.from_user); offer=await create_trade(s,m.from_user.id,target,give,receive)
                a=await s.get(OwnedCard,give); b=await s.get(OwnedCard,receive)
                try:
                    await m.bot.send_message(target,f'🔁 Предложение обмена #{offer.id}\nВам предлагают: {await owned_card_text(s,a)}\nВзамен вы отдаёте: {await owned_card_text(s,b)}',
                        reply_markup=trade_keyboard(offer.id))
                except Exception:
                    await resolve_trade(s,offer.id,target,False)
                    await m.answer('Не удалось доставить предложение. Обмен отменён, карточки разблокированы.')
                    return
                await m.answer(f'Предложение #{offer.id} отправлено.')
            except ValueError as e: await m.answer(str(e))

    def trade_keyboard(offer_id):
        kb=InlineKeyboardBuilder(); kb.button(text='✅ Принять',callback_data=f'tradeyes:{offer_id}'); kb.button(text='❌ Отклонить',callback_data=f'tradeno:{offer_id}'); kb.adjust(2); return kb.as_markup()

    @dp.callback_query(F.data.startswith(('tradeyes:','tradeno:')))
    async def trade_response(c:CallbackQuery):
        accept=c.data.startswith('tradeyes:'); oid=int(c.data.split(':')[1])
        async with Session() as s:
            try:
                await active_user(s,c.from_user)
                offer=await resolve_trade(s,oid,c.from_user.id,accept)
                await c.message.answer('✅ Обмен проведён.' if accept else 'Обмен отклонён.')
                try: await c.bot.send_message(offer.sender_id,'Ваше предложение принято.' if accept else 'Ваше предложение отклонено.')
                except Exception: pass
            except ValueError as e: await c.answer(str(e),show_alert=True); return
        await c.answer()

    @dp.message(Command('auction_submit'))
    async def auction_submit(m):
        try: oid=int(m.text.split()[1])
        except: await m.answer('Формат: /auction_submit OWNED_CARD_ID'); return
        async with Session() as s:
            try:
                await active_user(s,m.from_user); a=await submit_auction_card(s,m.from_user.id,oid)
                await m.answer(f'🔨 Карточка передана администрации. Заявка #{a.id}. После проверки администратор сможет запустить аукцион.')
                for aid in ADMIN_IDS:
                    try: await m.bot.send_message(aid,f'Новая заявка на аукцион #{a.id} от игрока {m.from_user.id}. /auction_queue')
                    except Exception: pass
            except ValueError as e: await m.answer(str(e))

    @dp.message(F.text=='🔨 Аукционы')
    @dp.message(Command('auctions'))
    async def auctions(m):
        async with Session() as s:
            rows=(await s.scalars(select(Auction).where(Auction.status=='active').order_by(Auction.ends_at).limit(15))).all()
            if not rows: await m.answer('Активных аукционов нет. Передать карточку: /auction_submit ID'); return
            lines=['🔨 АКТИВНЫЕ АУКЦИОНЫ']
            for a in rows:
                oc=await s.get(OwnedCard,a.owned_card_id); card=await s.get(Card,oc.card_id) if oc else None
                if card: lines.append(f'\n#{a.id} · {card.player_name} · {card.ovr} OVR\nТекущая ставка: {a.highest_bid or a.start_price} 🪙 · шаг {a.bid_step}\nЗавершение UTC: {a.ends_at:%Y-%m-%d %H:%M}\n/bid {a.id} СУММА')
            await send_section_photo(m,PHOTO_GOALIE,'🔨 Аукционы карточек — следите за ставками.')
            await m.answer('\n'.join(lines))

    @dp.message(Command('bid'))
    async def bid(m):
        try: _,aid,amount=m.text.split(); aid=int(aid); amount=int(amount)
        except: await m.answer('Формат: /bid AUCTION_ID AMOUNT'); return
        async with Session() as s:
            try:
                u=await active_user(s,m.from_user)
                before=await s.get(Auction,aid)
                previous_bidder=before.winner_id if before else 0
                a=await place_bid(s,u,aid,amount)
                if previous_bidder and previous_bidder!=u.id:
                    try: await m.bot.send_message(previous_bidder,f'🔨 Вашу ставку на аукционе #{aid} перебили. Зарезервированные монеты возвращены.')
                    except Exception: pass
                await m.answer(f'✅ Ставка {amount} 🪙 принята. Деньги зарезервированы до завершения аукциона.')
            except ValueError as e: await m.answer(str(e))

    @dp.message(Command('tournament'))
    @dp.message(F.text=='⚔️ Турнир')
    async def tournament(m):
        async with Session() as s:
            try: await active_user(s,m.from_user)
            except ValueError as e: await m.answer(str(e)); return
            t,e,new=await make_tournament_entry(s,m.from_user.id)
            rows=(await s.scalars(select(TournamentEntry).where(TournamentEntry.tournament_id==t.id).order_by(desc(TournamentEntry.points),desc(TournamentEntry.wins)).limit(10))).all()
            lines=[f'⚔️ ЕЖЕДНЕВНЫЙ ТУРНИР · {t.tournament_date}',f'Вы участвуете. Ваши очки: {e.points}', 'Очки: обычная победа 3 · победа в OT/буллитах 2 · поражение в OT/буллитах 1.','\nТОП-10:']
            for i,row in enumerate(rows,1): lines.append(f'{i}. {row.user_id} — {row.points} очк. · {row.wins} побед')
            lines.append('\nНаграда топ-1: карточка 80–90 OVR; чем выше OVR, тем ниже шанс. Сыграйте PvP-матч после регистрации.')
            await send_section_photo(m,PHOTO_GAME,'⚔️ Ежедневный турнир — набирайте очки и забирайте награду.')
            await m.answer('\n'.join(lines))

    @dp.message(Command('clan'))
    @dp.message(F.text=='🛡 Кланы')
    async def clan(m):
        parts=m.text.split(maxsplit=2)
        async with Session() as s:
            try: await active_user(s,m.from_user)
            except ValueError as e: await m.answer(str(e)); return
            if len(parts)>=3 and parts[1].lower()=='create':
                try: cl=await create_clan(s,m.from_user.id,parts[2]); await m.answer(f'🛡 Клан «{cl.name}» создан. ID: {cl.id}')
                except ValueError as e: await m.answer(str(e))
                return
            if len(parts)>=3 and parts[1].lower()=='invite':
                try:
                    inv=await create_clan_invite(s,m.from_user.id,int(parts[2]))
                    kb=InlineKeyboardBuilder(); kb.button(text='✅ Принять',callback_data=f'clanyes:{inv.id}'); kb.button(text='❌ Отклонить',callback_data=f'clanno:{inv.id}'); kb.adjust(2)
                    try:
                        await m.bot.send_message(int(parts[2]),f'🛡 Вас приглашают в клан. Приглашение #{inv.id}',reply_markup=kb.as_markup())
                    except Exception:
                        inv.status='delivery_failed'; await s.commit()
                        await m.answer('Не удалось доставить приглашение. Проверьте, запускал ли игрок бота.')
                        return
                    await m.answer('Приглашение отправлено.')
                except ValueError as e: await m.answer(str(e))
                return
            if len(parts)>=2 and parts[1].lower()=='leave':
                try: await leave_clan(s,m.from_user.id); await m.answer('Вы вышли из клана.')
                except ValueError as e: await m.answer(str(e))
                return
            if len(parts)>=3 and parts[1].lower()=='transfer':
                try:
                    cl=await transfer_clan_owner(s,m.from_user.id,int(parts[2]))
                    await m.answer(f'Владелец клана «{cl.name}» изменён.')
                except ValueError as e: await m.answer(str(e))
                return
            if len(parts)>=4 and parts[1].lower()=='role':
                try: await set_clan_role(s,m.from_user.id,int(parts[2]),parts[3].lower()); await m.answer('Роль участника обновлена.')
                except ValueError as e: await m.answer(str(e))
                return
            if len(parts)>=3 and parts[1].lower()=='join':
                try: cl=await join_clan(s,m.from_user.id,int(parts[2])); await m.answer(f'Вы вступили в клан «{cl.name}».')
                except (ValueError,TypeError) as e: await m.answer(str(e))
                return
            mem=await s.scalar(select(ClanMember).where(ClanMember.user_id==m.from_user.id))
            if mem:
                cl=await s.get(Clan,mem.clan_id); count=await s.scalar(select(func.count(ClanMember.id)).where(ClanMember.clan_id==cl.id))
                await m.answer(f'🛡 {cl.name}\nРоль: {mem.role}\nУчастников: {count}\nОчки битв: {cl.points}\nРейтинг: {cl.rating}\n/clanbattle — клановая битва')
            else:
                top=(await s.scalars(select(Clan).order_by(desc(Clan.points)).limit(10))).all()
                await m.answer('Вы не состоите в клане.\nСоздать: /clan create НАЗВАНИЕ\nВступить: /clan join ID\n\nКланы:\n'+'\n'.join(f'#{x.id} {x.name} · {x.points} очк.' for x in top))

    @dp.callback_query(F.data.startswith(('clanyes:','clanno:')))
    async def clan_invite_response(c:CallbackQuery):
        accept=c.data.startswith('clanyes:'); iid=int(c.data.split(':')[1])
        async with Session() as s:
            try:
                await active_user(s,c.from_user)
                inv=await resolve_clan_invite(s,iid,c.from_user.id,accept)
                clan_obj=await s.get(Clan,inv.clan_id)
                await c.message.answer(f'Вы вступили в клан «{clan_obj.name}».' if accept else 'Приглашение отклонено.')
                try: await c.bot.send_message(inv.inviter_id,f'Игрок {c.from_user.id} '+('вступил в ваш клан.' if accept else 'отклонил приглашение.'))
                except Exception: pass
            except ValueError as e: await c.answer(str(e),show_alert=True); return
        await c.answer()

    @dp.message(Command('clanbattle'))
    async def clanbattle(m):
        async with Session() as s:
            try: u=await active_user(s,m.from_user)
            except ValueError as e: await m.answer(str(e)); return
            member=await s.scalar(select(ClanMember).where(ClanMember.user_id==u.id))
            if not member: await m.answer('Сначала вступите в клан.'); return
            try:
                match=await start_pvp_queue(s,u,False,mode='clan')
            except ValueError as e: await m.answer(str(e)); return
            await m.answer(f'⚔️ Вы отправлены в поиск клановой битвы. ID #{match.id}. Очки клана начисляются только за этот режим.')

    @dp.message(Command('bp'))
    @dp.message(F.text=='🎟 Battle Pass')
    async def battlepass(m):
        parts=m.text.split()
        async with Session() as s:
            try: u=await active_user(s,m.from_user)
            except ValueError as e: await m.answer(str(e)); return
            u=await s.scalar(select(User).where(User.id==u.id).with_for_update())
            bp=await s.scalar(select(BattlePassProgress).where(BattlePassProgress.user_id==u.id,BattlePassProgress.season==season_key()).with_for_update())
            if not bp:
                bp=BattlePassProgress(user_id=u.id,season=season_key()); s.add(bp); await s.commit()
            if len(parts)>1 and parts[1].lower()=='premium':
                if bp.premium: await m.answer('Premium уже активирован.'); return
                if u.gems<BATTLEPASS_PREMIUM_PRICE: await m.answer(f'Нужно {BATTLEPASS_PREMIUM_PRICE} 💎 для Premium-линии.'); return
                u.gems-=BATTLEPASS_PREMIUM_PRICE; bp.premium=True; await s.commit(); await m.answer(f'✅ Premium-линия активирована за {BATTLEPASS_PREMIUM_PRICE} 💎.'); return
            if len(parts)>2 and parts[1].lower() in ('claim','premium_claim'):
                try:
                    reward=await claim_bp_reward(s,u.id,int(parts[2]),parts[1].lower()=='premium_claim')
                    await m.answer(f'🎁 Получена награда: {reward}.')
                except ValueError as e: await m.answer(str(e))
                return
            level=min(100,bp.xp//100)
            await m.answer(f'🎟 BATTLE PASS · сезон {bp.season}\nУровень: {level}/100 · XP: {bp.xp}\nFree-награды: /bp claim LEVEL\nPremium-линия: /bp premium ({BATTLEPASS_PREMIUM_PRICE} 💎), награды /bp premium_claim LEVEL\nНа 100 уровне: Семён Кошелев · 99 OVR (карточка должна быть предварительно импортирована администратором).')

    @dp.message(Command('quest'))
    async def quest(m):
        parts=m.text.split()
        async with Session() as s:
            try: await active_user(s,m.from_user)
            except ValueError as e: await m.answer(str(e)); return
            if len(parts)==3 and parts[1]=='claim':
                try: reward=await claim_quest(s,m.from_user.id,parts[2]); await m.answer(f'✅ Получено {reward} 🪙.')
                except ValueError as e: await m.answer(str(e))
                return
            today=now().date().isoformat(); week=f'{now().date().isocalendar().year}-W{now().date().isocalendar().week}'; season=season_key()
            definitions=[
                ('play3',today,3,'Ежедневное: сыграть 3 матча'),('win1',today,1,'Ежедневное: выиграть 1 матч'),('score5goals',today,5,'Ежедневное: забить 5 голов'),
                ('play20',week,20,'Еженедельное: сыграть 20 матчей'),('win10',week,10,'Еженедельное: выиграть 10 матчей'),
                ('play100',season,100,'Сезонное: сыграть 100 матчей'),('win50',season,50,'Сезонное: одержать 50 побед'),('reach_khl',season,1,'Сезонное: достичь КХЛ'),('open50packs',season,50,'Сезонное: открыть 50 паков')]
            lines=['📋 ЗАДАНИЯ']
            for key,period,need,title in definitions:
                q=await s.scalar(select(QuestProgress).where(QuestProgress.user_id==m.from_user.id,QuestProgress.quest_key==key,QuestProgress.period_key==period))
                value=q.progress if q else 0; status='получено' if q and q.claimed else (f'/quest claim {key}' if value>=need else '')
                lines.append(f'{title}: {value}/{need} {status}')
            await m.answer('\n'.join(lines))

    @dp.message(F.text=='🎁 Ежедневная награда')
    async def daily(m):
        async with Session() as s:
            try: u=await active_user(s,m.from_user)
            except ValueError as e: await m.answer(str(e)); return
            ok=await claim_daily(s,u); await m.answer('🎁 Получено 1000 🪙!' if ok else 'Вы уже забрали награду сегодня.')

    @dp.message(F.text=='🏆 Рейтинг')
    @dp.message(Command('leaderboard'))
    async def leaderboard(m):
        async with Session() as s:
            rows=(await s.scalars(select(User).order_by(desc(User.rating),desc(User.wins)).limit(15))).all()
            await send_section_photo(m,PHOTO_ARENA,'🏆 Рейтинг сезона — лучшие игроки лиги.')
            await m.answer('🏆 РЕЙТИНГ СЕЗОНА\n'+'\n'.join(f'{i}. {u.first_name or u.id} — {u.rating} · {league_for_rating(u.rating)}' for i,u in enumerate(rows,1)))

    @dp.message(Command('season'))
    async def season(m):
        async with Session() as s:
            ss=await ensure_season(s); await m.answer(f'🗓 Сезон {ss.season_key}\nНачало UTC: {ss.starts_at}\nКонец UTC: {ss.ends_at}\nПосле сезона рейтинг сбрасывается до 0, статистика сохраняется.')


    @dp.message(Command('myid'))
    async def myid(m):
        await m.answer(f'Ваш Telegram ID: {m.from_user.id}')

    admin_steps={}
    def admin_keyboard():
        kb=InlineKeyboardBuilder()
        for label,data in [('👥 Игроки','adm:players:0'),('📊 Статистика','adm:stats'),('💰 Выдать монеты','adm:wiz:givecoins'),('💎 Выдать алмазы','adm:wiz:givegems'),('🃏 Выдать карточку','adm:wiz:givecard'),('🚫 Блокировка','adm:wiz:ban'),('🔨 Аукционы','adm:auctions'),('🛠 Карточки и паки','adm:cards'),('🎟 Промокоды','adm:promos'),('📩 Сообщения игроков','adm:messages'),('📖 Все команды','adm:help')]:
            kb.button(text=label,callback_data=data)
        kb.adjust(2)
        return kb.as_markup()

    def admin_back_keyboard():
        kb=InlineKeyboardBuilder()
        kb.button(text='⬅️ Назад в админ-меню',callback_data='adm:home')
        return kb.as_markup()

    @dp.message(Command('admin'))
    async def admin_help(m):
        if not admin(m.from_user.id):
            await m.answer(f'⛔ Нет доступа. Ваш Telegram ID: {m.from_user.id}. Добавьте его в ADMIN_IDS в Railway Variables.')
            return
        await m.answer('🛡 ПАНЕЛЬ АДМИНИСТРАТОРА\nВыберите действие. Кнопки запускают пошаговый ввод.',reply_markup=admin_keyboard())

    @dp.callback_query(F.data.startswith('adm:'))
    async def admin_menu_callback(c:CallbackQuery):
        if not admin(c.from_user.id):
            await c.answer('⛔ Нет доступа',show_alert=True); return
        action=c.data.split(':')
        if action[1]=='stats':
            async with Session() as s:
                total=await s.scalar(select(func.count(User.id))) or 0
                banned=await s.scalar(select(func.count(User.id)).where(User.is_banned.is_(True))) or 0
                cards=await s.scalar(select(func.count(Card.id))) or 0
                matches=await s.scalar(select(func.count(Match.id))) or 0
                active=await s.scalar(select(func.count(Match.id)).where(Match.status.in_(['queued','searching','running','challenge']))) or 0
                listings=await s.scalar(select(func.count(MarketListing.id)).where(MarketListing.status=='active')) or 0
                coins=await s.scalar(select(func.coalesce(func.sum(User.coins),0))) or 0
                gems=await s.scalar(select(func.coalesce(func.sum(User.gems),0))) or 0
            await c.message.answer(f'📊 СТАТИСТИКА БОТА\n👥 Игроков: {total}\n🚫 Заблокировано: {banned}\n🃏 Шаблонов карточек: {cards}\n🏒 Всего матчей: {matches}\n🔥 Активных матчей: {active}\n🏪 Активных лотов: {listings}\n🪙 Монет в экономике: {coins}\n💎 Алмазов в экономике: {gems}',reply_markup=admin_back_keyboard())
        elif action[1]=='players':
            page=int(action[2]) if len(action)>2 else 0
            async with Session() as s:
                rows=(await s.scalars(select(User).order_by(desc(User.rating),desc(User.games)).offset(page*15).limit(15))).all()
                total=await s.scalar(select(func.count(User.id))) or 0
            lines=[f'👥 СПИСОК ИГРОКОВ · страница {page+1} · всего {total}', 'Средний уровень команды — средний рейтинг экипированных карточек.']
            for u in rows:
                async with Session() as team_session:
                    avg_team=await team_session.scalar(select(func.avg(Card.ovr)).join(OwnedCard,OwnedCard.card_id==Card.id).where(OwnedCard.user_id==u.id,OwnedCard.slot!=''))
                avg_text=f'{float(avg_team):.1f} OVR' if avg_team is not None else 'состав не собран'
                display_name=u.first_name or (f'@{u.username}' if u.username else 'Без имени')
                lines.append(f'\n👤 {display_name}\n🆔 ID: {u.id}\n⭐ Рейтинг: {u.rating} · {league_for_rating(u.rating)}\n🏒 Средний уровень команды: {avg_text}'+(' · 🚫 Заблокирован' if u.is_banned else ''))
            kb=InlineKeyboardBuilder()
            if page>0: kb.button(text='⬅️ Назад',callback_data=f'adm:players:{page-1}')
            if (page+1)*15<total: kb.button(text='Вперёд ➡️',callback_data=f'adm:players:{page+1}')
            kb.button(text='⬅️ В админ-меню',callback_data='adm:home'); kb.adjust(2)
            await c.message.answer('\n'.join(lines),reply_markup=kb.as_markup())
        elif action[1]=='wiz':
            kind=action[2]
            admin_steps[c.from_user.id]={'kind':kind,'step':'user'}
            prompt={'givecoins':'💰 Введите Telegram ID игрока — это числовой номер аккаунта в Telegram. Баланс монет будет изменён на указанную сумму.','givegems':'💎 Введите Telegram ID игрока — это числовой номер аккаунта в Telegram. Баланс алмазов будет изменён на указанную сумму.','givecard':'🃏 Введите Telegram ID игрока, которому нужно выдать карточку.','ban':'🚫 Введите Telegram ID игрока, которого нужно заблокировать.'}.get(kind,'Неизвестное действие.')
            await c.message.answer(prompt+'\n'+('Шаг 1 из 1.' if kind=='ban' else 'Шаг 1 из 2.')+' Отмена: /canceladmin')
        elif action[1]=='auctions':
            kb=InlineKeyboardBuilder()
            kb.button(text='📋 Заявки на аукцион',callback_data='adm:auction_queue')
            kb.button(text='❌ Отменить аукцион',callback_data='adm:auction_cancel')
            kb.button(text='⬅️ Назад',callback_data='adm:home'); kb.adjust(1)
            await c.message.answer('🔨 УПРАВЛЕНИЕ АУКЦИОНАМИ\nЗдесь можно просматривать заявки, запускать и отменять аукционы. Выберите действие:',reply_markup=kb.as_markup())
        elif action[1]=='auction_queue':
            async with Session() as s:
                rows=(await s.scalars(select(Auction).where(Auction.status=='submitted').order_by(Auction.created_at))).all()
                lines=['📋 ЗАЯВКИ НА АУКЦИОН']
                for a in rows:
                    oc=await s.get(OwnedCard,a.owned_card_id); card=await s.get(Card,oc.card_id) if oc else None
                    if card: lines.append(f'\nЗаявка #{a.id} · игрок {a.owner_id} · {card.player_name} · рейтинг {card.ovr}\nДля запуска: /auction_create {a.id} НАЧАЛЬНАЯ_ЦЕНА ШАГ СРОК_ЧАСЫ МИН_ЦЕНА МАКС_СТАВКА')
            await c.message.answer('\n'.join(lines) if len(lines)>1 else 'Заявок пока нет.',reply_markup=admin_back_keyboard())
        elif action[1]=='auction_cancel':
            await c.message.answer('Чтобы отменить аукцион, укажите его номер командой /auction_cancel НОМЕР. Кнопочное управление добавим следующим этапом.',reply_markup=admin_back_keyboard())
        elif action[1]=='cards':
            await c.message.answer('🃏 КАРТОЧКИ И ПАКИ\n/createcard — создать шаблон карточки. После команды укажите через |: имя игрока | позиция | общий рейтинг | редкость | страна | клуб | лига | ссылка на фото | ключ игрока.\n/editcard ID ПОЛЕ ЗНАЧЕНИЕ — изменить карточку. ID — номер шаблона; поле: ovr (общий рейтинг), rarity (редкость), country (страна) или image_url (ссылка на фото).\n/transfercard ID НОВЫЙ_КЛУБ НОВАЯ_ЛИГА СЕЗОН — создать новую версию карточки после трансфера.\n/cardactive ID on|off — включить или отключить карточку в новых выпадениях (уже выданные карты сохраняются).\n/setpack — изменить стоимость пака и вероятности выпадения редкостей.\n/importcards и /importchem — загрузить CSV-файл с карточками или сыгранностью.',reply_markup=admin_back_keyboard())
        elif action[1]=='promos':
            kb=InlineKeyboardBuilder()
            kb.button(text='➕ Создать промокод',callback_data='adm:promo_create')
            kb.button(text='📈 Статистика промокодов',callback_data='adm:promo_stats')
            kb.button(text='⬅️ Назад',callback_data='adm:home'); kb.adjust(1)
            await c.message.answer('🎟 УПРАВЛЕНИЕ ПРОМОКОДАМИ\nСоздавайте коды с наградой в монетах и/или алмазах. Лимит 0 означает без ограничений.',reply_markup=kb.as_markup())
        elif action[1]=='promo_create':
            admin_steps[c.from_user.id]={'kind':'promo','step':'code','data':{}}
            await c.message.answer('🎟 Шаг 1 из 4. Введите промокод латинскими буквами или цифрами (до 40 символов).')
        elif action[1]=='promo_stats':
            async with Session() as s:
                codes=(await s.scalars(select(PromoCode).order_by(PromoCode.created_at.desc()).limit(30))).all()
                total=await s.scalar(select(func.count(PromoRedemption.id))) or 0
            lines=[f'📈 СТАТИСТИКА ПРОМОКОДОВ\nВсего активаций: {total}']
            for p in codes:
                lines.append(f'\n🎟 {p.code} — {"🟢 активен" if p.active else "🔴 выключен"}\nАктиваций: {p.uses_count}'+(f'/{p.max_uses}' if p.max_uses else ' (без лимита)')+f'\nНаграда: {p.reward_coins} 🪙 · {p.reward_gems} 💎')
            if not codes: lines.append('Промокодов пока нет.')
            await c.message.answer(''.join(lines),reply_markup=admin_back_keyboard())

        elif action[1]=='messages':
            async with Session() as s:
                rows=(await s.scalars(select(SupportMessage).order_by(SupportMessage.created_at.desc()).limit(25))).all()
                unread=await s.scalar(select(func.count(SupportMessage.id)).where(SupportMessage.status=='new')) or 0
            lines=[f'📩 СООБЩЕНИЯ ИГРОКОВ · новых: {unread}']
            kb=InlineKeyboardBuilder()
            for item in rows:
                title={'suggestion':'Предложение','complaint':'Жалоба','request':'Обращение'}.get(item.category,item.category)
                who='анонимно' if item.anonymous else f'ID {item.user_id}'
                lines.append(f'\n#{item.id} · {title} · {who} · {item.status}')
                kb.button(text=f'Открыть #{item.id}',callback_data=f'adm:message:{item.id}')
            kb.button(text='⬅️ Назад',callback_data='adm:home'); kb.adjust(2)
            await c.message.answer('\n'.join(lines) if rows else 'Сообщений пока нет.',reply_markup=kb.as_markup())
        elif action[1]=='message':
            mid=int(action[2])
            async with Session() as s:
                item=await s.get(SupportMessage,mid)
                if not item:
                    await c.answer('Сообщение не найдено.',show_alert=True); return
                title={'suggestion':'Предложение','complaint':'Жалоба','request':'Обращение'}.get(item.category,item.category)
                who='анонимно' if item.anonymous else f'{item.user_id}'
                kb=InlineKeyboardBuilder()
                kb.button(text='✍️ Ответить игроку',callback_data=f'adm:reply:{mid}')
                kb.button(text='⬅️ К сообщениям',callback_data='adm:messages'); kb.adjust(1)
                item.status='read'; await s.commit()
            await c.message.answer(f'📩 Сообщение #{mid}\nТип: {title}\nОт: {who}\n\n{item.body}',reply_markup=kb.as_markup())
        elif action[1]=='reply':
            mid=int(action[2])
            async with Session() as s:
                item=await s.get(SupportMessage,mid)
                if not item:
                    await c.answer('Сообщение не найдено.',show_alert=True); return
            admin_steps[c.from_user.id]={'kind':'support_reply','step':'reply','message_id':mid}
            await c.message.answer(f'Введите ответ для сообщения #{mid}. Игрок получит его в боте.')

        elif action[1]=='help':
            await c.message.answer('📖 СПРАВОЧНИК АДМИНИСТРАТОРА\n/givecoins ID_ИГРОКА СУММА — изменить баланс монет. Положительное число начисляет, отрицательное списывает.\n/givegems ID_ИГРОКА СУММА — изменить баланс алмазов по тому же принципу.\n/givecard ID_ИГРОКА ID_КАРТОЧКИ — выдать игроку карточку по номеру шаблона.\n/ban ID_ИГРОКА — заблокировать игрока.\n/unban ID_ИГРОКА — снять блокировку.\n/createcard — создать шаблон карточки; порядок полей показан в разделе «Карточки и паки».\n/editcard — изменить рейтинг, редкость, страну или фото карточки.\n/transfercard — создать новую версию карточки после перехода игрока в другой клуб.\n/cardactive — разрешить или запретить выпадение карточки из паков.\n/auction_queue — посмотреть заявки на аукцион. /auction_create — запустить аукцион, указав цены, шаг и срок. /auction_cancel — отменить аукцион.\n/setpack — настроить стоимость пака и шансы редкостей.\n/importcards /importchem — загрузить данные из CSV-файлов.\n/bpgrant ID_ИГРОКА КОЛИЧЕСТВО_XP — начислить опыт Battle Pass.\n/season_reset — обнулить рейтинг текущего сезона (общая статистика матчей сохранится).\n/stats — краткая статистика бота.',reply_markup=admin_back_keyboard())
        elif action[1]=='home':
            await c.message.answer('🛡 ПАНЕЛЬ АДМИНИСТРАТОРА\\nВыберите нужный раздел:',reply_markup=admin_keyboard())
        await c.answer()

    @dp.message(Command('canceladmin'))
    async def cancel_admin_wizard(m):
        if not admin(m.from_user.id): return
        admin_steps.pop(m.from_user.id,None)
        await m.answer('❎ Пошаговый ввод отменён.',reply_markup=admin_keyboard())

    @dp.message(lambda m: m.from_user.id in admin_steps)
    async def admin_wizard_input(m:Message):
        state=admin_steps.get(m.from_user.id)
        if not state or not admin(m.from_user.id) or not m.text: return
        kind=state['kind']; step=state['step']
        try:
            if kind=='support_reply' and step=='reply':
                reply_text=m.text.strip()
                if not reply_text or len(reply_text)>3000:
                    await m.answer('Ответ должен содержать текст до 3000 символов.'); return
                async with Session() as s:
                    item=await s.get(SupportMessage,state['message_id'])
                    if not item:
                        await m.answer('Сообщение не найдено.'); admin_steps.pop(m.from_user.id,None); return
                    target=item.user_id; item.admin_reply=reply_text; item.status='answered'; await s.commit()
                try:
                    await m.bot.send_message(target,f'✉️ Ответ администрации на ваше сообщение #{item.id}:\n\n{reply_text}')
                    await m.answer('✅ Ответ отправлен игроку.',reply_markup=admin_keyboard())
                except Exception:
                    await m.answer('Ответ сохранён, но доставить его игроку не удалось.')
                admin_steps.pop(m.from_user.id,None)
                return
            if kind=='promo':
                data=state.setdefault('data',{})
                if step=='code':
                    code=m.text.strip().upper()
                    if not code or len(code)>40 or not code.replace('_','').isalnum():
                        await m.answer('Код должен содержать только латинские буквы, цифры и знак _. Введите ещё раз.'); return
                    async with Session() as s:
                        if await s.get(PromoCode,code): await m.answer('Такой промокод уже существует. Придумайте другой.'); return
                    data['code']=code; state['step']='coins'
                    await m.answer('Шаг 2 из 4. Сколько монет выдавать? Введите 0, если монеты не нужны.')
                elif step=='coins':
                    value=int(m.text.strip())
                    if value<0: raise ValueError()
                    data['coins']=value; state['step']='gems'
                    await m.answer('Шаг 3 из 4. Сколько алмазов выдавать? Введите 0, если алмазы не нужны.')
                elif step=='gems':
                    value=int(m.text.strip())
                    if value<0 or (value==0 and data.get('coins',0)==0): raise ValueError()
                    data['gems']=value; state['step']='limit'
                    await m.answer('Шаг 4 из 4. Укажите максимальное число активаций. 0 — без ограничений.')
                elif step=='limit':
                    limit=int(m.text.strip())
                    if limit<0: raise ValueError()
                    async with Session() as s:
                        s.add(PromoCode(code=data['code'],reward_coins=data['coins'],reward_gems=data['gems'],max_uses=limit,uses_count=0,active=True))
                        await audit(s,m.from_user.id,'promo_create',0,f'{data["code"]}:{data["coins"]}:{data["gems"]}:{limit}')
                        await s.commit()
                    admin_steps.pop(m.from_user.id,None)
                    await m.answer(f'✅ Промокод {data["code"]} создан!\nНаграда: {data["coins"]} 🪙 · {data["gems"]} 💎\nЛимит активаций: {limit if limit else "без ограничений"}',reply_markup=admin_keyboard())
                return
            if step=='user':
                uid=int(m.text.strip())
                async with Session() as s:
                    target=await s.get(User,uid)
                    if not target: await m.answer('Игрок не найден. Повторите ID или /canceladmin.'); return
                state['uid']=uid
                if kind in ('givecoins','givegems'):
                    state['step']='amount'
                    await m.answer('Шаг 2 из 2. Введите количество монет или алмазов. Например: 500 начислит 500, а -200 спишет 200.')
                elif kind=='givecard':
                    state['step']='card'
                    await m.answer('Шаг 2 из 2. Введите номер шаблона карточки, которую нужно выдать игроку.')
                elif kind=='ban':
                    async with Session() as s:
                        target=await s.get(User,uid); target.is_banned=True
                        await audit(s,m.from_user.id,'ban',uid); await s.commit()
                    admin_steps.pop(m.from_user.id,None)
                    await m.answer(f'🚫 Игрок {uid} заблокирован.',reply_markup=admin_keyboard())
            elif step=='amount':
                amount=int(m.text.strip())
                if amount==0: raise ValueError()
                async with Session() as s:
                    target=await s.get(User,state['uid']); field='coins' if kind=='givecoins' else 'gems'
                    if getattr(target,field)+amount<0: await m.answer('Недостаточно средств для списания. Введите другую сумму.'); return
                    setattr(target,field,getattr(target,field)+amount)
                    await audit(s,m.from_user.id,kind,state['uid'],str(amount)); await s.commit()
                admin_steps.pop(m.from_user.id,None)
                await m.answer(f'✅ Баланс обновлён: {amount:+} {"🪙" if kind=="givecoins" else "💎"}.',reply_markup=admin_keyboard())
            elif step=='card':
                cid=int(m.text.strip())
                async with Session() as s:
                    if not await s.get(Card,cid): await m.answer('Шаблон карточки не найден. Введите другой ID.'); return
                    s.add(OwnedCard(user_id=state['uid'],card_id=cid,slot=''))
                    await audit(s,m.from_user.id,'givecard',state['uid'],str(cid)); await s.commit()
                admin_steps.pop(m.from_user.id,None)
                await m.answer(f'✅ Карточка #{cid} выдана игроку {state["uid"]}.',reply_markup=admin_keyboard())
        except ValueError:
            await m.answer('Введите целое число (без букв) или отправьте /canceladmin для отмены.')

    @dp.message(Command('promo'))
    async def redeem_promo(m:Message):
        parts=(m.text or '').split(maxsplit=1)
        if len(parts)<2:
            await m.answer('🎟 Чтобы активировать промокод, отправьте: /promo КОД'); return
        code=parts[1].strip().upper()
        async with Session() as s:
            u=await s.scalar(select(User).where(User.id==m.from_user.id).with_for_update())
            if not u:
                await m.answer('Сначала запустите бота командой /start.'); return
            promo=await s.scalar(select(PromoCode).where(PromoCode.code==code).with_for_update())
            if not promo or not promo.active or (promo.max_uses and promo.uses_count>=promo.max_uses):
                await m.answer('❌ Промокод не найден, отключён или лимит активаций исчерпан.'); return
            used=await s.scalar(select(PromoRedemption.id).where(PromoRedemption.promo_code==code,PromoRedemption.user_id==u.id))
            if used:
                await m.answer('Вы уже активировали этот промокод.'); return
            u.coins+=promo.reward_coins; u.gems+=promo.reward_gems; promo.uses_count+=1
            s.add(PromoRedemption(promo_code=code,user_id=u.id,coins_awarded=promo.reward_coins,gems_awarded=promo.reward_gems))
            await s.commit()
        await m.answer(f'🎉 Промокод {code} активирован!\nПолучено: {promo.reward_coins} 🪙 · {promo.reward_gems} 💎')

    @dp.message(Command('givecoins'))
    async def givecoins(m):
        if not admin(m.from_user.id): return
        try: _,uid,amount=m.text.split(); uid=int(uid); amount=int(amount)
        except: await m.answer('Использование: /givecoins USER_ID AMOUNT'); return
        async with Session() as s:
            u=await s.get(User,uid)
            if not u or amount==0: await m.answer('Игрок не найден или сумма равна нулю.'); return
            if u.coins+amount<0: await m.answer('Операция приведёт к отрицательному балансу.'); return
            u.coins+=amount; await audit(s,m.from_user.id,'givecoins',uid,str(amount)); await s.commit()
        await m.answer('Баланс монет изменён.')

    @dp.message(Command('givegems'))
    async def givegems(m):
        if not admin(m.from_user.id): return
        try: _,uid,amount=m.text.split(); uid=int(uid); amount=int(amount)
        except: await m.answer('Использование: /givegems USER_ID AMOUNT'); return
        async with Session() as s:
            u=await s.get(User,uid)
            if not u or amount==0: await m.answer('Игрок не найден или сумма равна нулю.'); return
            if u.gems+amount<0: await m.answer('Операция приведёт к отрицательному балансу.'); return
            u.gems+=amount; await audit(s,m.from_user.id,'givegems',uid,str(amount)); await s.commit()
        await m.answer('Баланс алмазов изменён.')

    @dp.message(Command('givecard'))
    async def givecard(m):
        if not admin(m.from_user.id): return
        try: _,uid,cid=m.text.split(); uid=int(uid); cid=int(cid)
        except: await m.answer('Формат: /givecard ID_ИГРОКА ID_ШАБЛОНА_КАРТОЧКИ.'); return
        async with Session() as s:
            if not await s.get(User,uid) or not await s.get(Card,cid): await m.answer('Пользователь или шаблон карточки не найден.'); return
            oc=OwnedCard(user_id=uid,card_id=cid,slot=''); s.add(oc); await audit(s,m.from_user.id,'givecard',uid,str(cid)); await s.commit()
        await m.answer('Карточка выдана.')

    @dp.message(Command('createcard'))
    async def createcard(m):
        if not admin(m.from_user.id): return
        raw=m.text.partition(' ')[2]
        try:
            p=[x.strip() for x in raw.split('|')]
            name,pos,ovr,rarity,country,club,league=p[:7]
            if pos not in POSITIONS or rarity not in RARITY_RANGES or not 50<=int(ovr)<=100: raise ValueError('Позиция/редкость/OVR указаны неверно (OVR 50–100).')
            card=Card(player_name=name,player_key=p[8] if len(p)>8 else '',position=pos,ovr=int(ovr),rarity=rarity,country=country,club=club,league=league,image_url=p[7] if len(p)>7 else '',source='admin')
        except Exception as e:
            await m.answer(f'Ошибка: {e}\nФормат: name|position|ovr|rarity|country|club|league|image_url|player_key'); return
        async with Session() as s:
            s.add(card); await audit(s,m.from_user.id,'createcard',0,name); await s.commit(); await s.refresh(card)
        await m.answer(f'Карточка создана: ID {card.id}.')

    @dp.message(Command('transfercard'))
    async def transfercard(m):
        if not admin(m.from_user.id): return
        try:
            _,cid,club,league,season_value=m.text.split(maxsplit=4); cid=int(cid)
        except: await m.answer('Формат: /transfercard ID_КАРТОЧКИ НОВЫЙ_КЛУБ НОВАЯ_ЛИГА СЕЗОН.'); return
        async with Session() as s:
            old=await s.get(Card,cid)
            if not old: await m.answer('Карточка не найдена.'); return
            new=Card(player_name=old.player_name,player_key=old.player_key,position=old.position,ovr=old.ovr,
                rarity=old.rarity,country=old.country,club=club,league=league,season=season_value,
                image_url=old.image_url,source='team-transfer',active=True)
            old.active=False
            s.add(new); await audit(s,m.from_user.id,'transfercard',cid,f'{club}/{league}/{season_value}'); await s.commit(); await s.refresh(new)
        await m.answer(f'Создана новая историческая версия карточки #{new.id}. Старая версия #{cid} не изменена; OVR сохранён.')

    @dp.message(Command('editcard'))
    async def editcard(m):
        if not admin(m.from_user.id): return
        try: _,cid,field,value=m.text.split(maxsplit=3); cid=int(cid)
        except: await m.answer('Формат: /editcard ID ovr|rarity|country|image_url VALUE'); return
        async with Session() as s:
            c=await s.get(Card,cid)
            if not c: await m.answer('Карточка не найдена.'); return
            if field=='ovr':
                v=int(value)
                if not 50<=v<=100: await m.answer('OVR: 50–100.'); return
                c.ovr=v
            elif field=='rarity':
                if value not in RARITIES: await m.answer('Неизвестная редкость.'); return
                c.rarity=value
            elif field in ('country','image_url'): setattr(c,field,value[:500])
            else: await m.answer('Поле недоступно для редактирования.'); return
            await audit(s,m.from_user.id,'editcard',cid,f'{field}={value}'); await s.commit()
        await m.answer('Карточка обновлена.')

    @dp.message(Command('setpack'))
    async def setpack(m):
        if not admin(m.from_user.id): return
        # /setpack PACK_NAME|PRICE|coins-or-gems|RARITY=WEIGHT,...
        try:
            fields=m.text.partition(' ')[2].split('|')
            name=fields[0].strip(); price=int(fields[1]); currency=fields[2].strip()
            odds=[]
            for item in fields[3].split(','):
                rarity,weight=item.split('='); odds.append((rarity.strip(),int(weight)))
            async with Session() as s:
                await save_pack_config(s,name,price,currency,odds); await audit(s,m.from_user.id,'setpack',0,name); await s.commit()
            await m.answer('Настройки пака сохранены.')
        except Exception as e: await m.answer(f'Не удалось сохранить: {e}\nФормат: /setpack Премиум|1500|coins|Обычный=35,Редкий=35,Сверхредкий=20,Эпический=8,Мифический=2')

    @dp.message(Command('bpgrant'))
    async def bpgrant(m):
        if not admin(m.from_user.id): return
        try: _,uid,xp=m.text.split(); uid=int(uid); xp=int(xp)
        except: await m.answer('Формат: /bpgrant ID_ИГРОКА КОЛИЧЕСТВО_XP.'); return
        if xp<=0 or xp>100000: await m.answer('XP должен быть от 1 до 100000.'); return
        async with Session() as s:
            if not await s.get(User,uid): await m.answer('Игрок не найден.'); return
            bp=await s.scalar(select(BattlePassProgress).where(BattlePassProgress.user_id==uid,BattlePassProgress.season==season_key()).with_for_update())
            if not bp:
                bp=BattlePassProgress(user_id=uid,season=season_key(),xp=0,premium=False,claimed_free=[],claimed_premium=[]); s.add(bp)
            bp.xp=min(10000,bp.xp+xp); await audit(s,m.from_user.id,'bpgrant',uid,str(xp)); await s.commit()
        await m.answer('XP Battle Pass начислен.')

    @dp.message(Command('cardactive'))
    async def cardactive(m):
        if not admin(m.from_user.id): return
        try: _,cid,value=m.text.split(); cid=int(cid); value=value.lower()
        except: await m.answer('Формат: /cardactive ID_КАРТОЧКИ on|off. Укажите on, чтобы включить выпадение, или off, чтобы отключить.'); return
        if value not in ('on','off'): await m.answer('Укажите on или off.'); return
        async with Session() as s:
            card=await s.get(Card,cid)
            if not card: await m.answer('Карточка не найдена.'); return
            card.active=(value=='on'); await audit(s,m.from_user.id,'cardactive',cid,value); await s.commit()
        await m.answer('Карточка включена в пул выпадения.' if value=='on' else 'Карточка отключена от новых выпадений; уже выданные экземпляры сохраняются.')

    @dp.message(Command('ban'))
    async def ban(m):
        if not admin(m.from_user.id): return
        try: uid=int(m.text.split()[1])
        except: await m.answer('Формат: /ban ID_ИГРОКА.'); return
        async with Session() as s:
            u=await s.get(User,uid)
            if not u: await m.answer('Игрок не найден.'); return
            u.is_banned=True; await audit(s,m.from_user.id,'ban',uid); await s.commit()
        await m.answer('Игрок заблокирован.')

    @dp.message(Command('unban'))
    async def unban(m):
        if not admin(m.from_user.id): return
        try: uid=int(m.text.split()[1])
        except: await m.answer('Формат: /unban ID_ИГРОКА.'); return
        async with Session() as s:
            u=await s.get(User,uid)
            if not u: await m.answer('Игрок не найден.'); return
            u.is_banned=False; await audit(s,m.from_user.id,'unban',uid); await s.commit()
        await m.answer('Игрок разблокирован.')

    @dp.message(Command('stats'))
    async def stats(m):
        if not admin(m.from_user.id): return
        async with Session() as s:
            users=await s.scalar(select(func.count(User.id))); cards=await s.scalar(select(func.count(Card.id)))
            matches=await s.scalar(select(func.count(Match.id))); listings=await s.scalar(select(func.count(MarketListing.id)).where(MarketListing.status=='active'))
            await m.answer(f'📊 Статистика\nИгроки: {users}\nШаблоны карточек: {cards}\nМатчи: {matches}\nАктивные лоты: {listings}')

    @dp.message(Command('auction_queue'))
    async def auction_queue(m):
        if not admin(m.from_user.id): return
        async with Session() as s:
            rows=(await s.scalars(select(Auction).where(Auction.status=='submitted').order_by(Auction.created_at))).all()
            lines=['ЗАЯВКИ НА АУКЦИОН']
            for a in rows:
                oc=await s.get(OwnedCard,a.owned_card_id); c=await s.get(Card,oc.card_id) if oc else None
                if c: lines.append(f'#{a.id} · владелец {a.owner_id} · {c.player_name} · OVR {c.ovr}\n/auction_create {a.id} СТАРТ ШАГ ЧАСЫ МИН_СТАВКА МАКС_СТАВКА')
            await m.answer('\n'.join(lines) if len(lines)>1 else 'Очередь пуста.')

    @dp.message(Command('auction_create'))
    async def auction_create(m):
        if not admin(m.from_user.id): return
        try:
            _,aid,start,step,hours,minprice,maxbid=m.text.split()
            aid=int(aid); start=int(start); step=int(step); hours=int(hours); minprice=int(minprice); maxbid=int(maxbid)
        except: await m.answer('Формат: /auction_create ID НАЧАЛЬНАЯ_ЦЕНА ШАГ_СТАВКИ СРОК_В_ЧАСАХ МИН_ЦЕНА МАКС_СТАВКА'); return
        async with Session() as s:
            a=await s.get(Auction,aid)
            if not a or a.status!='submitted': await m.answer('Заявка не найдена или уже обработана.'); return
            if min(start,step,hours,minprice)<=0 or maxbid<start or maxbid<minprice or maxbid>1_000_000_000: await m.answer('Проверьте цены, шаг, срок и лимит.'); return
            from datetime import timedelta
            a.start_price=start; a.highest_bid=0; a.bid_step=step; a.min_price=minprice; a.max_bid=maxbid; a.ends_at=now()+timedelta(hours=hours); a.status='active'
            await audit(s,m.from_user.id,'auction_create',aid); await s.commit()
            try: await m.bot.send_message(a.owner_id,f'🔨 Ваша карточка выставлена на аукцион #{aid}.')
            except Exception: pass
        await m.answer('Аукцион запущен.')

    @dp.message(Command('auction_cancel'))
    async def auction_cancel(m):
        if not admin(m.from_user.id): return
        try: aid=int(m.text.split()[1])
        except: await m.answer('Формат: /auction_cancel ID'); return
        async with Session() as s:
            a=await s.scalar(select(Auction).where(Auction.id==aid).with_for_update())
            if not a or a.status not in ('active','submitted'): await m.answer('Аукцион недоступен.'); return
            if a.winner_id:
                bidder=await s.scalar(select(User).where(User.id==a.winner_id).with_for_update())
                if bidder:
                    bidder.coins+=a.highest_bid
                    s.add(EconomyLedger(user_id=bidder.id,currency='coins',amount=a.highest_bid,reason='auction_bid_refund',reference_id=a.id))
            oc=await s.scalar(select(OwnedCard).where(OwnedCard.id==a.owned_card_id).with_for_update())
            if oc and oc.user_id==a.owner_id: oc.locked=False
            a.status='cancelled'; await audit(s,m.from_user.id,'auction_cancel',aid); await s.commit()
        await m.answer('Аукцион отменён, блокировки и резервы ставок сняты.')

    @dp.message(Command('season_reset'))
    async def season_reset(m):
        if not admin(m.from_user.id): return
        async with Session() as s:
            users=(await s.scalars(select(User))).all()
            for u in users: u.rating=0
            await audit(s,m.from_user.id,'season_reset'); await s.commit()
        await m.answer('Рейтинг сезона сброшен; постоянная статистика сохранена.')

    @dp.message(F.document)
    async def upload_csv(m):
        if not admin(m.from_user.id): return
        doc=m.document
        filename=doc.file_name or 'upload.csv'
        if not filename.lower().endswith('.csv'): await m.answer('Пришлите CSV-файл.'); return
        if (doc.file_size or 0)>5*1024*1024: await m.answer('CSV-файл должен быть меньше 5 МБ.'); return
        os.makedirs('uploads',exist_ok=True); path=f'uploads/{doc.file_unique_id}_{filename.replace("/", "_")}'
        await m.bot.download(doc,destination=path)
        async with Session() as s:
            try:
                if filename.lower().startswith('chem'):
                    count=await import_chemistry(s,path); task='пар совместной сыгранности'
                    await audit(s,m.from_user.id,'import_chemistry',0,f'{count} rows')
                else:
                    count=await import_cards(s,path); task='карточек'
                    await audit(s,m.from_user.id,'import_cards',0,f'{count} cards')
                await s.commit()
            except Exception as e:
                await s.rollback(); await m.answer(f'Импорт не выполнен: {e}'); return
        await m.answer(f'✅ Импортировано: {count} {task}.')



    @dp.callback_query(F.data.startswith('player:'))
    async def player_menu_callback(c:CallbackQuery):
        action=c.data.split(':',1)[1]
        handlers={
            'team':team,'play':play,'support':support_start,'cards':collection,
            'packs':packs,'market':market,'auctions':auctions,'leaderboard':leaderboard,
            'daily':daily,'tournament':tournament,'clans':clan,'battlepass':battlepass,
            'profile':profile,'balance':balance,'help':help_cmd
        }
        handler=handlers.get(action)
        if not handler:
            await c.answer('Раздел не найден.',show_alert=True); return
        try:
            async def proxy_answer(*args,**kwargs):
                return await c.message.answer(*args,**kwargs)
            async def proxy_answer_photo(*args,**kwargs):
                return await c.message.answer_photo(*args,**kwargs)
            from types import SimpleNamespace
            menu_text={'clans':'🛡 Кланы','battlepass':'🎟 Battle Pass'}.get(action,'')
            player_message=SimpleNamespace(from_user=c.from_user,bot=c.bot,text=menu_text,
                answer=proxy_answer,answer_photo=proxy_answer_photo)
            await handler(player_message)
            # Do not append the main menu automatically: each section should
            # remain visible until the player explicitly returns.
            await c.answer()
        except Exception:
            await c.answer('Не удалось открыть раздел. Попробуйте ещё раз.',show_alert=True)

    await dp.start_polling(bot)
