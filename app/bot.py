import asyncio, csv, os, random, shlex, html
from datetime import date, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select, desc, func, or_
from sqlalchemy.exc import IntegrityError
from .config import BOT_TOKEN, ADMIN_IDS, BATTLEPASS_PREMIUM_PRICE
from .db import Session, init_db
from .models import (User, Card, OwnedCard, Match, MarketListing, Auction, AuctionBid,
    TradeOffer, Clan, ClanMember, DailyTournament, TournamentEntry, TournamentPrizeConfig, ClanTournamentEntry, WeeklyClanTournament, WeeklyClanTournamentEntry, BattlePassProgress,
    QuestProgress, Season, AuditLog, EconomyLedger, PromoCode, PromoRedemption, SupportMessage)
from .seed import seed_cards
from .game import PACKS, POSITIONS, RARITIES, RARITY_RANGES, PVE_REWARDS, LEAGUES, team_power, league_for_rating
from .worker import run_worker, prepare_match
from .services import (get_or_create_user, lineup, claim_daily, open_pack, equip_card,
    start_pve_match, start_pvp_queue, create_market_listing, buy_market_listing,
    create_trade, resolve_trade, submit_auction_card, place_bid,
    create_clan, join_clan, ensure_daily_tournament, ensure_daily_tournament_participants, ensure_season, progress_after_match,
    claim_quest, claim_bp_reward, import_cards, import_chemistry, audit, now, season_key, save_pack_config,
    create_clan_invite, resolve_clan_invite, leave_clan, set_clan_role, transfer_clan_owner, kick_clan_member)

def keyboard():
    kb=InlineKeyboardBuilder()
    items=[
        ('🏒 Моя команда','team'),('🏒 Матчи','matches'),
        ('✉️ Написать админу','support'),('🃏 Карточки','cards'),
        ('📦 Паки','packs'),('🏪 Рынок','market'),
        ('🔨 Аукционы','auctions'),('🏆 Рейтинг','leaderboard'),
        ('🎁 Ежедневная награда','daily'),('⚔️ Турнир','tournament'),
        ('🛡 Кланы','clans'),('🔁 Обмены','trades'),('🎟 Battle Pass','battlepass'),
        ('👤 Профиль','profile'),('🪙 Баланс','balance'),('🎟 Промокоды','promo'),
        ('❔ Помощь','help')
    ]
    for label,key in items: kb.button(text=label,callback_data=f'player:{key}')
    kb.adjust(2)
    return kb.as_markup()
MOSCOW=ZoneInfo('Europe/Moscow')
def format_msk(value, naive_is_utc=True):
    if not value: return '—'
    if value.tzinfo is None:
        value=value.replace(tzinfo=timezone.utc if naive_is_utc else MOSCOW)
    return value.astimezone(MOSCOW).strftime('%d.%m.%Y %H:%M:%S')

def admin(uid): return uid in ADMIN_IDS

TELEGRAM_POST_URL='https://t.me/bullet_kings_forms/40'
_TELEGRAM_POST_PHOTO=None

async def telegram_post_photo():
    global _TELEGRAM_POST_PHOTO
    if _TELEGRAM_POST_PHOTO:
        return _TELEGRAM_POST_PHOTO
    import aiohttp, re
    page_url='https://t.me/s/bullet_kings_forms/40?single'
    timeout=aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout,headers={'User-Agent':'Mozilla/5.0'}) as session:
        async with session.get(page_url) as response:
            response.raise_for_status()
            page=await response.text()
    # Telegram's public channel preview stores post photos in the CSS background.
    patterns=[
        r"""tgme_widget_message_photo_wrap[^>]*style="[^"]*?background-image\s*:\s*url\(['"]?(https?://[^'")]+)""",
        r"""<img[^>]+src="(https?://[^"]+)"[^>]+class="[^"]*tgme_widget_message_photo""",
        r"""background-image\s*:\s*url\(['"]?(https?://[^'")]+)"""
    ]
    for pattern in patterns:
        match=re.search(pattern,page,re.I)
        if match:
            _TELEGRAM_POST_PHOTO=match.group(1).replace('&amp;','&')
            return _TELEGRAM_POST_PHOTO
    raise RuntimeError('В публикации Telegram не найдено изображение')

async def send_main_menu(m, caption, reply_markup):
    try:
        photo=await telegram_post_photo()
        await m.answer_photo(photo=photo,caption=caption,reply_markup=reply_markup,parse_mode='HTML')
    except Exception as e:
        # Keep the bot usable if Telegram's public preview is temporarily unavailable.
        print(f'Не удалось получить фото главного меню из Telegram: {e}')
        await m.answer(caption,reply_markup=reply_markup,parse_mode='HTML')

async def send_section_photo(m, url, caption, reply_markup=None, parse_mode=None):
    await m.answer(caption, reply_markup=reply_markup, parse_mode=parse_mode)

PHOTO_ARENA=TELEGRAM_POST_URL
PHOTO_GOALIE='https://images.pexels.com/photos/6468596/pexels-photo-6468596.jpeg'
PHOTO_GAME='https://images.pexels.com/photos/8974638/pexels-photo-8974638.jpeg'
PHOTO_TRAINING='https://images.pexels.com/photos/6847600/pexels-photo-6847600.jpeg'

RARITY_EMOJI = {
    'Обычный':'⚪', 'Редкий':'🟢', 'Элитный':'🔵', 'Сверхредкий':'🟣',
    'Эпический':'🟡', 'Мифический':'🟠', 'Легендарный':'🔴',
    'Хроматический':'🟤', 'Легенда':'⚫', 'Икона':'🌈'
}
def rarity_label(value):
    return f"{RARITY_EMOJI.get(value, '💎')} {value}"

def pack_keyboard():
    kb=InlineKeyboardBuilder()
    for name,d in PACKS.items(): kb.button(text=f"{name} · {d['price']} {'🪙' if d['currency']=='coins' else '💎'}",callback_data=f'packview:{list(PACKS).index(name)}')
    kb.adjust(1); return kb.as_markup()

def pve_keyboard():
    kb=InlineKeyboardBuilder()
    kb.button(text='🏆 Рейтинговый матч',callback_data='pvp_start')
    kb.button(text='🤝 Матч по ID',callback_data='friendly_id_start')
    kb.adjust(1); return kb.as_markup()

async def active_user(session,tg):
    u,_=await get_or_create_user(session,tg)
    if u.is_banned: raise ValueError('Ваш аккаунт заблокирован.')
    return u

async def owned_card_text(session,oc):
    c=await session.get(Card,oc.card_id)
    return f'#{oc.id} {c.player_name} · {c.position} · {c.ovr} OVR · {rarity_label(c.rarity)}'

async def render_team(session,uid):
    rows=await lineup(session,uid); mapping={c.position:(o,c) for o,c in rows}
    lines=['🏒 МОЯ КОМАНДА']
    for pos in POSITIONS:
        item=mapping.get(pos)
        lines.append(f"{pos}: {item[1].player_name} · {item[1].ovr} OVR" if item else f'{pos}: —')
    cards=[c for _,c in rows]
    lines.append(f'\n💪 Сила состава: {team_power(cards)} OVR')
    from .game import chemistry
    ch=await chemistry(session,cards); lines.append(f'🤝 Сыгранность: {ch}/40')
    lines.append('\nДобавить или снять карточку можно кнопками ниже.')
    return '\n'.join(lines)

def team_keyboard():
    kb=InlineKeyboardBuilder()
    for pos in POSITIONS:
        kb.button(text=f'➕ Поставить {pos}',callback_data=f'team:choose:{pos}')
        kb.button(text=f'➖ Убрать {pos}',callback_data=f'team:remove:{pos}')
    kb.button(text='⬅️ Главное меню',callback_data='player:home')
    kb.adjust(2); return kb.as_markup()

def collection_keyboard(cards, session):
    return None

async def profile_text(u):
    import html
    player_name=html.escape(str(u.first_name or u.id))
    return (f'👤 Профиль · {player_name}\n'
            f'🆔 ID игрока: <code>{u.id}</code>  ← нажмите и удерживайте, чтобы скопировать\n'
            f'🪙 {u.coins} монет · 💎 {u.gems} алмазов\n'
            f'🏆 {league_for_rating(u.rating)} · ⭐ {u.rating} рейтинга\n'
            f'📊 Матчи: {u.games} · Победы/поражения: {u.wins}/{u.losses}\n'
            f'📈 Лучший рейтинг сезона: {u.lifetime_rating}')

async def get_match_for_user(session,uid):
    return await session.scalar(select(Match).where(or_(Match.home_user_id==uid,Match.away_user_id==uid),Match.status!='paired').order_by(Match.id.desc()))

