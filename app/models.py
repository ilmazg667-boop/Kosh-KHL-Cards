from datetime import datetime, timezone
from sqlalchemy import (BigInteger, Integer, String, Boolean, ForeignKey, DateTime,
                        Text, JSON, UniqueConstraint, Index, CheckConstraint, text)
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base

def utcnow():
    return datetime.now(timezone.utc)

class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)  # Telegram ID
    username: Mapped[str] = mapped_column(String(64), default='')
    first_name: Mapped[str] = mapped_column(String(128), default='')
    last_name: Mapped[str] = mapped_column(String(128), default='')
    team_name: Mapped[str] = mapped_column(String(40), default='Моя команда')
    coins: Mapped[int] = mapped_column(Integer, default=5000)
    gems: Mapped[int] = mapped_column(Integer, default=20)
    rating: Mapped[int] = mapped_column(Integer, default=0)  # current season rating
    lifetime_rating: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    games: Mapped[int] = mapped_column(Integer, default=0)
    daily_claimed: Mapped[str] = mapped_column(String(10), default='')
    is_banned: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (CheckConstraint('coins >= 0'), CheckConstraint('gems >= 0'))

class Card(Base):
    __tablename__ = 'cards'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    player_name: Mapped[str] = mapped_column(String(100), index=True)
    player_key: Mapped[str] = mapped_column(String(100), default='')  # stable identity across historical team versions
    position: Mapped[str] = mapped_column(String(2), index=True)
    ovr: Mapped[int] = mapped_column(Integer)
    rarity: Mapped[str] = mapped_column(String(30), index=True)
    country: Mapped[str] = mapped_column(String(60), default='—')
    club: Mapped[str] = mapped_column(String(100), default='—')
    league: Mapped[str] = mapped_column(String(60), default='—')
    season: Mapped[str] = mapped_column(String(20), default='')
    image_url: Mapped[str] = mapped_column(String(500), default='')
    source: Mapped[str] = mapped_column(String(120), default='demo')
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (CheckConstraint('ovr >= 50 AND ovr <= 100'), CheckConstraint("position IN ('LW','C','RW','LD','RD','G')"))

class OwnedCard(Base):
    __tablename__ = 'owned_cards'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    card_id: Mapped[int] = mapped_column(ForeignKey('cards.id'))
    slot: Mapped[str] = mapped_column(String(2), default='')
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (Index('uq_equipped_slot', 'user_id', 'slot', unique=True, sqlite_where=text("slot <> ''"), postgresql_where=text("slot <> ''")),)

class Match(Base):
    __tablename__ = 'matches'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mode: Mapped[str] = mapped_column(String(20), index=True)  # pve, pvp, ranked, clan
    status: Mapped[str] = mapped_column(String(20), default='queued', index=True)
    home_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    away_user_id: Mapped[int] = mapped_column(BigInteger, default=0, index=True)
    home_name: Mapped[str] = mapped_column(String(100), default='Игрок')
    away_name: Mapped[str] = mapped_column(String(100), default='AI')
    home_power: Mapped[int] = mapped_column(Integer, default=70)
    away_power: Mapped[int] = mapped_column(Integer, default=70)
    home_goalie: Mapped[int] = mapped_column(Integer, default=70)
    away_goalie: Mapped[int] = mapped_column(Integer, default=70)
    home_card_ids: Mapped[list] = mapped_column(JSON, default=list)
    away_card_ids: Mapped[list] = mapped_column(JSON, default=list)
    league: Mapped[str] = mapped_column(String(20), default='')
    reward: Mapped[int] = mapped_column(Integer, default=0)
    bet: Mapped[int] = mapped_column(Integer, default=0)
    home_score: Mapped[int] = mapped_column(Integer, default=0)
    away_score: Mapped[int] = mapped_column(Integer, default=0)
    minute: Mapped[int] = mapped_column(Integer, default=0)
    event_index: Mapped[int] = mapped_column(Integer, default=0)
    rating_change: Mapped[int] = mapped_column(Integer, default=0)
    events: Mapped[list] = mapped_column(JSON, default=list)
    home_goals: Mapped[list] = mapped_column(JSON, default=list)
    away_goals: Mapped[list] = mapped_column(JSON, default=list)
    shootout: Mapped[bool] = mapped_column(Boolean, default=False)
    overtime: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_sent: Mapped[bool] = mapped_column(Boolean, default=False)

class MarketListing(Base):
    __tablename__ = 'market_listings'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    seller_id: Mapped[int] = mapped_column(BigInteger, index=True)
    buyer_id: Mapped[int] = mapped_column(BigInteger, default=0)
    owned_card_id: Mapped[int] = mapped_column(ForeignKey('owned_cards.id'))
    price: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default='active', index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (Index('uq_active_market_card','owned_card_id',unique=True,sqlite_where=text("status='active'"),postgresql_where=text("status='active'")),)

class TradeOffer(Base):
    __tablename__ = 'trade_offers'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sender_id: Mapped[int] = mapped_column(BigInteger, index=True)
    receiver_id: Mapped[int] = mapped_column(BigInteger, index=True)
    give_card_ids: Mapped[list] = mapped_column(JSON, default=list)
    receive_card_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default='pending', index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class Auction(Base):
    __tablename__ = 'auctions'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    owned_card_id: Mapped[int] = mapped_column(ForeignKey('owned_cards.id'))
    start_price: Mapped[int] = mapped_column(Integer)
    min_price: Mapped[int] = mapped_column(Integer, default=0)
    bid_step: Mapped[int] = mapped_column(Integer, default=100)
    max_bid: Mapped[int] = mapped_column(Integer, default=100000000)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default='submitted', index=True)
    winner_id: Mapped[int] = mapped_column(BigInteger, default=0)
    highest_bid: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (Index('uq_open_auction_card','owned_card_id',unique=True,sqlite_where=text("status IN ('submitted','active')"),postgresql_where=text("status IN ('submitted','active')")),)

class AuctionBid(Base):
    __tablename__ = 'auction_bids'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    auction_id: Mapped[int] = mapped_column(ForeignKey('auctions.id'), index=True)
    bidder_id: Mapped[int] = mapped_column(BigInteger, index=True)
    amount: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class DailyTournament(Base):
    __tablename__ = 'daily_tournaments'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tournament_date: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(100), default='')
    status: Mapped[str] = mapped_column(String(20), default='open')
    rewarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class TournamentPrizeConfig(Base):
    __tablename__ = 'tournament_prize_configs'
    place: Mapped[int] = mapped_column(Integer, primary_key=True)
    coins: Mapped[int] = mapped_column(Integer, default=0)
    gems: Mapped[int] = mapped_column(Integer, default=0)

class TournamentEntry(Base):
    __tablename__ = 'tournament_entries'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey('daily_tournaments.id'), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    points: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    goals: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (UniqueConstraint('tournament_id', 'user_id'),)

class Clan(Base):
    __tablename__ = 'clans'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(40), unique=True)
    owner_id: Mapped[int] = mapped_column(BigInteger, index=True)
    rating: Mapped[int] = mapped_column(Integer, default=0)
    points: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class ClanMember(Base):
    __tablename__ = 'clan_members'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    clan_id: Mapped[int] = mapped_column(ForeignKey('clans.id'), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    role: Mapped[str] = mapped_column(String(20), default='member')
    __table_args__ = (UniqueConstraint('clan_id','user_id'),)

class ClanTournamentEntry(Base):
    __tablename__ = 'clan_tournament_entries'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey('daily_tournaments.id'), index=True)
    clan_id: Mapped[int] = mapped_column(ForeignKey('clans.id'), index=True)
    points: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    goals: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (UniqueConstraint('tournament_id','clan_id'),)