async def main():
    if not BOT_TOKEN: raise RuntimeError('Укажите BOT_TOKEN в файле .env')
    await init_db()
    async with Session() as s:
        await seed_cards(s); await ensure_season(s); await ensure_daily_tournament_participants(s); await s.commit()
    bot=Bot(BOT_TOKEN); dp=Dispatcher()
    await bot.set_my_commands([BotCommand(command='start',description='Открыть главное меню'),BotCommand(command='team',description='Моя команда'),BotCommand(command='cards',description='Моя коллекция'),BotCommand(command='play',description='Играть'),BotCommand(command='market',description='Рынок карточек'),BotCommand(command='auctions',description='Аукционы'),BotCommand(command='daily',description='Ежедневная награда'),BotCommand(command='tournament',description='Ежедневный турнир'),BotCommand(command='clan',description='Кланы'),BotCommand(command='ranked',description='Рейтинговая лига'),BotCommand(command='trades',description='Обмены карточками'),BotCommand(command='promo',description='Активировать промокод'),BotCommand(command='help',description='Помощь')])

    @dp.message(Command('start'))
    async def start(m:Message):
        await m.answer('Меню обновлено: теперь управление через кнопки ниже.',reply_markup=ReplyKeyboardRemove())
        async with Session() as s:
            u,new=await get_or_create_user(s,m.from_user)
            await ensure_daily_tournament_participants(s)
            await s.commit()
            if u.is_banned: await m.answer('Ваш аккаунт заблокирован.'); return
            welcome='Добро пожаловать в Kosh KHL Cards!\nСтартовый набор: 20 💎 · 5000 🪙 · команда из 6 карточек.' if new else 'С возвращением в Kosh KHL Cards!'
            await send_main_menu(m,f'🏒 KOSH KHL CARDS\n\n{welcome}\n\nВыберите раздел в меню.',reply_markup=keyboard())


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

    @dp.message(Command('id'))
    async def show_player_id(m:Message):
        await m.answer(f'🆔 Ваш Telegram ID: {m.from_user.id}')

    @dp.message(Command('help'))
    @dp.message(F.text=='❔ Помощь')
    async def help_cmd(m):
        await m.answer('Команды игрока:\n/equip ID POS — поставить карточку в состав\n/pve — матчи против AI\n/pvp — обычный PvP поиск\n/ranked — рейтинговый поиск\n/challenge ID [ставка] — вызов игрока\n/cancelchallenge — отменить исходящий вызов\n/sell ID PRICE — выставить на рынок\n/buy ID — купить лот\n/trades — обмены карточками через кнопки\n/auction_submit ID — передать карточку администрации\n/bid AUCTION_ID AMOUNT — ставка\n/tournament — ежедневный турнир\n/clan create НАЗВАНИЕ | /clan join ID | /clan invite USER_ID | /clan role USER_ID admin|member | /clan transfer USER_ID | /clan kick USER_ID | /clan leave | /clanbattle — недельная клановая битва\n/bp — Battle Pass с 10 страницами уровней\n/match — статус и текущая трансляция\n/leaderboard — рейтинг игроков\n/promo КОД — активировать промокод.')

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
            await m.answer(await profile_text(u),parse_mode='HTML')

    @dp.message(Command('team'))
    @dp.message(F.text=='🏒 Моя команда')
    async def team(m):
        async with Session() as s:
            try: await active_user(s,m.from_user)
            except ValueError as e: await m.answer(str(e)); return
            await send_section_photo(m,PHOTO_GOALIE,await render_team(s,m.from_user.id),reply_markup=team_keyboard())

    @dp.callback_query(F.data.startswith('team:'))
    async def team_action(c:CallbackQuery):
        _,action,pos=c.data.split(':',2)
        if pos not in POSITIONS: await c.answer('Позиция не найдена.',show_alert=True); return
        async with Session() as s:
            try: await active_user(s,c.from_user)
            except ValueError as e: await c.answer(str(e),show_alert=True); return
            if action=='remove':
                oc=await s.scalar(select(OwnedCard).where(OwnedCard.user_id==c.from_user.id,OwnedCard.slot==pos).with_for_update())
                if not oc: await c.answer('На этой позиции уже нет карточки.',show_alert=True); return
                if oc.locked: await c.answer('Карточка заблокирована торговой операцией.',show_alert=True); return
                oc.slot=''; await s.commit()
                await c.message.answer(f'➖ Карточка снята с позиции {pos}.',reply_markup=team_keyboard())
            elif action=='choose':
                cards=(await s.scalars(select(OwnedCard).where(OwnedCard.user_id==c.from_user.id,OwnedCard.locked.is_(False)).order_by(OwnedCard.id.desc()).limit(100))).all()
                kb=InlineKeyboardBuilder(); count=0
                for oc in cards:
                    card=await s.get(Card,oc.card_id)
                    if card and card.position==pos:
                        kb.button(text=f'{card.player_name} · {card.ovr} OVR'+(f' · сейчас {oc.slot}' if oc.slot else ''),callback_data=f'teamequip:{oc.id}:{pos}'); count+=1
                kb.button(text='⬅️ Назад к команде',callback_data='player:team'); kb.adjust(1)
                await c.message.answer(f'Выберите карточку на позицию {pos}:' if count else f'Нет доступных карточек на позицию {pos}. Откройте паки или снимите блокировку с карточки.',reply_markup=kb.as_markup())
        await c.answer()

    @dp.callback_query(F.data.startswith('teamequip:'))
    async def team_equip_selected(c:CallbackQuery):
        try: _,oid,pos=c.data.split(':',2); oid=int(oid)
        except ValueError: await c.answer('Карточка не найдена.',show_alert=True); return
        async with Session() as s:
            try:
                await active_user(s,c.from_user); card=await equip_card(s,c.from_user.id,oid,pos)
                await c.message.answer(f'✅ {card.player_name} поставлен на {pos}.\n\n'+await render_team(s,c.from_user.id),reply_markup=team_keyboard())
            except ValueError as e: await c.answer(str(e),show_alert=True); return
        await c.answer('Состав обновлён.')

    @dp.message(Command('equip'))
    async def equip(m):
        try: _,oid,slot=m.text.split(); oid=int(oid); slot=slot.upper()
        except: await m.answer('Формат: /equip ID LW|C|RW|LD|RD|G'); return
        async with Session() as s:
            try:
                await active_user(s,m.from_user); c=await equip_card(s,m.from_user.id,oid,slot)
                await m.answer(f'✅ {c.player_name} установлен на позицию {slot}.\n\n'+await render_team(s,m.from_user.id))
            except ValueError as e: await m.answer(str(e))

    async def render_collection(target, uid, league=None, position=None, rarity=None, page=0):
        async with Session() as s:
            u=await s.get(User,uid)
            if not u or u.is_banned:
                await target.answer('Аккаунт недоступен.'); return
            conditions=[OwnedCard.user_id==uid]
            if league: conditions.append(Card.league==league)
            if position: conditions.append(Card.position==position)
            if rarity: conditions.append(Card.rarity==rarity)
            query=select(OwnedCard).join(Card,Card.id==OwnedCard.card_id).where(*conditions)
            total=await s.scalar(select(func.count(OwnedCard.id)).join(Card,Card.id==OwnedCard.card_id).where(*conditions)) or 0
            cards=(await s.scalars(query.order_by(OwnedCard.id.desc()).offset(page*12).limit(12))).all()
            active=[]
            if league: active.append(f'лига: {league}')
            if position: active.append(f'позиция: {position}')
            if rarity: active.append(f'редкость: {rarity_label(rarity)}')
            lines=[f'🃏 МОИ КАРТОЧКИ · {", ".join(active) if active else "все карточки"} · стр. {page+1} · всего {total}']
            kb=InlineKeyboardBuilder()
            for oc in cards:
                card=await s.get(Card,oc.card_id)
                if not card: continue
                lines.append(f'\n{card.player_name} · {card.position} · {card.ovr} OVR · {rarity_label(card.rarity)}'+(f' · Состав: {oc.slot}' if oc.slot else '')+(' · 🔒 занята' if oc.locked else ''))
                if not oc.locked: kb.button(text=f'🃏 {card.player_name} · {card.ovr}',callback_data=f'cardmenu:{oc.id}')
            kb.button(text='🔎 Фильтр',callback_data='collection:filter')
            if page>0: kb.button(text='⬅️ Назад',callback_data=f'collection:page:{page-1}')
            if (page+1)*12<total: kb.button(text='Вперёд ➡️',callback_data=f'collection:page:{page+1}')
            kb.button(text='⬅️ Главное меню',callback_data='player:home'); kb.adjust(1)
            await target.answer('\n'.join(lines),reply_markup=kb.as_markup())

    async def render_collection_filters(target, uid, group=None):
        async with Session() as s:
            kb=InlineKeyboardBuilder()
            if group is None:
                kb.button(text='🏒 Лиги',callback_data='collection:group:l')
                kb.button(text='📍 Позиции',callback_data='collection:group:p')
                kb.button(text='💎 Редкости',callback_data='collection:group:r')
                title='🔎 ФИЛЬТР КАРТОЧЕК\nВыбери тип фильтра:'
            elif group=='l':
                values=list((await s.scalars(select(Card.league).join(OwnedCard,OwnedCard.card_id==Card.id).where(OwnedCard.user_id==uid,Card.league!='').distinct().order_by(Card.league))).all())
                for i,value in enumerate(values): kb.button(text=value,callback_data=f'collection:pick:l:{i}')
                title='🏒 ВЫБЕРИ ЛИГУ:'
            elif group=='p':
                values=list((await s.scalars(select(Card.position).join(OwnedCard,OwnedCard.card_id==Card.id).where(OwnedCard.user_id==uid).distinct().order_by(Card.position))).all())
                for i,value in enumerate(values): kb.button(text=value,callback_data=f'collection:pick:p:{i}')
                title='📍 ВЫБЕРИ ПОЗИЦИЮ:'
            elif group=='r':
                values=list((await s.scalars(select(Card.rarity).join(OwnedCard,OwnedCard.card_id==Card.id).where(OwnedCard.user_id==uid).distinct().order_by(Card.rarity))).all())
                for i,value in enumerate(values): kb.button(text=rarity_label(value),callback_data=f'collection:pick:r:{i}')
                title='💎 ВЫБЕРИ РЕДКОСТЬ:'
            else:
                title='Фильтр не найден.'
            if group is not None:
                kb.button(text='⬅️ К типам фильтра',callback_data='collection:filter')
            kb.button(text='♻️ Сбросить фильтры',callback_data='collection:clear')
            kb.button(text='⬅️ К карточкам',callback_data='collection:page:0')
            kb.adjust(1)
            await target.answer(title,reply_markup=kb.as_markup())

    @dp.message(Command('cards'))
    @dp.message(F.text=='🃏 Карточки')
    async def collection(m):
        await render_collection(m,m.from_user.id)

    @dp.callback_query(F.data.startswith('collection:'))
    async def collection_filter(c:CallbackQuery):
        parts=c.data.split(':')
        from types import SimpleNamespace
        async def edit_collection(text, **kwargs):
            try: return await c.message.edit_text(text, **kwargs)
            except Exception: return await c.message.answer(text, **kwargs)
        if parts[1]=='filter':
            await render_collection_filters(SimpleNamespace(answer=edit_collection),c.from_user.id)
        elif parts[1]=='clear':
            await render_collection(SimpleNamespace(answer=edit_collection),c.from_user.id)
        elif parts[1]=='page':
            try: page=max(0,int(parts[2]))
            except (ValueError,IndexError): page=0
            await render_collection(SimpleNamespace(answer=edit_collection),c.from_user.id,page=page)
        elif parts[1]=='group':
            group=parts[2] if len(parts)>2 else None
            await render_collection_filters(SimpleNamespace(answer=edit_collection),c.from_user.id,group)
        elif parts[1]=='pick':
            try:
                kind=parts[2]; index=int(parts[3])
                async with Session() as s:
                    if kind=='l':
                        values=list((await s.scalars(select(Card.league).join(OwnedCard,OwnedCard.card_id==Card.id).where(OwnedCard.user_id==c.from_user.id,Card.league!='').distinct().order_by(Card.league))).all())
                        await render_collection(SimpleNamespace(answer=edit_collection),c.from_user.id,league=values[index])
                    elif kind=='p':
                        values=list((await s.scalars(select(Card.position).join(OwnedCard,OwnedCard.card_id==Card.id).where(OwnedCard.user_id==c.from_user.id).distinct().order_by(Card.position))).all())
                        await render_collection(SimpleNamespace(answer=edit_collection),c.from_user.id,position=values[index])
                    elif kind=='r':
                        values=list((await s.scalars(select(Card.rarity).join(OwnedCard,OwnedCard.card_id==Card.id).where(OwnedCard.user_id==c.from_user.id).distinct().order_by(Card.rarity))).all())
                        await render_collection(SimpleNamespace(answer=edit_collection),c.from_user.id,rarity=values[index])
                    else: raise ValueError()
            except (ValueError,IndexError):
                await c.answer('Фильтр не найден.',show_alert=True); return
        await c.answer()

    @dp.callback_query(F.data.startswith('cardmenu:'))
    async def card_menu(c:CallbackQuery):
        try: oid=int(c.data.split(':',1)[1])
        except ValueError: await c.answer('Карточка не найдена.',show_alert=True); return
        async with Session() as s:
            oc=await s.get(OwnedCard,oid)
            if not oc or oc.user_id!=c.from_user.id: await c.answer('Карточка не принадлежит вам.',show_alert=True); return
            card=await s.get(Card,oc.card_id)
            kb=InlineKeyboardBuilder()
            if not oc.locked:
                kb.button(text=f'🏒 Поставить на {card.position}',callback_data=f'teamequip:{oc.id}:{card.position}')
                if oc.slot: kb.button(text='➖ Снять из состава',callback_data=f'team:remove:{oc.slot}')
                if not oc.slot: kb.button(text='🏪 Выставить на рынок',callback_data=f'marketcard:{oc.id}')
                kb.button(text='🔨 Передать на аукцион',callback_data=f'auctioncard:{oc.id}')
            kb.button(text='⬅️ К коллекции',callback_data='player:cards'); kb.adjust(1)
            await c.message.answer(f'🏒 {card.player_name}\n{card.position} · {card.ovr} OVR · {rarity_label(card.rarity)}\n'+('В составе: '+oc.slot if oc.slot else 'Не в составе')+('\n🔒 Карточка заблокирована.' if oc.locked else ''),reply_markup=kb.as_markup())
        await c.answer()

    @dp.message(Command('card'))
    async def card_details(m):
        try: owned_id=int(m.text.split()[1])
        except: await m.answer('Формат: /card OWNED_CARD_ID'); return
        async with Session() as s:
            oc=await s.get(OwnedCard,owned_id)
            if not oc or oc.user_id!=m.from_user.id: await m.answer('Карточка не найдена.'); return
            c=await s.get(Card,oc.card_id)
            caption=f'🏒 {c.player_name}\n{c.ovr} OVR · {c.position} · {c.rarity}\n{c.country} · {c.club} · {c.league}\nСезон: {c.season or "—"} · ID: #{oc.id}'
            await m.answer(caption)

    async def render_pack_carousel(target, index, uid, edit=False):
        from .models import PackConfig
        names=list(PACKS)
        index=max(0,min(index,len(names)-1))
        name=names[index]; base=PACKS[name]
        async with Session() as s:
            user=await s.get(User,uid)
            cfg=await s.get(PackConfig,name)
            d={'price':cfg.price,'currency':cfg.currency,'odds':cfg.odds} if cfg else base
            balance=f'🪙 Монеты: <b>{user.coins}</b> · 💎 Алмазы: <b>{user.gems}</b>' if user else ''
        currency='🪙' if d['currency']=='coins' else '💎'
        odds='\n'.join(f'  {rarity_label(r)} — <b>{p}%</b>' for r,p in d['odds'])
        caption=(
            f'📦 <b>{name.upper()} ПАК</b>\n'
            f'━━━━━━━━━━━━━━\n'
            f'{balance}\n💰 Цена: <b>{d["price"]} {currency}</b>\n\n'
            f'🎲 <b>Шансы выпадения</b>\n{odds}\n\n'
            f'Пак {index+1} из {len(names)}'
        )
        kb=InlineKeyboardBuilder()
        kb.button(text=f'🎁 Открыть за {d["price"]} {currency}',callback_data=f'packbuy:{name}')
        nav=[]
        if index>0: nav.append(InlineKeyboardButton(text='⬅️ Предыдущий',callback_data=f'packview:{index-1}'))
        if index<len(names)-1: nav.append(InlineKeyboardButton(text='Следующий ➡️',callback_data=f'packview:{index+1}'))
        if nav: kb.row(*nav)
        if edit:
            await target.edit_text(caption,reply_markup=kb.as_markup(),parse_mode='HTML')
        else:
            await target.answer(caption,reply_markup=kb.as_markup(),parse_mode='HTML')

    @dp.message(F.text=='📦 Паки')
    async def packs(m):
        await render_pack_carousel(m,0,m.from_user.id)

    @dp.callback_query(F.data.startswith('packview:'))
    async def pack_view(c:CallbackQuery):
        try: index=int(c.data.split(':',1)[1])
        except ValueError: await c.answer('Пак не найден.',show_alert=True); return
        await render_pack_carousel(c.message,index,c.from_user.id,edit=True)
        await c.answer()

    def pack_reveal_text(card, oc, stage):
        fields=[
            ('🌍 Страна',card.country or '—'),
            ('🏆 Лига',card.league or '—'),
            ('🏒 Команда',card.club or '—'),
            ('📍 Позиция',card.position),
            ('⭐ Рейтинг',f'{card.ovr} OVR'),
            ('💎 Редкость',rarity_label(card.rarity)),
            ('🎉 Игрок',card.player_name)
        ]
        lines=['🎁 <b>ОТКРЫТИЕ ПАКА</b>','━━━━━━━━━━━━━━']
        for i,(label,value) in enumerate(fields):
            lines.append(f'{label}: <b>{html.escape(str(value))}</b>' if i<=stage else f'{label}: <tg-spoiler>Скрыто</tg-spoiler>')
        lines.append(f'\nШаг {min(stage+1,len(fields))} из {len(fields)}')
        if stage>=len(fields)-1:
            lines.append(f'\n🆔 Карточка #{oc.id} добавлена в коллекцию!')
        else:
            lines.append('\nОткрывайте характеристики по одной.')
        return '\n'.join(lines)

    def reveal_keyboard(oc_id, stage):
        kb=InlineKeyboardBuilder()
        if stage<6: kb.button(text='✨ Открыть дальше',callback_data=f'reveal:{oc_id}:{stage+1}')
        else: kb.button(text='🃏 Перейти в коллекцию',callback_data='player:cards')
        kb.adjust(1)
        return kb.as_markup()

    @dp.callback_query(F.data.startswith('packbuy:'))
    async def buy_pack(c:CallbackQuery):
        name=c.data.split(':',1)[1]
        async with Session() as s:
            try:
                u=await active_user(s,c.from_user)
                oc,card=await open_pack(s,u,name)
            except ValueError as e: await c.answer(str(e),show_alert=True); return
            oc_id=oc.id
            reveal=pack_reveal_text(card,oc,0)
            await c.message.answer(reveal,reply_markup=reveal_keyboard(oc_id,0),parse_mode='HTML')
        await c.answer('Пак открыт!')

    @dp.callback_query(F.data.startswith('reveal:'))
    async def reveal_next(c:CallbackQuery):
        try: _,raw_id,raw_stage=c.data.split(':',2); oc_id=int(raw_id); stage=int(raw_stage)
        except ValueError: await c.answer('Этап открытия не найден.',show_alert=True); return
        if not 1<=stage<=6: await c.answer('Все характеристики уже открыты.'); return
        async with Session() as s:
            oc=await s.get(OwnedCard,oc_id)
            if not oc or oc.user_id!=c.from_user.id:
                await c.answer('Эта карточка вам не принадлежит.',show_alert=True); return
            card=await s.get(Card,oc.card_id)
            if not card: await c.answer('Карточка не найдена.',show_alert=True); return
            reveal=pack_reveal_text(card,oc,stage)
        await c.message.edit_text(reveal,reply_markup=reveal_keyboard(oc_id,stage),parse_mode='HTML')
        await c.answer()

    @dp.message(Command('play'))
    @dp.message(F.text=='🎮 Играть')
    async def play(m):
        await matches(m)

    async def matches(m):
        async with Session() as s:
            try: u=await active_user(s,m.from_user)
            except ValueError as e: await m.answer(str(e)); return
            caption='🏒 МАТЧИ\n\nВыберите режим:'
        await send_section_photo(m,PHOTO_GAME,caption,reply_markup=pve_keyboard())

    friendly_steps=set()
    @dp.callback_query(F.data=='friendly_id_start')
    async def friendly_id_prompt(c:CallbackQuery):
        friendly_steps.add(c.from_user.id)
        await c.message.answer('Введите Telegram ID соперника для товарищеского матча. Для отмены отправьте /cancelchallenge.')
        await c.answer()

    @dp.message(lambda m: m.from_user.id in friendly_steps and not (m.text or '').startswith('/'))
    async def friendly_id_submit(m:Message):
        raw_id=(m.text or '').strip()
        if not raw_id.isdigit(): await m.answer('ID должен состоять только из цифр. Попробуйте ещё раз или отправьте /cancelchallenge.'); return
        friendly_steps.discard(m.from_user.id)
        target=int(raw_id)
        async with Session() as s:
            try:
                u=await active_user(s,m.from_user); target_user=await s.get(User,target)
                if not target_user or target_user.is_banned: raise ValueError('Игрок не найден или заблокирован.')
                if target==u.id: raise ValueError('Нельзя вызвать самого себя.')
                rows=await lineup(s,u.id); cards=[x for _,x in rows]
                if len(rows)!=6 or {x.position for x in cards}!=set(POSITIONS): raise ValueError('Сначала соберите полный состав из шести разных позиций.')
                active=await s.scalar(select(Match.id).where(or_(Match.home_user_id==u.id,Match.away_user_id==u.id),Match.status.in_(['queued','searching','running','challenge'])))
                target_active=await s.scalar(select(Match.id).where(or_(Match.home_user_id==target,Match.away_user_id==target),Match.status.in_(['queued','searching','running','challenge'])))
                if active: raise ValueError('У вас уже есть активный матч.')
                if target_active: raise ValueError('У выбранного игрока уже есть активный матч.')
                from .game import team_power
                match=Match(mode='challenge',status='challenge',home_user_id=u.id,away_user_id=target,home_name=u.first_name or str(u.id),away_name=target_user.first_name or str(target),home_power=team_power(cards),home_goalie=next(x.ovr for x in cards if x.position=='G'))
                s.add(match); await s.commit(); await s.refresh(match)
                kb=InlineKeyboardBuilder(); kb.button(text='✅ Принять матч',callback_data=f'chacc:{match.id}'); kb.button(text='❌ Отклонить',callback_data=f'chdec:{match.id}'); kb.adjust(1)
                try: await m.bot.send_message(target,f'🏒 Товарищеский вызов от {u.first_name or u.id}. Матч #{match.id}, без ставки.',reply_markup=kb.as_markup())
                except Exception:
                    match.status='delivery_failed'; await s.commit(); await m.answer('Не удалось доставить вызов. Убедитесь, что соперник запускал бота.'); return
                await m.answer(f'Вызов отправлен игроку {target}. Ожидаем подтверждения.')
            except ValueError as e: await m.answer(str(e))

    @dp.callback_query(F.data=='pve_start')
    async def pve_from_menu(c:CallbackQuery):
        async with Session() as s:
            try:
                u=await active_user(s,c.from_user)
                league=league_for_rating(u.rating)
                match=await start_pve_match(s,u,league)
                await c.message.answer(
                    f'🤖 Матч против бота запущен! Соперник: {league}. '
                    f'Награда за победу — {match.reward} 🪙, за поражение — половина. '
                    f'ID матча #{match.id}. /match'
                )
            except ValueError as e:
                await c.answer(str(e),show_alert=True); return
        await c.answer()

    @dp.callback_query(F.data.startswith('pve_after_timeout:'))
    async def pve_after_timeout(c:CallbackQuery):
        try: mid=int(c.data.split(':',1)[1])
        except ValueError:
            await c.answer('Некорректный номер поиска.',show_alert=True); return
        async with Session() as s:
            mt=await s.scalar(select(Match).where(Match.id==mid,Match.home_user_id==c.from_user.id,Match.status=='searching').with_for_update())
            if not mt:
                await c.answer('Поиск уже завершён или отменён — соперник мог найтись.',show_alert=True); return
            mt.status='cancelled'
            await s.commit()
            try:
                u=await active_user(s,c.from_user)
                league=league_for_rating(u.rating)
                match=await start_pve_match(s,u,league)
            except ValueError as e:
                await c.answer(str(e),show_alert=True); return
        await c.message.edit_text('🔎 Поиск соперника отменён.')
        await c.message.answer(
            f'🤖 Матч против бота запущен! Соперник: {league}. '
            f'Награда за победу — {match.reward} 🪙, за поражение — половина. '
            f'ID матча #{match.id}. /match'
        )
        await c.answer('Поиск отменён, запускаю матч с ботом.')

    @dp.callback_query(F.data=='pvp_start')
    async def queue_from_menu(c:CallbackQuery):
        ranked=True
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
                        async with Session() as check:
                            current=await check.get(Match,match.id)
                            if not current or current.status!='searching': return
                        bot_kb=InlineKeyboardBuilder()
                        bot_kb.button(text='🤖 Играть с ботом',callback_data=f'pve_after_timeout:{match.id}')
                        try: await c.bot.send_message(c.from_user.id,'Не удалось найти соперника за 30 секунд. Можете продолжить поиск или сыграть с ботом:',reply_markup=bot_kb.as_markup())
                        except Exception: pass
                    asyncio.create_task(countdown())
            except ValueError as e: await c.answer(str(e),show_alert=True); return
        await c.answer()

        # Legacy ranked button now redirects to the unified regular match queue.
    @dp.callback_query(F.data=='ranked_start')
    async def ranked_queue_from_menu(c:CallbackQuery):
        await c.answer('Теперь все обычные матчи учитываются в рейтинге и турнире.')
        await queue_from_menu(c)

    @dp.callback_query(F.data.startswith('pve:'))
    async def legacy_pve_redirect(c:CallbackQuery):
        await c.answer('PvE-режим убран. Запускается обычный матч с рейтингом и турниром.')
        await queue_from_menu(c)

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
        if m.from_user.id in friendly_steps:
            friendly_steps.discard(m.from_user.id); await m.answer('Ввод ID отменён.'); return
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

    @dp.message(Command('startmatch'))
    @dp.message(F.text.casefold() == 'старт')
    async def start_match_now(m):
        async with Session() as s:
            mt=await s.scalar(select(Match).where(
                or_(Match.home_user_id==m.from_user.id,Match.away_user_id==m.from_user.id),
                Match.status.in_(['queued','running'])
            ).order_by(Match.id.desc()))
            if not mt:
                await m.answer('Нет ожидающего матча. Сначала запустите поиск соперника через /pvp.')
                return
            mid=mt.id
            status=mt.status
        if status=='queued':
            await prepare_match(mid,m.bot)
            await m.answer(f'🚨 Запрос на немедленный старт матча #{mid} отправлен. Трансляция начнётся сейчас.')
        else:
            await m.answer(f'🏒 Матч #{mid} уже запущен. Статус и счёт: /match')

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

    market_steps={}
    def market_keyboard():
        kb=InlineKeyboardBuilder()
        kb.button(text='🛍 Смотреть лоты',callback_data='market:current')
        kb.button(text='➕ Выставить карточку',callback_data='market:sell')
        kb.button(text='📦 Мои лоты',callback_data='market:mine')
        kb.button(text='⬅️ Главное меню',callback_data='player:home')
        kb.adjust(1); return kb.as_markup()

    @dp.message(F.text=='🏪 Рынок')
    @dp.message(Command('market'))
    async def market(m):
        await m.answer('🏪 РЫНОК КАРТОЧЕК\nВыберите действие:',reply_markup=market_keyboard())

    @dp.callback_query(F.data.startswith('market:'))
    async def market_menu_action(c:CallbackQuery):
        action=c.data.split(':',1)[1]
        async with Session() as s:
            try: await active_user(s,c.from_user)
            except ValueError as e: await c.answer(str(e),show_alert=True); return
            if action=='sell':
                cards=(await s.scalars(select(OwnedCard).where(OwnedCard.user_id==c.from_user.id,OwnedCard.slot=='',OwnedCard.locked.is_(False)).order_by(OwnedCard.id.desc()).limit(100))).all()
                kb=InlineKeyboardBuilder(); count=0
                for oc in cards:
                    card=await s.get(Card,oc.card_id)
                    if card:
                        kb.button(text=f'{card.player_name} · {card.ovr} OVR · {card.position}',callback_data=f'marketcard:{oc.id}'); count+=1
                kb.button(text='⬅️ Назад',callback_data='market:home'); kb.adjust(1)
                await c.message.answer('Выберите свободную карточку для продажи:' if count else 'Нет свободных карточек. Сначала снимите карточку с позиции в меню «Моя команда».',reply_markup=kb.as_markup())
            elif action=='current':
                listings=(await s.scalars(select(MarketListing).where(MarketListing.status=='active').order_by(desc(MarketListing.created_at)).limit(25))).all()
                lines=['🏪 АКТИВНЫЕ ЛОТЫ']; kb=InlineKeyboardBuilder(); count=0
                for listing in listings:
                    oc=await s.get(OwnedCard,listing.owned_card_id); card=await s.get(Card,oc.card_id) if oc else None
                    seller=await s.get(User,listing.seller_id)
                    if card:
                        lines.append(f'\n{card.player_name} · {card.ovr} OVR · {rarity_label(card.rarity)}\nПродавец: {seller.first_name or seller.id if seller else "игрок"}\nЦена: {listing.price} 🪙')
                        kb.button(text=f'🛒 Купить · {card.player_name} · {listing.price} 🪙',callback_data=f'marketbuy:{listing.id}'); count+=1
                if not count: lines.append('\nПока нет активных лотов.')
                kb.button(text='⬅️ Назад',callback_data='market:home'); kb.adjust(1)
                try: await c.message.edit_text('\n'.join(lines),reply_markup=kb.as_markup(),parse_mode='HTML')
                except Exception: await c.message.answer('\n'.join(lines),reply_markup=kb.as_markup(),parse_mode='HTML')
            elif action=='mine':
                listings=(await s.scalars(select(MarketListing).where(MarketListing.seller_id==c.from_user.id,MarketListing.status=='active').order_by(desc(MarketListing.created_at)))).all()
                kb=InlineKeyboardBuilder(); lines=['📦 МОИ ЛОТЫ']
                for listing in listings:
                    oc=await s.get(OwnedCard,listing.owned_card_id); card=await s.get(Card,oc.card_id) if oc else None
                    if card:
                        lines.append(f'\n{card.player_name} · {card.ovr} OVR · {listing.price} 🪙')
                        kb.button(text=f'❌ Снять · {card.player_name}',callback_data=f'marketdelist:{listing.id}')
                if not listings: lines.append('\nУ вас нет активных лотов.')
                kb.button(text='⬅️ Назад',callback_data='market:home'); kb.adjust(1)
                await c.message.answer('\n'.join(lines),reply_markup=kb.as_markup())
            elif action=='home':
                await c.message.answer('🏪 РЫНОК КАРТОЧЕК\nВыберите действие:',reply_markup=market_keyboard())
        await c.answer()

    @dp.callback_query(F.data.startswith('marketcard:'))
    async def market_card_selected(c:CallbackQuery):
        try: oid=int(c.data.split(':',1)[1])
        except ValueError: await c.answer('Карточка не найдена.',show_alert=True); return
        async with Session() as s:
            oc=await s.get(OwnedCard,oid); card=await s.get(Card,oc.card_id) if oc else None
            if not oc or oc.user_id!=c.from_user.id or oc.slot or oc.locked or not card:
                await c.answer('Эта карточка больше недоступна.',show_alert=True); return
            market_steps[c.from_user.id]={'owned_id':oid}
        await c.message.answer(f'Вы выбрали {card.player_name} · {card.ovr} OVR.\nВведите цену в монетах одним числом. Для отмены /cancelmarket.')
        await c.answer()

    @dp.message(lambda m: m.from_user.id in market_steps and not (m.text or '').startswith('/'))
    async def market_price_input(m:Message):
        try: price=int((m.text or '').strip())
        except ValueError: await m.answer('Введите цену целым числом или /cancelmarket для отмены.'); return
        state=market_steps.get(m.from_user.id,{})
        async with Session() as s:
            try:
                await active_user(s,m.from_user)
                listing=await create_market_listing(s,m.from_user.id,state['owned_id'],price)
                market_steps.pop(m.from_user.id,None)
                await m.answer(f'✅ Карточка выставлена на рынок за {price} 🪙.')
            except (ValueError,KeyError) as e: await m.answer(str(e))

    @dp.callback_query(F.data.startswith('marketbuy:'))
    async def market_buy_button(c:CallbackQuery):
        try: lid=int(c.data.split(':',1)[1])
        except ValueError: await c.answer('Лот не найден.',show_alert=True); return
        async with Session() as s:
            try:
                user=await active_user(s,c.from_user); oc=await buy_market_listing(s,user,lid); card=await s.get(Card,oc.card_id)
                await c.message.answer(f'✅ Куплено: {card.player_name} · {card.ovr} OVR. Карточка добавлена в коллекцию.')
            except ValueError as e: await c.answer(str(e),show_alert=True); return
        await c.answer('Покупка успешна.')

    @dp.callback_query(F.data.startswith('marketdelist:'))
    async def market_delist_button(c:CallbackQuery):
        lid=int(c.data.split(':',1)[1])
        async with Session() as s:
            listing=await s.scalar(select(MarketListing).where(MarketListing.id==lid,MarketListing.seller_id==c.from_user.id,MarketListing.status=='active').with_for_update())
            if not listing: await c.answer('Лот уже недоступен.',show_alert=True); return
            oc=await s.get(OwnedCard,listing.owned_card_id)
            if oc: oc.locked=False
            listing.status='cancelled'; await s.commit()
        await c.message.answer('Лот снят с рынка.'); await c.answer()

    @dp.message(Command('cancelmarket'))
    async def market_cancel(m:Message):
        market_steps.pop(m.from_user.id,None); await m.answer('Выставление карточки отменено.')

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

    @dp.message(Command('trades'))
    @dp.message(F.text=='🔁 Обмены')
    async def trade_menu(m):
        kb=InlineKeyboardBuilder(); kb.button(text='🤝 Предложить обмен',callback_data='trade:players')
        kb.button(text='📨 Мои входящие предложения',callback_data='trade:incoming')
        kb.button(text='📤 Мои исходящие предложения',callback_data='trade:outgoing')
        kb.button(text='⬅️ Главное меню',callback_data='player:home'); kb.adjust(1)
        await m.answer('🔁 ОБМЕНЫ КАРТОЧКАМИ\nВыбирайте игроков и карточки кнопками. Перед подтверждением обе карточки блокируются.',reply_markup=kb.as_markup())

    @dp.callback_query(F.data.startswith('trade:'))
    async def trade_menu_action(c:CallbackQuery):
        parts=c.data.split(':'); action=parts[1]
        async with Session() as s:
            try: await active_user(s,c.from_user)
            except ValueError as e: await c.answer(str(e),show_alert=True); return
            if action=='players':
                rows=(await s.scalars(select(User).where(User.id!=c.from_user.id,User.is_banned.is_(False)).order_by(desc(User.rating)).limit(30))).all()
                kb=InlineKeyboardBuilder()
                for u in rows: kb.button(text=f'{u.first_name or "Игрок"} · {u.rating} ⭐',callback_data=f'trade:target:{u.id}')
                kb.button(text='⬅️ Назад',callback_data='player:trades'); kb.adjust(1)
                await c.message.answer('Выберите игрока для обмена:',reply_markup=kb.as_markup())
            elif action=='target':
                target=int(parts[2])
                if not await s.get(User,target): await c.answer('Игрок не найден.',show_alert=True); return
                cards=(await s.scalars(select(OwnedCard).where(OwnedCard.user_id==c.from_user.id,OwnedCard.slot=='',OwnedCard.locked.is_(False)).order_by(OwnedCard.id.desc()).limit(80))).all()
                kb=InlineKeyboardBuilder(); count=0
                for oc in cards:
                    card=await s.get(Card,oc.card_id)
                    if card: kb.button(text=f'{card.player_name} · {card.ovr} · {rarity_label(card.rarity)}',callback_data=f'trade:give:{target}:{oc.id}'); count+=1
                kb.button(text='⬅️ Назад',callback_data='trade:players'); kb.adjust(1)
                await c.message.answer('Выберите карточку, которую отдаёте:' if count else 'Нет свободных карточек для обмена.',reply_markup=kb.as_markup())
            elif action=='give':
                target=int(parts[2]); give=int(parts[3])
                cards=(await s.scalars(select(OwnedCard).where(OwnedCard.user_id==target,OwnedCard.slot=='',OwnedCard.locked.is_(False)).order_by(OwnedCard.id.desc()).limit(80))).all()
                kb=InlineKeyboardBuilder(); count=0
                for oc in cards:
                    card=await s.get(Card,oc.card_id)
                    if card: kb.button(text=f'{card.player_name} · {card.ovr} · {rarity_label(card.rarity)}',callback_data=f'trade:send:{target}:{give}:{oc.id}'); count+=1
                kb.button(text='⬅️ Назад',callback_data=f'trade:target:{target}'); kb.adjust(1)
                await c.message.answer('Выберите карточку, которую хотите получить:' if count else 'У игрока нет свободных карточек для обмена.',reply_markup=kb.as_markup())
            elif action=='send':
                target,give,receive=map(int,parts[2:5])
                try:
                    offer=await create_trade(s,c.from_user.id,target,give,receive)
                    a=await s.get(OwnedCard,give); b=await s.get(OwnedCard,receive)
                    message=f'🔁 Предложение обмена #{offer.id}\nВам предлагают: {await owned_card_text(s,a)}\nВзамен вы отдаёте: {await owned_card_text(s,b)}'
                    await c.bot.send_message(target,message,reply_markup=trade_keyboard(offer.id))
                    await c.message.answer(f'Предложение #{offer.id} отправлено. Обе карточки заблокированы до ответа.')
                except Exception as e:
                    await c.answer(str(e),show_alert=True); return
            elif action=='cancel':
                oid=int(parts[2])
                offer=await s.scalar(select(TradeOffer).where(TradeOffer.id==oid).with_for_update())
                if not offer or offer.sender_id!=c.from_user.id or offer.status!='pending':
                    await c.answer('Исходящее предложение уже недоступно.',show_alert=True); return
                for owned_id in offer.give_card_ids+offer.receive_card_ids:
                    oc=await s.get(OwnedCard,owned_id)
                    if oc: oc.locked=False
                offer.status='cancelled'; await s.commit()
                try: await c.bot.send_message(offer.receiver_id,f'Предложение обмена #{offer.id} отменено отправителем.')
                except Exception: pass
                await c.message.answer(f'Предложение #{offer.id} отменено, карточки разблокированы.')
            elif action in ('incoming','outgoing'):
                field=TradeOffer.receiver_id if action=='incoming' else TradeOffer.sender_id
                rows=(await s.scalars(select(TradeOffer).where(field==c.from_user.id).order_by(TradeOffer.id.desc()).limit(15))).all()
                lines=['📨 ОБМЕНЫ' if action=='incoming' else '📤 ИСХОДЯЩИЕ ОБМЕНЫ']
                kb=InlineKeyboardBuilder()
                for offer in rows:
                    lines.append(f'#{offer.id} · игрок {offer.sender_id} → {offer.receiver_id} · {offer.status}')
                    if offer.status=='pending' and action=='incoming':
                        kb.button(text=f'✅ Принять #{offer.id}',callback_data=f'tradeyes:{offer.id}')
                        kb.button(text=f'❌ Отклонить #{offer.id}',callback_data=f'tradeno:{offer.id}')
                    if offer.status=='pending' and action=='outgoing':
                        kb.button(text=f'Отменить #{offer.id}',callback_data=f'trade:cancel:{offer.id}')
                kb.button(text='⬅️ Назад',callback_data='player:trades'); kb.adjust(1)
                await c.message.answer('\n'.join(lines),reply_markup=kb.as_markup())
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

    auction_steps={}
    def auction_menu_keyboard():
        kb=InlineKeyboardBuilder()
        kb.button(text='➕ Начать аукцион',callback_data='auctionmenu:start')
        kb.button(text='🔥 Текущие аукционы',callback_data='auctionmenu:current')
        kb.button(text='⏹ Завершить мой аукцион',callback_data='auctionmenu:end')
        kb.button(text='📚 История аукционов',callback_data='auctionmenu:history')
        kb.button(text='⬅️ Главное меню',callback_data='player:home')
        kb.adjust(1); return kb.as_markup()

    @dp.message(F.text=='🔨 Аукционы')
    @dp.message(Command('auctions'))
    async def auctions(m):
        await m.answer('🔨 АУКЦИОНЫ\nВыберите действие. Для выставления карточки она выбирается из вашей коллекции — вводить ID не нужно.',reply_markup=auction_menu_keyboard())

    @dp.callback_query(F.data.startswith('auctionmenu:'))
    async def auction_menu_action(c:CallbackQuery):
        action=c.data.split(':',1)[1]
        async with Session() as s:
            await active_user(s,c.from_user)
            if action=='start':
                cards=(await s.scalars(select(OwnedCard).where(OwnedCard.user_id==c.from_user.id,OwnedCard.locked.is_(False)).order_by(OwnedCard.id.desc()).limit(100))).all()
                kb=InlineKeyboardBuilder()
                for oc in cards:
                    card=await s.get(Card,oc.card_id)
                    if card: kb.button(text=f'{card.player_name} · {card.ovr} OVR · {card.position}'+(f' · в составе {oc.slot}' if oc.slot else ''),callback_data=f'auctioncard:{oc.id}')
                kb.button(text='⬅️ Назад',callback_data='auctionmenu:home'); kb.adjust(1)
                await c.message.answer('Шаг 1 из 2. Выберите карточку. Если она стоит в составе, при выставлении автоматически освободится её позиция.',reply_markup=kb.as_markup()) if cards else await c.message.answer('У вас пока нет карточек для аукциона. Откройте пак или получите карточку, затем вернитесь сюда.',reply_markup=auction_menu_keyboard())
            elif action=='current':
                rows=(await s.scalars(select(Auction).where(Auction.status=='active').order_by(Auction.ends_at).limit(20))).all()
                lines=['🔥 ТЕКУЩИЕ АУКЦИОНЫ']; kb=InlineKeyboardBuilder()
                for a in rows:
                    oc=await s.get(OwnedCard,a.owned_card_id); card=await s.get(Card,oc.card_id) if oc else None
                    if card:
                        lines.append(f'\n{card.player_name} · {card.ovr} OVR\nСтавка: {a.highest_bid or a.start_price} 🪙 · шаг {a.bid_step}\nДо: {a.ends_at:%d.%m %H:%M} МСК')
                        kb.button(text=f'💰 Сделать ставку · {card.player_name}',callback_data=f'auctionbid:{a.id}')
                if not rows: lines.append('\nПока нет активных аукционов.')
                kb.button(text='⬅️ Назад',callback_data='auctionmenu:home'); kb.adjust(1)
                await c.message.answer('\n'.join(lines),reply_markup=kb.as_markup())
            elif action=='end':
                rows=(await s.scalars(select(Auction).where(Auction.owner_id==c.from_user.id,Auction.status.in_(['submitted','active'])).order_by(Auction.created_at.desc()))).all()
                kb=InlineKeyboardBuilder(); lines=['⏹ ВАШИ АУКЦИОНЫ']
                for a in rows:
                    oc=await s.get(OwnedCard,a.owned_card_id); card=await s.get(Card,oc.card_id) if oc else None
                    if card:
                        lines.append(f'{card.player_name} · {card.ovr} OVR · {("на проверке" if a.status=="submitted" else "идёт")} ')
                        kb.button(text=f'Завершить · {card.player_name}',callback_data=f'auctionstop:{a.id}')
                if not rows: lines.append('У вас нет активных аукционов.')
                kb.button(text='⬅️ Назад',callback_data='auctionmenu:home'); kb.adjust(1)
                await c.message.answer('\n'.join(lines),reply_markup=kb.as_markup())
            elif action=='history':
                rows=(await s.scalars(select(Auction).where(Auction.owner_id==c.from_user.id,Auction.status.in_(['completed','cancelled','expired'])).order_by(Auction.created_at.desc()).limit(15))).all()
                lines=['📚 ВАША ИСТОРИЯ АУКЦИОНОВ']
                for a in rows:
                    oc=await s.get(OwnedCard,a.owned_card_id); card=await s.get(Card,oc.card_id) if oc else None
                    if card: lines.append(f'{card.player_name} · {card.ovr} OVR · {a.status} · итог {a.highest_bid} 🪙')
                if len(lines)==1: lines.append('Завершённых аукционов пока нет.')
                await c.message.answer('\n'.join(lines),reply_markup=auction_menu_keyboard())
            elif action=='home':
                await c.message.answer('🔨 АУКЦИОНЫ — выберите действие:',reply_markup=auction_menu_keyboard())
        await c.answer()

    @dp.callback_query(F.data.startswith('auctioncard:'))
    async def auction_card_selected(c:CallbackQuery):
        try: oid=int(c.data.split(':',1)[1])
        except ValueError: await c.answer('Карточка не найдена.',show_alert=True); return
        async with Session() as s:
            try:
                await active_user(s,c.from_user)
                a=await submit_auction_card(s,c.from_user.id,oid)
                oc=await s.get(OwnedCard,oid); card=await s.get(Card,oc.card_id)
                await c.message.answer(f'✅ {card.player_name} передан на проверку администрации. После одобрения аукцион появится в текущих.')
                for admin_id in ADMIN_IDS:
                    try: await c.bot.send_message(admin_id,f'Новая заявка на аукцион: {card.player_name} · {card.ovr} OVR. Откройте /admin → Аукционы.')
                    except Exception: pass
            except ValueError as e: await c.answer(str(e),show_alert=True); return
        await c.answer('Заявка отправлена.')

    @dp.callback_query(F.data.startswith('auctionbid:'))
    async def auction_bid_start(c:CallbackQuery):
        aid=int(c.data.split(':',1)[1])
        async with Session() as s:
            a=await s.get(Auction,aid)
            if not a or a.status!='active': await c.answer('Аукцион уже недоступен.',show_alert=True); return
            oc=await s.get(OwnedCard,a.owned_card_id); card=await s.get(Card,oc.card_id) if oc else None
            minimum=a.start_price if not a.highest_bid else a.highest_bid+a.bid_step
        auction_steps[c.from_user.id]={'step':'bid','auction_id':aid}
        await c.message.answer(f'Шаг 1 из 1. Введите ставку для {card.player_name if card else "карточки"} (минимум {minimum} 🪙).')
        await c.answer()

    @dp.message(lambda m: m.from_user.id in auction_steps and auction_steps[m.from_user.id].get('step')=='bid' and not (m.text or '').startswith('/'))
    async def auction_bid_amount(m:Message):
        state=auction_steps.get(m.from_user.id,{})
        try: amount=int((m.text or '').strip())
        except ValueError: await m.answer('Введите сумму целым числом. Для отмены отправьте /cancelauction.'); return
        async with Session() as s:
            try:
                u=await active_user(s,m.from_user); a=await place_bid(s,u,state['auction_id'],amount)
                auction_steps.pop(m.from_user.id,None)
                await m.answer(f'✅ Ставка {amount} 🪙 принята. Деньги зарезервированы.')
            except (ValueError,KeyError) as e: await m.answer(str(e))

    @dp.callback_query(F.data.startswith('auctionstop:'))
    async def auction_stop(c:CallbackQuery):
        aid=int(c.data.split(':',1)[1])
        async with Session() as s:
            a=await s.scalar(select(Auction).where(Auction.id==aid, Auction.owner_id==c.from_user.id).with_for_update())
            if not a or a.status not in ('submitted','active'):
                await c.answer('Этот аукцион уже нельзя завершить.',show_alert=True); return
            if a.winner_id and a.highest_bid:
                bidder=await s.scalar(select(User).where(User.id==a.winner_id).with_for_update())
                if bidder:
                    bidder.coins+=a.highest_bid
                    s.add(EconomyLedger(user_id=bidder.id,currency='coins',amount=a.highest_bid,reason='auction_bid_refund',reference_id=a.id))
            oc=await s.get(OwnedCard,a.owned_card_id)
            if oc: oc.locked=False
            a.status='cancelled'; await s.commit()
        await c.message.answer('⏹ Аукцион завершён. Карточка разблокирована, зарезервированная ставка возвращена.')
        await c.answer()

    @dp.message(Command('cancelauction'))
    async def cancel_auction_input(m:Message):
        auction_steps.pop(m.from_user.id,None); await m.answer('Действие с аукционом отменено.')

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

    def tournament_keyboard():
        kb=InlineKeyboardBuilder()
        kb.button(text='🏆 Таблица турнира',callback_data='tournament:table')
        kb.button(text='🏆 Играть рейтинговый матч',callback_data='pvp_start')
        kb.button(text='⬅️ Главное меню',callback_data='player:home')
        kb.adjust(1); return kb.as_markup()

    @dp.message(Command('tournament'))
    @dp.message(F.text=='⚔️ Турнир')
    async def tournament(m):
        async with Session() as s:
            t=await ensure_daily_tournament_participants(s)
            status='🟢 Турнир идёт' if t.status=='open' else '🔴 Турнир завершён'
            await m.answer(f'⚔️ {t.title or "ЕЖЕДНЕВНЫЙ ТУРНИР"} · {t.tournament_date}\n{status}\nВсе игроки бота участвуют автоматически — отдельная регистрация не нужна. Очки начисляются за сыгранные матчи. Победа — 2 очка, поражение в OT/буллитах — 1. Призы получают места 1–5.',reply_markup=tournament_keyboard())

    @dp.callback_query(F.data.startswith('tournament:'))
    async def tournament_action(c:CallbackQuery):
        action=c.data.split(':',1)[1]
        async with Session() as s:
            try: await active_user(s,c.from_user)
            except ValueError as e: await c.answer(str(e),show_alert=True); return
            if action=='table':
                t=await ensure_daily_tournament_participants(s)
                rows=(await s.scalars(select(TournamentEntry).where(TournamentEntry.tournament_id==t.id).order_by(desc(TournamentEntry.points),desc(TournamentEntry.wins),desc(TournamentEntry.goals),TournamentEntry.user_id).limit(10))).all()
                lines=[f'🏆 {t.title or "ЕЖЕДНЕВНЫЙ ТУРНИР"} · {t.tournament_date}',f'Статус: {"🟢 идёт" if t.status=="open" else "🔴 завершён"}','']
                defaults={1:(10000,20),2:(7000,15),3:(5000,10),4:(3000,7),5:(2000,5)}
                prizes={}
                for place,(coins,gems) in defaults.items():
                    cfg=await s.get(TournamentPrizeConfig,place)
                    prizes[place]=(cfg.coins,cfg.gems) if cfg else (coins,gems)
                for i,row in enumerate(rows,1):
                    user=await s.get(User,row.user_id)
                    lines.append(f'{i}. {user.first_name or "Игрок" if user else "Игрок"} — {row.points} очк. · {row.wins} побед · {row.goals} голов')
                if len(rows)==0: lines.append('Пока нет участников.')
                elif rows:
                    lead=rows[0]
                    lines.append(f'\n🥇 Первое место: {lead.points} очков, {lead.wins} побед и {lead.goals} голов. Сначала сравниваются очки, затем победы, затем голы.')
                lines.extend(['','🎁 ПРИЗЫ ТОП-5:'])
                for place,(coins,gems) in prizes.items(): lines.append(f'{place}. {coins} 🪙 + {gems} 💎')
                await c.message.answer('\n'.join(lines),reply_markup=tournament_keyboard())
        await c.answer()

    clan_steps={}
    clan_invite_steps=set()
    def clan_keyboard(is_member=False):
        kb=InlineKeyboardBuilder()
        if is_member:
            kb.button(text='⚔️ Недельная битва',callback_data='clanmenu:battle')
            kb.button(text='👥 Участники клана',callback_data='clanmenu:members')
            kb.button(text='🏆 Рейтинг кланов',callback_data='clanmenu:top')
            kb.button(text='👥 Пригласить игрока',callback_data='clanmenu:invite')
            kb.button(text='🚪 Покинуть клан',callback_data='clanmenu:leave')
        else:
            kb.button(text='➕ Создать клан',callback_data='clanmenu:create')
            kb.button(text='🔎 Найти клан и вступить',callback_data='clanmenu:join')
        kb.button(text='⬅️ Главное меню',callback_data='player:home')
        kb.adjust(1); return kb.as_markup()

    @dp.message(Command('clan'))
    @dp.message(F.text=='🛡 Кланы')
    async def clan(m):
        parts=(m.text or '').split(maxsplit=2)
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
            if len(parts)>=3 and parts[1].lower()=='kick':
                try:
                    await kick_clan_member(s,m.from_user.id,int(parts[2]))
                    await m.answer('Участник исключён из клана.')
                except (ValueError,TypeError) as e: await m.answer(str(e))
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
                await m.answer(f'🛡 {cl.name}\nРоль: {mem.role}\nУчастников: {count}\nОчки битв: {cl.points}\nРейтинг: {cl.rating}',reply_markup=clan_keyboard(True))
            else:
                top=(await s.scalars(select(Clan).order_by(desc(Clan.points)).limit(10))).all()
                await m.answer('🛡 КЛАНЫ\nСоздайте свой клан или выберите существующий в списке.',reply_markup=clan_keyboard(False))

    async def weekly_clan_battle_text(session):
        today=now().date()
        iso=today.isocalendar()
        week_key=f'{iso.year}-W{iso.week:02d}'
        tournament=await session.scalar(select(WeeklyClanTournament).where(WeeklyClanTournament.week_key==week_key))
        if not tournament:
            tournament=WeeklyClanTournament(week_key=week_key,status='open')
            session.add(tournament); await session.commit(); await session.refresh(tournament)
        week_start=date.fromisocalendar(iso.year,iso.week,1)
        week_end=date.fromisocalendar(iso.year,iso.week,7)
        rows=(await session.scalars(select(WeeklyClanTournamentEntry).where(WeeklyClanTournamentEntry.tournament_id==tournament.id).order_by(desc(WeeklyClanTournamentEntry.points),desc(WeeklyClanTournamentEntry.wins),desc(WeeklyClanTournamentEntry.goals),WeeklyClanTournamentEntry.clan_id).limit(10))).all()
        prizes={1:'30 000 🪙 + 60 💎 + премиум-пак каждому участнику',2:'20 000 🪙 + 40 💎 каждому',3:'15 000 🪙 + 30 💎 каждому',4:'10 000 🪙 + 20 💎 каждому',5:'5 000 🪙 + 10 💎 каждому'}
        lines=[f'⚔️ НЕДЕЛЬНАЯ КЛАНОВАЯ БИТВА · {week_key}',f'Период: {week_start.strftime("%d.%m")} — {week_end.strftime("%d.%m")}', 'В зачёт идут только рейтинговые матчи. Каждый рейтинговый матч учитывается и в личном турнире, и в недельной битве клана.', '', '🏆 ПРИЗЫ ТОП-5 (каждому участнику клана на момент завершения):']
        for place,reward in prizes.items(): lines.append(f'{place}. {reward}')
        lines.append('')
        lines.append('📊 ТЕКУЩИЙ РЕЙТИНГ')
        for place,row in enumerate(rows,1):
            clan=await session.get(Clan,row.clan_id)
            if clan: lines.append(f'{place}. 🛡 {clan.name} — {row.points} очк. · {row.wins} побед · {row.goals} голов')
        if rows:
            lead=rows[0]
            lines.append(f'\n🥇 Лидер недели: {lead.points} очков, {lead.wins} побед и {lead.goals} голов. При равенстве очков учитываются победы, затем голы.')
        if not rows: lines.append('Пока нет результатов. Играйте рейтинговые матчи — очки автоматически пойдут в зачёт вашего клана.')
        return '\n'.join(lines)

    def clan_battle_keyboard():
        kb=InlineKeyboardBuilder()
        kb.button(text='🏆 Найти рейтинговый матч',callback_data='pvp_start')
        kb.button(text='⬅️ Назад к клану',callback_data='player:clans')
        kb.adjust(1)
        return kb.as_markup()

    @dp.callback_query(F.data.startswith('clanmenu:'))
    async def clan_menu_action(c:CallbackQuery):
        action=c.data.split(':',1)[1]
        async with Session() as s:
            try: await active_user(s,c.from_user)
            except ValueError as e: await c.answer(str(e),show_alert=True); return
            member=await s.scalar(select(ClanMember).where(ClanMember.user_id==c.from_user.id))
            if action=='create':
                if member: await c.answer('Вы уже состоите в клане.',show_alert=True); return
                clan_steps[c.from_user.id]='create'
                await c.message.answer('Введите название нового клана одним сообщением. Для отмены /cancelclan.')
            elif action=='join':
                if member: await c.answer('Вы уже состоите в клане.',show_alert=True); return
                clans=(await s.scalars(select(Clan).order_by(desc(Clan.points)).limit(20))).all()
                kb=InlineKeyboardBuilder()
                for cl in clans: kb.button(text=f'🛡 {cl.name} · {cl.points} очк.',callback_data=f'clanjoin:{cl.id}')
                kb.button(text='⬅️ Назад',callback_data='player:clans'); kb.adjust(1)
                await c.message.answer('Выберите клан для вступления:',reply_markup=kb.as_markup())
            elif action=='leave':
                try: await leave_clan(s,c.from_user.id); await c.message.answer('Вы покинули клан.',reply_markup=clan_keyboard(False))
                except ValueError as e: await c.answer(str(e),show_alert=True); return
            elif action=='members':
                if not member: await c.answer('Сначала вступите в клан.',show_alert=True); return
                clan_obj=await s.get(Clan,member.clan_id)
                rows=(await s.execute(select(ClanMember,User).join(User,User.id==ClanMember.user_id).where(ClanMember.clan_id==member.clan_id))).all()
                role_names={'owner':'👑 Владелец','admin':'🛡 Администратор','member':'Игрок'}
                rows.sort(key=lambda pair:(0 if pair[0].role=='owner' else 1 if pair[0].role=='admin' else 2,-(pair[1].rating or 0)))
                lines=[f'👥 СОСТАВ КЛАНА «{clan_obj.name}» · {len(rows)}/25','']
                for idx,(mem,user) in enumerate(rows,1):
                    display=(user.first_name or user.username or f'Игрок {user.id}').replace('<','').replace('>','')
                    lines.append(f'{idx}. {role_names.get(mem.role,"Игрок")} · {display} · ⭐ {user.rating or 0}')
                lines.append('')
                lines.append('Управление: /clan role ID admin|member (только владелец); /clan kick ID (владелец или администратор).')
                await c.message.answer('\n'.join(lines))
            elif action=='top':
                clans=(await s.scalars(select(Clan).order_by(desc(Clan.points),desc(Clan.rating),Clan.created_at).limit(15))).all()
                lines=['🏆 РЕЙТИНГ КЛАНОВ','Сортировка по суммарным очкам клановых матчей и рейтингу.','']
                for idx,cl in enumerate(clans,1):
                    count=await s.scalar(select(func.count(ClanMember.id)).where(ClanMember.clan_id==cl.id))
                    lines.append(f'{idx}. 🛡 {cl.name} · {cl.points or 0} очк. · рейтинг {cl.rating or 0} · {count}/25')
                if not clans: lines.append('Пока нет созданных кланов.')
                await c.message.answer('\n'.join(lines))
            elif action=='battle':
                if not member: await c.answer('Сначала вступите в клан.',show_alert=True); return
                await c.message.answer(await weekly_clan_battle_text(s),reply_markup=clan_battle_keyboard())
            elif action=='invite':
                if not member: await c.answer('Сначала вступите в клан.',show_alert=True); return
                if member.role not in ('owner','admin'): await c.answer('Приглашать могут владелец и администратор клана.',show_alert=True); return
                clan_invite_steps.add(c.from_user.id)
                await c.message.answer('Введите Telegram ID игрока, которого хотите пригласить. Для отмены — /cancelclan.')
        await c.answer()

    @dp.callback_query(F.data.startswith('clanjoin:'))
    async def clan_join_button(c:CallbackQuery):
        cid=int(c.data.split(':',1)[1])
        async with Session() as s:
            try:
                await active_user(s,c.from_user); cl=await join_clan(s,c.from_user.id,cid)
                await c.message.answer(f'✅ Вы вступили в клан «{cl.name}».',reply_markup=clan_keyboard(True))
            except ValueError as e: await c.answer(str(e),show_alert=True); return
        await c.answer()

    @dp.callback_query(F.data.startswith('claninvite:'))
    async def clan_invite_button(c:CallbackQuery):
        target=int(c.data.split(':',1)[1])
        async with Session() as s:
            try:
                await active_user(s,c.from_user); inv=await create_clan_invite(s,c.from_user.id,target)
                kb=InlineKeyboardBuilder(); kb.button(text='✅ Принять',callback_data=f'clanyes:{inv.id}'); kb.button(text='❌ Отклонить',callback_data=f'clanno:{inv.id}'); kb.adjust(2)
                await c.bot.send_message(target,'🛡 Вас приглашают в клан.',reply_markup=kb.as_markup())
                await c.message.answer('Приглашение отправлено.')
            except Exception as e: await c.answer(str(e),show_alert=True); return
        await c.answer()

    @dp.message(Command('cancelclan'))
    async def clan_cancel(m:Message):
        clan_steps.pop(m.from_user.id,None)
        clan_invite_steps.discard(m.from_user.id)
        await m.answer('Действие с кланом отменено.')

    @dp.message(lambda m: m.from_user.id in clan_invite_steps and not (m.text or '').startswith('/'))
    async def clan_invite_input(m:Message):
        raw=(m.text or '').strip()
        try:
            target=int(raw)
            if target<=0: raise ValueError
        except ValueError:
            await m.answer('Введите корректный числовой Telegram ID. Для отмены — /cancelclan.')
            return
        async with Session() as s:
            try:
                inv=await create_clan_invite(s,m.from_user.id,target)
                kb=InlineKeyboardBuilder()
                kb.button(text='✅ Принять',callback_data=f'clanyes:{inv.id}')
                kb.button(text='❌ Отклонить',callback_data=f'clanno:{inv.id}')
                kb.adjust(2)
                try:
                    await m.bot.send_message(target,'🛡 Вас приглашают в клан. Приглашение доступно ниже:',reply_markup=kb.as_markup())
                except Exception:
                    inv.status='delivery_failed'
                    await s.commit()
                    await m.answer('Не удалось доставить приглашение. Игрок должен хотя бы один раз запустить бота (/start).')
                    clan_invite_steps.discard(m.from_user.id)
                    return
                clan_invite_steps.discard(m.from_user.id)
                await m.answer(f'✅ Приглашение отправлено игроку с ID {target}.')
            except ValueError as e:
                await m.answer(str(e))

    @dp.message(lambda m: m.from_user.id in clan_steps and not (m.text or '').startswith('/'))
    async def clan_create_input(m:Message):
        name=(m.text or '').strip()
        if not name or len(name)>32: await m.answer('Название должно содержать от 1 до 32 символов.'); return
        async with Session() as s:
            try:
                cl=await create_clan(s,m.from_user.id,name)
                clan_steps.pop(m.from_user.id,None)
                await m.answer(f'✅ Клан «{cl.name}» создан.',reply_markup=clan_keyboard(True))
            except ValueError as e: await m.answer(str(e))

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
            try:
                u=await active_user(s,m.from_user)
                member=await s.scalar(select(ClanMember).where(ClanMember.user_id==u.id))
                if not member: await m.answer('Сначала вступите в клан.'); return
                await m.answer(await weekly_clan_battle_text(s),reply_markup=clan_battle_keyboard())
            except ValueError as e: await m.answer(str(e))

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
            page=1
            if len(parts)>2 and parts[1].lower()=='page':
                try: page=max(1,min(10,int(parts[2])))
                except ValueError: page=1
            start_level=(page-1)*10+1; end_level=min(page*10,100)
            kb=InlineKeyboardBuilder()
            claimed_free=set(bp.claimed_free or []); claimed_premium=set(bp.claimed_premium or [])
            lines=[f'🎟 BATTLE PASS · сезон {bp.season}',f'Уровень: {level}/100 · XP: {bp.xp} · до следующего уровня: {0 if level>=100 else 100-bp.xp%100} XP',f'Страница {page}/10 · уровни {start_level}–{end_level}','']
            for tier in range(start_level,end_level+1):
                free_reward='🏒 Семён Кошелев · 95 OVR' if tier==100 else ( 'Пак «Обычный»' if tier%10==0 else f'{250+tier*25} 🪙')
                premium_reward='🏒 Семён Кошелев · 99 OVR' if tier==100 else ('🎴 Бонусная карточка' if tier%10==0 else ('📦 Пак «Премиум»' if tier%5==0 else f'{2+tier//10} 💎'))
                free_state='получено' if tier in claimed_free else ('доступно' if tier<=level else 'закрыто')
                premium_state='получено' if tier in claimed_premium else ('доступно' if tier<=level and bp.premium else 'закрыто')
                lines.append(f'Ур. {tier}: Free — {free_reward} ({free_state}); Premium — {premium_reward} ({premium_state})')
                if tier<=level and tier not in claimed_free: kb.button(text=f'🎁 Free · ур. {tier}',callback_data=f'bp:one:free:{tier}')
                if tier<=level and bp.premium and tier not in claimed_premium: kb.button(text=f'💎 Premium · ур. {tier}',callback_data=f'bp:one:premium:{tier}')
            if page>1: kb.button(text='⬅️ Страница '+str(page-1),callback_data=f'bp:page:{page-1}')
            if page<10: kb.button(text='➡️ Страница '+str(page+1),callback_data=f'bp:page:{page+1}')
            kb.button(text='📋 Задания',callback_data='bp:quests')
            if not bp.premium: kb.button(text=f'💎 Активировать Premium · {BATTLEPASS_PREMIUM_PRICE}',callback_data='bp:premium')
            free_ready_count=sum(1 for tier in range(1,level+1) if tier not in claimed_free)
            premium_ready_count=sum(1 for tier in range(1,level+1) if tier not in claimed_premium)
            if free_ready_count>2: kb.button(text='🎁 Забрать все Free-награды',callback_data='bp:claim:free')
            if bp.premium and premium_ready_count>2: kb.button(text='💎 Забрать все Premium-награды',callback_data='bp:claim:premium')
            kb.button(text='⬅️ Главное меню',callback_data='player:home'); kb.adjust(1)
            await m.answer('\n'.join(lines)+'\n\nЗа матч начисляется 100 XP, за выполненное задание — 50 XP.',reply_markup=kb.as_markup())

    @dp.callback_query(F.data.startswith('bp:'))
    async def battlepass_button(c:CallbackQuery):
        _,action,*rest=c.data.split(':')
        async with Session() as s:
            try:
                u=await active_user(s,c.from_user)
                if action=='quests':
                    from types import SimpleNamespace
                    async def edit_quests(text, **kwargs):
                        try: return await c.message.edit_text(text, **kwargs)
                        except Exception: return await c.message.answer(text, **kwargs)
                    await quest(SimpleNamespace(from_user=c.from_user,text='/quest',answer=edit_quests))
                elif action=='page':
                    from types import SimpleNamespace
                    async def edit_page(text, **kwargs):
                        try: return await c.message.edit_text(text, **kwargs)
                        except Exception: return await c.message.answer(text, **kwargs)
                    await battlepass(SimpleNamespace(from_user=c.from_user,text=f'/bp page {rest[0]}',answer=edit_page))
                elif action=='one':
                    premium=rest[0]=='premium'; tier=int(rest[1])
                    reward=await claim_bp_reward(s,u.id,tier,premium)
                    await c.message.answer(f'🎁 Награда уровня {tier}: {reward}.')
                elif action=='premium':
                    u=await s.scalar(select(User).where(User.id==u.id).with_for_update())
                    bp=await s.scalar(select(BattlePassProgress).where(BattlePassProgress.user_id==u.id,BattlePassProgress.season==season_key()).with_for_update())
                    if not bp: bp=BattlePassProgress(user_id=u.id,season=season_key()); s.add(bp)
                    if bp.premium: await c.answer('Premium уже активирован.',show_alert=True); return
                    if u.gems<BATTLEPASS_PREMIUM_PRICE: await c.answer(f'Нужно {BATTLEPASS_PREMIUM_PRICE} 💎.',show_alert=True); return
                    u.gems-=BATTLEPASS_PREMIUM_PRICE; bp.premium=True; await s.commit()
                    await c.message.answer('✅ Premium-линия активирована.')
                elif action=='claim':
                    premium=rest[0]=='premium'
                    bp=await s.scalar(select(BattlePassProgress).where(BattlePassProgress.user_id==u.id,BattlePassProgress.season==season_key()))
                    current=min(100,bp.xp//100) if bp else 0
                    claimed=set(bp.claimed_premium if premium and bp else (bp.claimed_free if bp else []))
                    count=0; rewards=[]
                    for level in range(1,current+1):
                        if level in claimed: continue
                        try:
                            reward=await claim_bp_reward(s,u.id,level,premium)
                            rewards.append(f'{level}: {reward}'); count+=1
                        except ValueError: continue
                    await c.message.answer('🎁 Получено наград: '+str(count)+('.\n'+'\n'.join(rewards[:10]) if rewards else '. Новых наград нет.'))
            except ValueError as e: await c.answer(str(e),show_alert=True); return
        await c.answer()

    @dp.message(Command('quest'))
    @dp.message(F.text=='📋 Задания')
    async def quest(m):
        parts=(m.text or '').split()
        async with Session() as s:
            try: await active_user(s,m.from_user)
            except ValueError as e: await m.answer(str(e)); return
            if len(parts)==3 and parts[1]=='claim':
                try:
                    coins,gems,xp=await claim_quest(s,m.from_user.id,parts[2]); await m.answer(f'✅ Награда: {coins} 🪙 · {gems} 💎 · {xp} XP Battle Pass.')
                except ValueError as e: await m.answer(str(e))
                return
            today=now().date().isoformat(); week=f'{now().date().isocalendar().year}-W{now().date().isocalendar().week}'; month=now().strftime('%Y-%m'); season=season_key()
            groups=[
                ('📅 ЕЖЕДНЕВНЫЕ',[( 'play3',today,3,'Сыграть 3 матча'),('win1',today,1,'Выиграть 1 матч'),('score5goals',today,5,'Забить 5 голов')]),
                ('🗓 ЕЖЕНЕДЕЛЬНЫЕ',[( 'play20',week,20,'Сыграть 20 матчей'),('win10',week,10,'Выиграть 10 матчей')]),
                ('🗓 ЕЖЕМЕСЯЧНЫЕ',[( 'play60month',month,60,'Сыграть 60 матчей за месяц'),('win20month',month,20,'Выиграть 20 матчей за месяц')]),
                ('🏆 СЕЗОННЫЕ',[( 'play100',season,100,'Сыграть 100 матчей'),('win50',season,50,'Одержать 50 побед'),('reach_khl',season,1,'Достичь КХЛ'),('open50packs',season,50,'Открыть 50 паков')])
            ]
            rewards={'play3':(500,1,100),'win1':(750,2,100),'score5goals':(1000,3,100),'play20':(2500,4,100),'win10':(3500,5,100),'play60month':(6000,8,100),'win20month':(8000,10,100),'play100':(10000,12,100),'win50':(15000,18,100),'reach_khl':(5000,8,100),'open50packs':(10000,15,100)}
            lines=['📋 ЗАДАНИЯ']
            kb=InlineKeyboardBuilder()
            for heading,items in groups:
                lines.extend(['', '━━━━━━━━━━━━', heading])
                for key,period,need,title in items:
                    q=await s.scalar(select(QuestProgress).where(QuestProgress.user_id==m.from_user.id,QuestProgress.quest_key==key,QuestProgress.period_key==period))
                    value=q.progress if q else 0; status='получено' if q and q.claimed else ('доступно' if value>=need else '')
                    coins,gems,xp=rewards[key]
                    lines.append(f'• {title}: {value}/{need} · 🎁 {coins} 🪙 + {gems} 💎 + {xp} XP'+(f' · {status}' if status else ''))
                    if value>=need and not (q and q.claimed): kb.button(text=f'🎁 Забрать награду · {title}',callback_data=f'questclaim:{key}')
            kb.button(text='🎟 К Battle Pass',callback_data='player:battlepass'); kb.adjust(1)
            await m.answer('\n'.join(lines),reply_markup=kb.as_markup())

    @dp.callback_query(F.data.startswith('questclaim:'))
    async def quest_claim_button(c:CallbackQuery):
        key=c.data.split(':',1)[1]
        async with Session() as s:
            try:
                await active_user(s,c.from_user); coins,gems,xp=await claim_quest(s,c.from_user.id,key)
                await c.message.answer(f'✅ Награда: {coins} 🪙 · {gems} 💎 · {xp} XP Battle Pass.')
            except ValueError as e: await c.answer(str(e),show_alert=True); return
        await c.answer()

    @dp.message(Command('daily'))
    @dp.message(F.text=='🎁 Ежедневная награда')
    async def daily(m):
        async with Session() as s:
            try: u=await active_user(s,m.from_user)
            except ValueError as e: await m.answer(str(e)); return
            reward=await claim_daily(s,u)
            if reward is None: await m.answer('🎁 Вы уже забрали ежедневную награду. Возвращайтесь завтра!')
            else:
                coins,gems,bonus=reward
                text=f'🎉 ЕЖЕДНЕВНЫЙ БОНУС\n\n🪙 +{coins} монет'
                if gems: text+=f'\n💎 +{gems} алмаз(а)'
                if bonus: text+='\n✨ Бонус дня! Дополнительная награда выпала случайно.'
                text+='\n\nЗаглядывайте завтра — награда снова будет другой!'
                await m.answer(text)

    @dp.message(F.text=='🏆 Рейтинг')
    @dp.message(Command('leaderboard'))
    async def leaderboard(m):
        async with Session() as s:
            rows=(await s.scalars(select(User).order_by(desc(User.rating),desc(User.wins)).limit(15))).all()
            lines=['🏆 <b>РЕЙТИНГ СЕЗОНА</b>','━━━━━━━━━━━━━━','Топ игроков по рейтингу и победам:']
            if rows:
                medals={1:'🥇',2:'🥈',3:'🥉'}
                lines.extend(f'{medals.get(i, f"{i}.")} <b>{u.first_name or u.id}</b> — ⭐ {u.rating} · {league_for_rating(u.rating)}' for i,u in enumerate(rows,1))
            else:
                lines.append('Пока нет игроков в рейтинге. Сыграй первый матч!')
            await send_section_photo(m,PHOTO_ARENA,'\n'.join(lines),parse_mode='HTML')

    @dp.message(Command('season'))
    async def season(m):
        async with Session() as s:
            ss=await ensure_season(s); await m.answer(f'🗓 Сезон {ss.season_key}\nНачало МСК: {ss.starts_at:%d.%m.%Y %H:%M}\nКонец МСК: {ss.ends_at:%d.%m.%Y %H:%M}\nПосле сезона рейтинг сбрасывается до 0, статистика сохраняется.')


    @dp.message(Command('myid'))
    async def myid(m):
        await m.answer(f'Ваш Telegram ID: {m.from_user.id}')

    admin_steps={}
    admin_auction_steps={}
    def admin_keyboard():
        kb=InlineKeyboardBuilder()
        for label,data in [('👥 Игроки','adm:players:0'),('📊 Статистика','adm:stats'),('🏒 История матчей','adm:match_history'),('🔁 История обменов','adm:trade_history'),('💰 Выдать монеты','adm:wiz:givecoins'),('💎 Выдать алмазы','adm:wiz:givegems'),('🃏 Выдать карточку','adm:wiz:givecard'),('🎟 Выдать Premium Pass','adm:wiz:givepremium'),('⭐ Выдать уровни Pass','adm:wiz:givebpxp'),('🚫 Блокировка','adm:wiz:ban'),('🏆 Управление турниром','adm:tournaments'),('🔨 Аукционы','adm:auctions'),('🛠 Карточки и паки','adm:cards'),('🎟 Промокоды','adm:promos'),('📩 Сообщения игроков','adm:messages'),('📖 Все команды','adm:help')]:
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
                    last=await team_session.scalar(select(Match).where(or_(Match.home_user_id==u.id,Match.away_user_id==u.id),Match.status=='completed').order_by(Match.id.desc()).limit(1))
                avg_text=f'{float(avg_team):.1f} OVR' if avg_team is not None else 'состав не собран'
                display_name=u.first_name or (f'@{u.username}' if u.username else 'Без имени')
                from html import escape
                display_name=escape(display_name)
                if last:
                    own=last.home_score if last.home_user_id==u.id else last.away_score
                    opp=last.away_score if last.home_user_id==u.id else last.home_score
                    last_text=f'#{last.id} · {own}:{opp}'
                else: last_text='ещё не играл'
                lines.append(f'\n👤 {display_name}\n🆔 ID: <code>{u.id}</code>\n⭐ {u.rating} · {league_for_rating(u.rating)}\n🏒 Состав: {avg_text}\nПоследний матч: {last_text}'+(' · 🚫 Бан' if u.is_banned else ''))
            kb=InlineKeyboardBuilder()
            if page>0: kb.button(text='⬅️ Назад',callback_data=f'adm:players:{page-1}')
            if (page+1)*15<total: kb.button(text='Вперёд ➡️',callback_data=f'adm:players:{page+1}')
            kb.button(text='⬅️ В админ-меню',callback_data='adm:home'); kb.adjust(2)
            await c.message.answer('\n'.join(lines),reply_markup=kb.as_markup(),parse_mode='HTML')
        elif action[1]=='match_history':
            await c.message.answer('🏒 История матчей администратора\nВведите /matchinfo НОМЕР_МАТЧА, например /matchinfo 3.',reply_markup=admin_back_keyboard())
        elif action[1]=='trade_history':
            await c.message.answer('🔁 История обменов администратора\nВведите /tradeinfo НОМЕР_ОБМЕНА, например /tradeinfo 3.',reply_markup=admin_back_keyboard())
        elif action[1]=='wiz':
            kind=action[2]
            admin_steps[c.from_user.id]={'kind':kind,'step':'user'}
            prompt={'givecoins':'💰 Введите Telegram ID игрока. Баланс монет будет изменён на указанную сумму.','givegems':'💎 Введите Telegram ID игрока. Баланс алмазов будет изменён на указанную сумму.','givecard':'🃏 Введите числовой Telegram ID игрока, которому нужно выдать карточку.','givepremium':'🎟 Введите Telegram ID игрока, которому нужно выдать Premium Pass.','givebpxp':'⭐ Введите Telegram ID игрока, которому нужно начислить уровни Battle Pass.','ban':'🚫 Введите Telegram ID игрока, которого нужно заблокировать.'}.get(kind,'Неизвестное действие.')
            await c.message.answer(prompt+'\n'+('Шаг 1 из 1.' if kind=='ban' else 'Шаг 1 из 4.' if kind=='givecard' else 'Шаг 1 из 2.')+' Отмена: /canceladmin')
        elif action[1]=='tournaments':
            async with Session() as s:
                rows=(await s.scalars(select(DailyTournament).order_by(DailyTournament.tournament_date.desc()).limit(10))).all()
            lines=['🏆 УПРАВЛЕНИЕ ЕЖЕДНЕВНЫМИ ТУРНИРАМИ','Выберите существующий турнир для просмотра или управления:']
            kb=InlineKeyboardBuilder()
            for t in rows:
                lines.append(f"\\n{t.title or 'Ежедневный турнир'} · {t.tournament_date} · {'🟢 открыт' if t.status=='open' else '🔴 завершён'} · ID {t.id}")
                kb.button(text=f"{'🟢' if t.status=='open' else '🔴'} {t.tournament_date}",callback_data=f'adm:tournament_view:{t.id}')
            kb.button(text='🎁 Награды за места',callback_data='adm:tournament_prizes')
            kb.button(text='➕ Управлять по дате',callback_data='adm:tournament_date')
            kb.button(text='⬅️ Назад',callback_data='adm:home'); kb.adjust(1)
            await c.message.answer(''.join(lines),reply_markup=kb.as_markup())
        elif action[1]=='tournament_prizes':
            defaults={1:(10000,20),2:(7000,15),3:(5000,10),4:(3000,7),5:(2000,5)}
            async with Session() as s:
                lines=['🎁 НАГРАДЫ ЕЖЕДНЕВНОГО ТУРНИРА']
                kb=InlineKeyboardBuilder()
                for place,(coins,gems) in defaults.items():
                    cfg=await s.get(TournamentPrizeConfig,place)
                    coins,gems=(cfg.coins,cfg.gems) if cfg else (coins,gems)
                    lines.append(f'{place} место — {coins} 🪙 + {gems} 💎')
                    kb.button(text=f'✏️ Изменить {place} место',callback_data=f'adm:tournament_prize:{place}')
            kb.button(text='⬅️ К турнирам',callback_data='adm:tournaments'); kb.adjust(1)
            await c.message.answer('\n'.join(lines)+'\n\nВыберите место для изменения награды:',reply_markup=kb.as_markup())
        elif action[1]=='tournament_prize':
            place=int(action[2])
            if place<1 or place>5:
                await c.answer('Можно настроить места с 1-го по 5-е.',show_alert=True); return
            admin_steps[c.from_user.id]={'kind':'tournament_prize','step':'amounts','place':place}
            await c.message.answer(f'Введите награду за {place} место в формате: МОНЕТЫ АЛМАЗЫ. Например: 12000 25. Отмена: /canceladmin')
        elif action[1]=='tournament_date':
            admin_steps[c.from_user.id]={'kind':'tournament','step':'date'}
            await c.message.answer('🏆 Введите дату существующего или нового ежедневного турнира в формате ГГГГ-ММ-ДД.\\nШаг 1 из 2. Затем выберете: начать или завершить. Отмена: /canceladmin')
        elif action[1]=='tournament_view':
            tid=int(action[2])
            async with Session() as s:
                t=await s.get(DailyTournament,tid)
                count=await s.scalar(select(func.count(TournamentEntry.id)).where(TournamentEntry.tournament_id==tid)) if t else 0
            if not t:
                await c.answer('Турнир не найден.',show_alert=True); return
            kb=InlineKeyboardBuilder()
            if t.status=='open': kb.button(text='🔴 Завершить турнир',callback_data=f'adm:tournament_finish:{tid}')
            else:
                kb.button(text='🟢 Открыть турнир',callback_data=f'adm:tournament_start:{tid}')
                if not t.rewarded_at: kb.button(text='🎁 Начислить награды',callback_data=f'adm:tournament_reward:{tid}')
            kb.button(text='✏️ Изменить название',callback_data=f'adm:tournament_rename:{tid}')
            kb.button(text='🗑 Удалить турнир',callback_data=f'adm:tournament_delete:{tid}')
            kb.button(text='⬅️ К списку',callback_data='adm:tournaments'); kb.adjust(1)
            await c.message.answer(f'🏆 {t.title or "Ежедневный турнир"}\\nДата: {t.tournament_date}\\nСтатус: {"открыт" if t.status=="open" else "завершён"}\\nУчастников: {count}\\nID: {tid}',reply_markup=kb.as_markup())
        elif action[1]=='tournament_rename':
            tid=int(action[2])
            async with Session() as s:
                t=await s.get(DailyTournament,tid)
            if not t:
                await c.answer('Турнир не найден.',show_alert=True); return
            admin_steps[c.from_user.id]={'kind':'tournament_rename','step':'title','tournament_id':tid}
            await c.message.answer(f'Введите новое название турнира (до 100 символов). Сейчас: {t.title or "Ежедневный турнир"}. Отмена: /canceladmin')
        elif action[1]=='tournament_delete':
            tid=int(action[2])
            async with Session() as s:
                t=await s.get(DailyTournament,tid)
                if not t:
                    await c.answer('Турнир не найден.',show_alert=True); return
                count=await s.scalar(select(func.count(TournamentEntry.id)).where(TournamentEntry.tournament_id==tid)) or 0
            kb=InlineKeyboardBuilder()
            kb.button(text='⚠️ Да, удалить',callback_data=f'adm:tournament_delete_apply:{tid}')
            kb.button(text='❌ Отмена',callback_data=f'adm:tournament_view:{tid}'); kb.adjust(1)
            await c.message.answer(f'Удалить турнир «{t.title or "Ежедневный турнир"}» от {t.tournament_date}? Будут удалены сведения о турнире и {count} записей участников. Уже начисленные награды с балансов не списываются.',reply_markup=kb.as_markup())
        elif action[1]=='tournament_delete_apply':
            tid=int(action[2])
            async with Session() as s:
                t=await s.get(DailyTournament,tid)
                if not t:
                    await c.answer('Турнир уже удалён.',show_alert=True); return
                await s.execute(TournamentEntry.__table__.delete().where(TournamentEntry.tournament_id==tid))
                await audit(s,c.from_user.id,'tournament_deleted',tid,f'{t.tournament_date}:{t.title}')
                await s.delete(t)
                await s.commit()
            await c.message.answer('🗑 Турнир и связанные записи удалены. Ранее начисленные награды не изменены.',reply_markup=admin_back_keyboard())
        elif action[1] in ('tournament_start','tournament_finish','tournament_reward'):
            tid=int(action[2]); mode='open' if action[1]=='tournament_start' else 'closed'
            async with Session() as s:
                t=await s.get(DailyTournament,tid)
                if not t:
                    await c.answer('Турнир не найден.',show_alert=True); return
                if t.status==mode and not (mode=='closed' and action[1]=='tournament_reward' and not t.rewarded_at):
                    await c.answer('Турнир уже в этом статусе.',show_alert=True); return
                admin_steps[c.from_user.id]={'kind':'tournament_confirm','step':'confirm','tournament_id':tid,'mode':mode}
            kb=InlineKeyboardBuilder()
            kb.button(text='✅ Подтвердить',callback_data=f'adm:tournament_apply:{tid}:{mode}')
            kb.button(text='❌ Отмена',callback_data='adm:tournaments'); kb.adjust(1)
            await c.message.answer(f"Подтвердите {'открытие' if mode=='open' else 'завершение'} турнира {t.tournament_date}.",reply_markup=kb.as_markup())
        elif action[1]=='tournament_apply':
            tid=int(action[2]); mode=action[3]
            async with Session() as s:
                t=await s.get(DailyTournament,tid)
                if not t:
                    await c.answer('Турнир не найден.',show_alert=True); return
                t.status=mode
                notices=[]
                if mode=='open':
                    users=(await s.scalars(select(User.id).where(User.is_banned.is_(False)))).all()
                    existing=set((await s.scalars(select(TournamentEntry.user_id).where(TournamentEntry.tournament_id==tid))).all())
                    for uid in users:
                        if uid not in existing: s.add(TournamentEntry(tournament_id=tid,user_id=uid))
                elif mode=='closed' and not t.rewarded_at:
                    rows=(await s.scalars(select(TournamentEntry).where(TournamentEntry.tournament_id==tid).order_by(desc(TournamentEntry.points),desc(TournamentEntry.wins),desc(TournamentEntry.goals),TournamentEntry.user_id))).all()
                    defaults={1:(10000,20),2:(7000,15),3:(5000,10),4:(3000,7),5:(2000,5)}
                    for place,entry in enumerate(rows,1):
                        user=await s.get(User,entry.user_id)
                        if user and not user.is_banned:
                            coins,gems=(0,0)
                            if place<=5:
                                cfg=await s.get(TournamentPrizeConfig,place)
                                coins,gems=(cfg.coins,cfg.gems) if cfg else defaults[place]
                                user.coins+=coins; user.gems+=gems
                            notices.append((user.id,place,coins,gems))
                    t.rewarded_at=now()
                await audit(s,c.from_user.id,'tournament_status',tid,mode)
                await s.commit()
            admin_steps.pop(c.from_user.id,None)
            for uid,place,coins,gems in notices:
                try:
                    await c.bot.send_message(uid,f'🏁 Турнир {t.tournament_date} завершён!\n\n🎉 Ваше место: {place}.\n🎁 Ваши награды: {coins} 🪙 и {gems} 💎.'+( '\nНаграды начислены на баланс.' if coins or gems else '\nВ этот раз вы не попали в призовую пятёрку. Удачи в следующем турнире!'))
                except Exception: pass
            await c.message.answer(f"✅ Турнир {t.tournament_date} {'открыт' if mode=='open' else 'завершён'}. Награды начислены: {len(notices)} игрокам.",reply_markup=admin_back_keyboard())
        elif action[1]=='auctions':
            kb=InlineKeyboardBuilder()
            kb.button(text='📋 Заявки на аукцион',callback_data='adm:auction_queue')
            kb.button(text='❌ Отменить аукцион',callback_data='adm:auction_cancel')
            kb.button(text='⬅️ Назад',callback_data='adm:home'); kb.adjust(1)
            await c.message.answer('🔨 УПРАВЛЕНИЕ АУКЦИОНАМИ\nЗдесь можно просматривать заявки, запускать и отменять аукционы. Выберите действие:',reply_markup=kb.as_markup())
        elif action[1]=='auction_queue':
            async with Session() as s:
                rows=(await s.scalars(select(Auction).where(Auction.status=='submitted').order_by(Auction.created_at))).all()
                lines=['📋 ЗАЯВКИ НА АУКЦИОН']; kb=InlineKeyboardBuilder()
                for a in rows:
                    oc=await s.get(OwnedCard,a.owned_card_id); card=await s.get(Card,oc.card_id) if oc else None
                    if card:
                        lines.append(f'\n{card.player_name} · {card.ovr} OVR · заявка от игрока')
                        kb.button(text=f'▶️ Запустить · {card.player_name}',callback_data=f'adm:auction_start:{a.id}')
                if len(lines)==1: lines.append('\nНовых заявок нет.')
                kb.button(text='⬅️ В админ-меню',callback_data='adm:home'); kb.adjust(1)
            await c.message.answer('\n'.join(lines),reply_markup=kb.as_markup())
        elif action[1]=='auction_start':
            if len(action)<3:
                await c.answer('Заявка не выбрана.',show_alert=True); return
            aid=int(action[2])
            async with Session() as s:
                a=await s.get(Auction,aid); oc=await s.get(OwnedCard,a.owned_card_id) if a else None
                card=await s.get(Card,oc.card_id) if oc else None
                if not a or a.status!='submitted' or not card:
                    await c.answer('Заявка уже недоступна.',show_alert=True); return
            admin_auction_steps[c.from_user.id]={'auction_id':aid,'step':'start'}
            await c.message.answer(f'Запускаем аукцион: {card.player_name} · {card.ovr} OVR.\nШаг 1 из 5. Введите начальную цену в монетах.')
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
                lines.append(f'\n🎟 {p.code} — {"🟢 активен" if p.active else "🔴 выключен"}\nАктиваций: {p.uses_count}'+(f'/{p.max_uses}' if p.max_uses else ' (без лимита)')+f'\nНаграда: {p.reward_coins} 🪙 · {p.reward_gems} 💎 · {p.reward_xp} XP · Premium: {"да" if p.reward_premium else "нет"}')
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
            await c.message.answer('🛡 ПАНЕЛЬ АДМИНИСТРАТОРА\nВыберите нужный раздел:',reply_markup=admin_keyboard())
        await c.answer()

    @dp.callback_query(F.data.startswith('admcard:'))
    async def admin_card_picker(c:CallbackQuery):
        if not admin(c.from_user.id):
            await c.answer('Нет доступа.',show_alert=True); return
        state=admin_steps.get(c.from_user.id)
        if not state or state.get('kind')!='givecard':
            await c.answer('Начните выдачу карточки заново из админ-меню.',show_alert=True); return
        parts=c.data.split(':')
        action=parts[1] if len(parts)>1 else ''
        if action=='cancel':
            admin_steps.pop(c.from_user.id,None); await c.message.answer('Выдача карточки отменена.',reply_markup=admin_keyboard()); await c.answer(); return
        league_map={'KHL':'КХЛ','NHL':'НХЛ'}
        if action=='league':
            code=parts[2] if len(parts)>2 else ''
            if code not in league_map: await c.answer('Лига не найдена.',show_alert=True); return
            league=league_map[code]
            async with Session() as s:
                clubs=(await s.scalars(select(Card.club).where(Card.active.is_(True),Card.source!='demo',Card.league==league,Card.club!='—').distinct().order_by(Card.club))).all()
            if not clubs: await c.answer(f'В каталоге пока нет активных карточек лиги {league}.',show_alert=True); return
            state.update(step='team',league=league,club='')
            kb=InlineKeyboardBuilder()
            for idx,club in enumerate(clubs): kb.button(text=club,callback_data=f'admcard:team:{code}:{idx}')
            kb.button(text='⬅️ К выбору лиги',callback_data='admcard:back:league'); kb.button(text='❌ Отмена',callback_data='admcard:cancel'); kb.adjust(1)
            await c.message.answer(f'Шаг 3 из 4. Лига: {league}. Выберите команду:',reply_markup=kb.as_markup())
        elif action=='back' and len(parts)>2 and parts[2]=='league':
            state['step']='league'
            kb=InlineKeyboardBuilder(); kb.button(text='🏒 КХЛ',callback_data='admcard:league:KHL'); kb.button(text='🇺🇸 НХЛ',callback_data='admcard:league:NHL'); kb.button(text='❌ Отмена',callback_data='admcard:cancel'); kb.adjust(1)
            await c.message.answer('Выберите лигу карточки:',reply_markup=kb.as_markup())
        elif action=='team':
            code=parts[2] if len(parts)>2 else ''; idx=int(parts[3]) if len(parts)>3 else -1
            if code not in league_map or state.get('league')!=league_map[code]: await c.answer('Сначала выберите лигу.',show_alert=True); return
            async with Session() as s:
                clubs=(await s.scalars(select(Card.club).where(Card.active.is_(True),Card.source!='demo',Card.league==state['league'],Card.club!='—').distinct().order_by(Card.club))).all()
                if idx<0 or idx>=len(clubs): await c.answer('Команда не найдена.',show_alert=True); return
                club=clubs[idx]
                cards=(await s.scalars(select(Card).where(Card.active.is_(True),Card.source!='demo',Card.league==state['league'],Card.club==club).order_by(Card.player_name,Card.ovr.desc(),Card.id.desc()))).all()
            state.update(step='player',club=club)
            kb=InlineKeyboardBuilder()
            for card in cards:
                kb.button(text=f'{card.player_name} · {card.position} · {card.ovr} OVR',callback_data=f'admcard:player:{card.id}')
            kb.button(text='⬅️ К командам',callback_data=f'admcard:league:{code}'); kb.button(text='❌ Отмена',callback_data='admcard:cancel'); kb.adjust(1)
            await c.message.answer(f'Шаг 4 из 4. {state["league"]} · {club}. Выберите игрока:',reply_markup=kb.as_markup())
        elif action=='player':
            try: cid=int(parts[2])
            except (ValueError,IndexError): await c.answer('Игрок не найден.',show_alert=True); return
            async with Session() as s:
                target=await s.get(User,state.get('uid'))
                card=await s.get(Card,cid)
                if not target or not card or not card.active or card.source=='demo' or card.league!=state.get('league') or card.club!=state.get('club'):
                    await c.answer('Игрок или карточка больше недоступны.',show_alert=True); return
                s.add(OwnedCard(user_id=target.id,card_id=card.id,slot=''))
                await audit(s,c.from_user.id,'givecard',target.id,str(card.id)); await s.commit()
                target_id=target.id; player_name=card.player_name; card_id=card.id; ovr=card.ovr
            admin_steps.pop(c.from_user.id,None)
            try: await c.bot.send_message(target_id,f'🃏 Вам выдана карточка: {player_name} · {ovr} OVR.')
            except Exception: pass
            await c.message.answer(f'✅ Карточка {player_name} · {ovr} OVR (шаблон #{card_id}) выдана игроку {target_id}.',reply_markup=admin_keyboard())
        await c.answer()

    @dp.message(Command('canceladmin'))
    async def cancel_admin_wizard(m):
        if not admin(m.from_user.id): return
        admin_steps.pop(m.from_user.id,None)
        admin_auction_steps.pop(m.from_user.id,None)
        await m.answer('❎ Пошаговый ввод отменён.',reply_markup=admin_keyboard())

    @dp.message(lambda m: m.from_user.id in admin_steps)
    async def admin_wizard_input(m:Message):
        state=admin_steps.get(m.from_user.id)
        if not state or not admin(m.from_user.id) or not m.text: return
        kind=state['kind']; step=state['step']
        try:
            if kind=='tournament_prize' and step=='amounts':
                parts=m.text.replace(',',' ').split()
                if len(parts)!=2:
                    await m.answer('Формат: МОНЕТЫ АЛМАЗЫ, например 12000 25.'); return
                coins,gems=map(int,parts)
                if coins<0 or gems<0 or coins>100000000 or gems>1000000: raise ValueError()
                place=state['place']
                async with Session() as s:
                    cfg=await s.get(TournamentPrizeConfig,place)
                    if cfg: cfg.coins=coins; cfg.gems=gems
                    else: s.add(TournamentPrizeConfig(place=place,coins=coins,gems=gems))
                    await audit(s,m.from_user.id,'tournament_prize_update',place,f'{coins}:{gems}')
                    await s.commit()
                admin_steps.pop(m.from_user.id,None)
                await m.answer(f'✅ Награда за {place} место обновлена: {coins} 🪙 + {gems} 💎',reply_markup=admin_keyboard())
                return
            if kind=='tournament_rename' and step=='title':
                title=m.text.strip()
                if not title or len(title)>100:
                    await m.answer('Название должно содержать от 1 до 100 символов.'); return
                tid=state['tournament_id']
                async with Session() as s:
                    t=await s.get(DailyTournament,tid)
                    if not t:
                        await m.answer('Турнир не найден.'); admin_steps.pop(m.from_user.id,None); return
                    t.title=title
                    await audit(s,m.from_user.id,'tournament_renamed',tid,title)
                    await s.commit()
                admin_steps.pop(m.from_user.id,None)
                await m.answer(f'✅ Название турнира изменено: «{title}».',reply_markup=admin_keyboard())
                return
            if kind=='tournament' and step=='date':
                raw=m.text.strip()
                try:
                    parsed=date.fromisoformat(raw)
                    if parsed.isoformat()!=raw: raise ValueError()
                except ValueError:
                    await m.answer('Введите корректную дату в формате ГГГГ-ММ-ДД.'); return
                async with Session() as s:
                    t=await s.scalar(select(DailyTournament).where(DailyTournament.tournament_date==raw))
                    if not t:
                        t=DailyTournament(tournament_date=raw,status='closed'); s.add(t); await s.commit(); await s.refresh(t)
                    tid=t.id
                admin_steps.pop(m.from_user.id,None)
                kb=InlineKeyboardBuilder()
                kb.button(text='🟢 Начать / открыть',callback_data=f'adm:tournament_start:{tid}')
                kb.button(text='🔴 Завершить / закрыть',callback_data=f'adm:tournament_finish:{tid}')
                kb.button(text='❌ Отмена',callback_data='adm:tournaments'); kb.adjust(1)
                await m.answer(f'Шаг 2 из 2. Турнир {raw} найден/создан. Выберите действие:',reply_markup=kb.as_markup())
                return
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
                    await m.answer('Шаг 2 из 6. Сколько монет выдавать? 0 — без монет.')
                elif step=='coins':
                    value=int(m.text.strip())
                    if value<0: raise ValueError()
                    data['coins']=value; state['step']='gems'
                    await m.answer('Шаг 3 из 6. Сколько алмазов выдавать? 0 — без алмазов.')
                elif step=='gems':
                    value=int(m.text.strip())
                    if value<0: raise ValueError()
                    data['gems']=value; state['step']='xp'
                    await m.answer('Шаг 4 из 6. Сколько XP Battle Pass начислить? 0 — без XP.')
                elif step=='xp':
                    value=int(m.text.strip())
                    if value<0 or value>100000: raise ValueError()
                    data['xp']=value; state['step']='premium'
                    await m.answer('Шаг 5 из 6. Подарить Premium Battle Pass? Ответьте да или нет.')
                elif step=='premium':
                    value=m.text.strip().lower()
                    if value not in ('да','нет','yes','no'): await m.answer('Ответьте «да» или «нет».'); return
                    data['premium']=value in ('да','yes'); state['step']='limit'
                    await m.answer('Шаг 6 из 6. Укажите максимальное число активаций. 0 — без ограничений.')
                elif step=='limit':
                    limit=int(m.text.strip())
                    if limit<0: raise ValueError()
                    if not any((data.get('coins',0),data.get('gems',0),data.get('xp',0),data.get('premium',False))):
                        await m.answer('Укажите хотя бы одну награду: монеты, алмазы, XP или Premium.'); return
                    async with Session() as s:
                        s.add(PromoCode(code=data['code'],reward_coins=data['coins'],reward_gems=data['gems'],
                            reward_xp=data['xp'],reward_premium=data['premium'],max_uses=limit,uses_count=0,active=True))
                        await audit(s,m.from_user.id,'promo_create',0,f'{data["code"]}:{data["coins"]}:{data["gems"]}:{data["xp"]}:{data["premium"]}:{limit}')
                        await s.commit()
                    admin_steps.pop(m.from_user.id,None)
                    await m.answer(f'✅ Промокод {data["code"]} создан!\nНаграда: {data["coins"]} 🪙 · {data["gems"]} 💎 · {data["xp"]} XP · Premium: {"да" if data["premium"] else "нет"}\nЛимит: {limit if limit else "без ограничений"}',reply_markup=admin_keyboard())
                return
            if step=='user':
                try:
                    uid=int(m.text.strip())
                except ValueError:
                    await m.answer('Введите числовой Telegram ID без имени и фамилии.'); return
                async with Session() as s:
                    if not await s.get(User,uid):
                        await m.answer('Игрок с таким Telegram ID не найден.'); return
                state['uid']=uid
                if kind in ('givecoins','givegems'):
                    state['step']='amount'
                    await m.answer('Шаг 2 из 2. Введите количество монет или алмазов. Например: 500 начислит 500, а -200 спишет 200.')
                elif kind=='givecard':
                    state['step']='league'
                    kb=InlineKeyboardBuilder(); kb.button(text='🏒 КХЛ',callback_data='admcard:league:KHL'); kb.button(text='🇺🇸 НХЛ',callback_data='admcard:league:NHL'); kb.button(text='❌ Отмена',callback_data='admcard:cancel'); kb.adjust(1)
                    await m.answer('Шаг 2 из 4. Выберите лигу карточки:',reply_markup=kb.as_markup())
                elif kind=='givepremium':
                    async with Session() as s:
                        bp=await s.scalar(select(BattlePassProgress).where(BattlePassProgress.user_id==uid,BattlePassProgress.season==season_key()).with_for_update())
                        if not bp: bp=BattlePassProgress(user_id=uid,season=season_key(),xp=0,premium=False,claimed_free=[],claimed_premium=[]); s.add(bp)
                        bp.premium=True; await audit(s,m.from_user.id,'givepremium',uid,'admin grant'); await s.commit()
                    admin_steps.pop(m.from_user.id,None)
                    try: await m.bot.send_message(uid,'🎟 Администрация выдала вам Premium Battle Pass!')
                    except Exception: pass
                    await m.answer(f'✅ Premium Pass выдан игроку {uid}.',reply_markup=admin_keyboard())
                elif kind=='givebpxp':
                    state['step']='amount'
                    await m.answer('Шаг 2 из 2. Сколько уровней Battle Pass начислить? 1 уровень = 100 XP.')
                elif kind=='ban':
                    async with Session() as s:
                        target=await s.get(User,uid); target.is_banned=True
                        await audit(s,m.from_user.id,'ban',uid); await s.commit()
                    admin_steps.pop(m.from_user.id,None)
                    await m.answer(f'🚫 Игрок {uid} заблокирован.',reply_markup=admin_keyboard())
            elif step=='amount':
                amount=int(m.text.strip())
                if amount==0: raise ValueError()
                if kind=='givebpxp':
                    if amount<0 or amount>100: raise ValueError()
                    async with Session() as s:
                        bp=await s.scalar(select(BattlePassProgress).where(BattlePassProgress.user_id==state['uid'],BattlePassProgress.season==season_key()).with_for_update())
                        if not bp: bp=BattlePassProgress(user_id=state['uid'],season=season_key(),xp=0,premium=False,claimed_free=[],claimed_premium=[]); s.add(bp)
                        xp=amount*100; bp.xp=min(10000,bp.xp+xp)
                        await audit(s,m.from_user.id,'bpgrant',state['uid'],f'{amount} levels / {xp} XP'); await s.commit()
                    admin_steps.pop(m.from_user.id,None)
                    try: await m.bot.send_message(state['uid'],f'⭐ Администрация начислила вам {amount} уровней Battle Pass ({xp} XP).')
                    except Exception: pass
                    await m.answer(f'✅ Начислено {amount} уровней Battle Pass ({xp} XP) игроку {state["uid"]}.',reply_markup=admin_keyboard())
                    return
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

    @dp.message(F.text=='🎟 Промокоды')
    async def promo_section(m:Message):
        await m.answer('🎟 ПРОМОКОДЫ\nЧтобы активировать код, отправьте команду:\n/promo ВАШ_КОД')

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
            if promo.reward_xp or promo.reward_premium:
                bp=await s.scalar(select(BattlePassProgress).where(BattlePassProgress.user_id==u.id,BattlePassProgress.season==season_key()).with_for_update())
                if not bp:
                    bp=BattlePassProgress(user_id=u.id,season=season_key(),xp=0,premium=False,claimed_free=[],claimed_premium=[]); s.add(bp)
                bp.xp=min(10000,bp.xp+promo.reward_xp)
                if promo.reward_premium: bp.premium=True
            s.add(PromoRedemption(promo_code=code,user_id=u.id,coins_awarded=promo.reward_coins,gems_awarded=promo.reward_gems,
                xp_awarded=promo.reward_xp,premium_awarded=promo.reward_premium))
            await s.commit()
        await m.answer(f'🎉 Промокод {code} активирован!\nПолучено: {promo.reward_coins} 🪙 · {promo.reward_gems} 💎 · {promo.reward_xp} XP · Premium: {"да" if promo.reward_premium else "нет"}')

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

    @dp.message(Command('banned'))
    async def banned_list(m):
        if not admin(m.from_user.id): return
        async with Session() as s:
            users=(await s.scalars(select(User).where(User.is_banned.is_(True)).order_by(User.id))).all()
        if not users:
            await m.answer('🚫 Заблокированных игроков нет.')
            return
        kb=InlineKeyboardBuilder()
        lines=['🚫 ЗАБЛОКИРОВАННЫЕ ИГРОКИ:']
        for u in users:
            name=' '.join(x for x in (u.first_name,u.last_name) if x).strip() or u.username or 'Игрок'
            lines.append(f'{name} · ID {u.id}')
            kb.button(text=f'🔓 Разбанить {name} · {u.id}',callback_data=f'admin_unban:{u.id}')
        kb.adjust(1)
        await m.answer('\n'.join(lines),reply_markup=kb.as_markup())

    @dp.callback_query(F.data.startswith('admin_unban:'))
    async def admin_unban_button(c:CallbackQuery):
        if not admin(c.from_user.id):
            await c.answer('Нет доступа.',show_alert=True); return
        try: uid=int(c.data.split(':',1)[1])
        except ValueError:
            await c.answer('Некорректный ID.',show_alert=True); return
        async with Session() as s:
            u=await s.get(User,uid)
            if not u:
                await c.answer('Игрок не найден.',show_alert=True); return
            if not u.is_banned:
                await c.answer('Игрок уже разблокирован.',show_alert=True); return
            u.is_banned=False
            await audit(s,c.from_user.id,'unban',uid)
            await s.commit()
        await c.message.answer(f'🔓 Игрок {uid} разблокирован.')
        await c.answer('Разбан выполнен.')

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

    @dp.message(Command('matchinfo'))
    async def admin_match_info(m:Message):
        if not admin(m.from_user.id): return
        try: mid=int(m.text.split()[1])
        except: await m.answer('Формат: /matchinfo НОМЕР_МАТЧА'); return
        async with Session() as s:
            match=await s.get(Match,mid)
            if not match: await m.answer('Матч с таким номером не найден.'); return
            def shots(side): return sum(1 for e in (match.events or []) if e.get('type')=='shot' and e.get('side')==side)
            hg=len(match.home_goals or []); ag=len(match.away_goals or [])
            text=(f'🏒 <b>МАТЧ #{match.id}</b>\n<b>Режим:</b> {match.mode} · <b>Статус:</b> {match.status}\n'
                  f'<b>{match.home_name}</b> (ID <code>{match.home_user_id}</code>) — <b>{match.away_name}</b> (ID <code>{match.away_user_id}</code>)\n'
                  f'<b>Счёт: {match.home_score}:{match.away_score}</b> · голы: <b>{hg}:{ag}</b>\n'
                  f'<b>Броски:</b> {shots("home")+hg}:{shots("away")+ag}\n'
                  f'<b>Рейтинг:</b> {match.rating_change} · <b>Создан:</b> {format_msk(match.created_at)} МСК · <b>Завершён:</b> {format_msk(match.finished_at,naive_is_utc=False)} МСК')
            kb=InlineKeyboardBuilder(); kb.button(text='🗑 Удалить данные матча',callback_data=f'adm_match_delete_confirm:{match.id}')
            await m.answer(text,parse_mode='HTML',reply_markup=kb.as_markup())

    @dp.callback_query(F.data.startswith('adm_match_delete_confirm:'))
    async def admin_match_delete_confirm(c:CallbackQuery):
        if not admin(c.from_user.id): await c.answer('Нет доступа.',show_alert=True); return
        try: mid=int(c.data.split(':')[1])
        except ValueError: await c.answer('Некорректный номер матча.',show_alert=True); return
        async with Session() as s:
            match=await s.get(Match,mid)
            if not match: await c.answer('Матч уже отсутствует.',show_alert=True); return
            if match.status in ('queued','searching','running','challenge'):
                await c.answer('Нельзя удалять активный матч.',show_alert=True); return
        kb=InlineKeyboardBuilder()
        kb.button(text='⚠️ Да, удалить матч и трансляцию',callback_data=f'adm_match_delete:{mid}')
        kb.button(text='Отмена',callback_data='adm_match_delete_cancel')
        kb.adjust(1)
        await c.message.answer(f'Подтвердите удаление данных матча #{mid}. Запись и события трансляции будут удалены из базы.',reply_markup=kb.as_markup())
        await c.answer()

    @dp.callback_query(F.data.startswith('adm_match_delete:'))
    async def admin_match_delete(c:CallbackQuery):
        if not admin(c.from_user.id): await c.answer('Нет доступа.',show_alert=True); return
        try: mid=int(c.data.split(':')[1])
        except ValueError: await c.answer('Некорректный номер матча.',show_alert=True); return
        async with Session() as s:
            match=await s.get(Match,mid)
            if not match: await c.answer('Матч уже удалён.',show_alert=True); return
            if match.status in ('queued','searching','running','challenge'):
                await c.answer('Нельзя удалять активный матч.',show_alert=True); return
            await s.delete(match)
            await audit(s,c.from_user.id,'admin_match_delete',mid,'Удалена запись матча и события')
            await s.commit()
        await c.message.answer(f'🗑 Данные матча #{mid} удалены.')
        await c.answer('Удалено.')

    @dp.callback_query(F.data=='adm_match_delete_cancel')
    async def admin_match_delete_cancel(c:CallbackQuery):
        if not admin(c.from_user.id): await c.answer('Нет доступа.',show_alert=True); return
        await c.message.answer('Удаление отменено.')
        await c.answer()

    @dp.message(Command('tradeinfo'))
    async def admin_trade_info(m:Message):
        if not admin(m.from_user.id): return
        try: oid=int(m.text.split()[1])
        except: await m.answer('Формат: /tradeinfo НОМЕР_ОБМЕНА'); return
        async with Session() as s:
            offer=await s.get(TradeOffer,oid)
            if not offer: await m.answer('Обмен с таким номером не найден.'); return
            async def describe(ids):
                result=[]
                for owned_id in ids:
                    oc=await s.get(OwnedCard,owned_id)
                    card=await s.get(Card,oc.card_id) if oc else None
                    result.append(f'{card.player_name} · {card.ovr} OVR · владелец {oc.user_id}' if card and oc else f'Карточка #{owned_id}: отсутствует')
                return '; '.join(result) or '—'
            text=(f'🔁 ОБМЕН #{offer.id}\nСтатус: {offer.status}\n'
                  f'Отправитель: {offer.sender_id}\nПолучатель: {offer.receiver_id}\n'
                  f'Отдаёт: {await describe(offer.give_card_ids)}\nПолучает: {await describe(offer.receive_card_ids)}\n'
                  f'Создан: {format_msk(offer.created_at)} МСК')
            await m.answer(text)

    @dp.message(Command('stats'))
    async def stats(m):
        if not admin(m.from_user.id): return
        async with Session() as s:
            users=await s.scalar(select(func.count(User.id))); cards=await s.scalar(select(func.count(Card.id)))
            matches=await s.scalar(select(func.count(Match.id))); listings=await s.scalar(select(func.count(MarketListing.id)).where(MarketListing.status=='active'))
            await m.answer(f'📊 Статистика\nИгроки: {users}\nШаблоны карточек: {cards}\nМатчи: {matches}\nАктивные лоты: {listings}')

    @dp.message(lambda m: m.from_user.id in admin_auction_steps)
    async def admin_auction_wizard(m:Message):
        if not admin(m.from_user.id): admin_auction_steps.pop(m.from_user.id,None); return
        state=admin_auction_steps.get(m.from_user.id)
        prompts={'step':'Шаг 2 из 5. Введите шаг повышения ставки.','hours':'Шаг 3 из 5. На сколько часов открыть аукцион?','minprice':'Шаг 4 из 5. Введите минимальную цену продажи.','maxbid':'Шаг 5 из 5. Введите максимальную ставку (лимит).'}
        try: value=int((m.text or '').strip())
        except ValueError: await m.answer('Введите целое число. Для отмены нажмите /canceladmin.'); return
        if value<=0: await m.answer('Значение должно быть больше нуля. Повторите ввод.'); return
        if state['step']=='start':
            state['start']=value; state['step']='step'; await m.answer(prompts['step'])
        elif state['step']=='step':
            state['bid_step']=value; state['step']='hours'; await m.answer(prompts['hours'])
        elif state['step']=='hours':
            if value>720: await m.answer('Срок не может превышать 720 часов.'); return
            state['hours']=value; state['step']='minprice'; await m.answer(prompts['minprice'])
        elif state['step']=='minprice':
            state['min_price']=value; state['step']='maxbid'; await m.answer(prompts['maxbid'])
        elif state['step']=='maxbid':
            if value<state['start'] or value<state['min_price']:
                await m.answer('Максимальная ставка не может быть ниже начальной или минимальной цены. Введите ещё раз.'); return
            from datetime import timedelta
            async with Session() as s:
                a=await s.scalar(select(Auction).where(Auction.id==state['auction_id'],Auction.status=='submitted').with_for_update())
                if not a: admin_auction_steps.pop(m.from_user.id,None); await m.answer('Заявка уже обработана.'); return
                a.start_price=state['start']; a.highest_bid=0; a.bid_step=state['bid_step']
                a.min_price=state['min_price']; a.max_bid=value
                a.ends_at=now()+timedelta(hours=state['hours']); a.status='active'
                await audit(s,m.from_user.id,'auction_create',a.id); owner_id=a.owner_id; await s.commit()
            admin_auction_steps.pop(m.from_user.id,None)
            try: await m.bot.send_message(owner_id,'🔨 Ваша карточка одобрена и выставлена на аукцион!')
            except Exception: pass
            await m.answer('✅ Аукцион запущен. Все параметры сохранены.',reply_markup=admin_back_keyboard())

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
            'team':team,'play':play,'matches':matches,'support':support_start,'cards':collection,
            'packs':packs,'market':market,'auctions':auctions,'leaderboard':leaderboard,
            'daily':daily,'tournament':tournament,'clans':clan,'battlepass':battlepass,
            'profile':profile,'balance':balance,'promo':promo_section,'trades':trade_menu,'quests':quest,'help':help_cmd
        }
        handler=handlers.get(action)
        if not handler and action!='home':
            await c.answer('Раздел не найден.',show_alert=True); return
        try:
            def add_player_back(kwargs):
                markup=kwargs.get('reply_markup')
                rows=list(markup.inline_keyboard) if isinstance(markup,InlineKeyboardMarkup) else []
                has_section_nav=any(btn.callback_data and (btn.callback_data.startswith(('auctionmenu:','player:','market:','team:'))) for row in rows for btn in row)
                if not has_section_nav:
                    rows.append([InlineKeyboardButton(text='⬅️ Назад в главное меню',callback_data='player:home')])
                kwargs['reply_markup']=InlineKeyboardMarkup(inline_keyboard=rows)
                return kwargs
            async def proxy_answer(*args,**kwargs):
                kwargs=add_player_back(kwargs)
                # Text-only menus: edit the existing message, including legacy photo messages.
                if c.message.photo:
                    try: return await c.message.edit_caption(*args,**kwargs)
                    except Exception:
                        try: await c.message.delete()
                        except Exception: pass
                        return await c.message.answer(*args,**kwargs)
                try: return await c.message.edit_text(*args,**kwargs)
                except Exception:
                    try: await c.message.delete()
                    except Exception: pass
                    return await c.message.answer(*args,**kwargs)
            async def proxy_answer_photo(*args,**kwargs):
                # Compatibility for old handlers: discard the image and send only its caption.
                caption=kwargs.pop('caption',None)
                if caption is None and len(args)>1: caption=args[1]
                return await proxy_answer(caption or '',**kwargs)
            from types import SimpleNamespace
            menu_text={'clans':'🛡 Кланы','battlepass':'🎟 Battle Pass','tournament':'⚔️ Турнир','quests':'📋 Задания'}.get(action,'')
            player_message=SimpleNamespace(from_user=c.from_user,bot=c.bot,text=menu_text,
                answer=proxy_answer,answer_photo=proxy_answer_photo)
            if action=='home':
                caption='🏒 <b>KOSH KHL CARDS</b>\n━━━━━━━━━━━━━━\nВыбери раздел игры — команда, карточки, матчи, турниры и развитие клуба ждут тебя!'
                try:
                    photo=await telegram_post_photo()
                    try: await c.message.delete()
                    except Exception: pass
                    await c.bot.send_photo(c.from_user.id,photo,caption=caption,reply_markup=keyboard(),parse_mode='HTML')
                except Exception as e:
                    print(f'Не удалось показать фото главного меню: {e}')
                    await proxy_answer(caption,reply_markup=keyboard(),parse_mode='HTML')
            else:
                await handler(player_message)
            # Do not append the main menu automatically: each section should
            # remain visible until the player explicitly returns.
            await c.answer()
        except Exception:
            await c.answer('Не удалось открыть раздел. Попробуйте ещё раз.',show_alert=True)

    worker_task=None
    if os.getenv('RUN_MATCH_WORKER','1').strip().lower() not in ('0','false','no','off'):
        async def embedded_match_worker():
            while True:
                try:
                    await run_worker()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    import logging
                    logging.exception('Embedded match worker stopped; restarting in 5 seconds')
                    await asyncio.sleep(5)
        worker_task=asyncio.create_task(embedded_match_worker(),name='embedded-match-worker')
    try:
        await dp.start_polling(bot)
    finally:
        if worker_task:
            worker_task.cancel()
            await asyncio.gather(worker_task,return_exceptions=True)
        await bot.session.close()