class WeeklyClanTournament(Base):
    __tablename__ = 'weekly_clan_tournaments'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    week_key: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default='open')
    rewarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class WeeklyClanTournamentEntry(Base):
    __tablename__ = 'weekly_clan_tournament_entries'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey('weekly_clan_tournaments.id'), index=True)
    clan_id: Mapped[int] = mapped_column(ForeignKey('clans.id'), index=True)
    points: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    goals: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (UniqueConstraint('tournament_id','clan_id'),)

class BattlePassProgress(Base):
    __tablename__ = 'battlepass_progress'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    season: Mapped[str] = mapped_column(String(20), index=True)
    xp: Mapped[int] = mapped_column(Integer, default=0)
    premium: Mapped[bool] = mapped_column(Boolean, default=False)
    claimed_free: Mapped[list] = mapped_column(JSON, default=list)
    claimed_premium: Mapped[list] = mapped_column(JSON, default=list)
    __table_args__ = (UniqueConstraint('user_id','season'),)

class QuestProgress(Base):
    __tablename__ = 'quest_progress'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    quest_key: Mapped[str] = mapped_column(String(60))
    period_key: Mapped[str] = mapped_column(String(20))
    progress: Mapped[int] = mapped_column(Integer, default=0)
    claimed: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (UniqueConstraint('user_id','quest_key','period_key'),)

class Season(Base):
    __tablename__ = 'seasons'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season_key: Mapped[str] = mapped_column(String(20), unique=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

class AuditLog(Base):
    __tablename__ = 'audit_logs'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[int] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(80))
    target_id: Mapped[int] = mapped_column(BigInteger, default=0)
    details: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class ChemistryPair(Base):
    __tablename__ = 'chemistry_pairs'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_key_a: Mapped[str] = mapped_column(String(100), index=True)
    player_key_b: Mapped[str] = mapped_column(String(100), index=True)
    shared_games: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (UniqueConstraint('player_key_a','player_key_b'),)

class PackConfig(Base):
    __tablename__ = 'pack_configs'
    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    price: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(10), default='coins')
    odds: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

class ClanInvite(Base):
    __tablename__ = 'clan_invites'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    clan_id: Mapped[int] = mapped_column(ForeignKey('clans.id'), index=True)
    inviter_id: Mapped[int] = mapped_column(BigInteger)
    invitee_id: Mapped[int] = mapped_column(BigInteger, index=True)
    status: Mapped[str] = mapped_column(String(20), default='pending')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class EconomyLedger(Base):
    __tablename__ = 'economy_ledger'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    currency: Mapped[str] = mapped_column(String(10))
    amount: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(100))
    reference_id: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PromoCode(Base):
    __tablename__ = 'promo_codes'
    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    reward_coins: Mapped[int] = mapped_column(Integer, default=0)
    reward_gems: Mapped[int] = mapped_column(Integer, default=0)
    reward_xp: Mapped[int] = mapped_column(Integer, default=0)
    reward_premium: Mapped[bool] = mapped_column(Boolean, default=False)
    max_uses: Mapped[int] = mapped_column(Integer, default=0)  # 0 = unlimited
    uses_count: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

class PromoRedemption(Base):
    __tablename__ = 'promo_redemptions'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    promo_code: Mapped[str] = mapped_column(ForeignKey('promo_codes.code'), index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    coins_awarded: Mapped[int] = mapped_column(Integer, default=0)
    gems_awarded: Mapped[int] = mapped_column(Integer, default=0)
    xp_awarded: Mapped[int] = mapped_column(Integer, default=0)
    premium_awarded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint('promo_code', 'user_id'),)


class SupportMessage(Base):
    __tablename__ = 'support_messages'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    category: Mapped[str] = mapped_column(String(30), default='обращение')
    body: Mapped[str] = mapped_column(Text)
    anonymous: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default='new', index=True)
    admin_reply: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
