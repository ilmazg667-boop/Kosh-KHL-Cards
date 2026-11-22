"""Auction wallet/settlement integration tests (requires project DB dependencies)."""
import importlib.util
import unittest
from datetime import timedelta

HAS_SQLITE_ASYNC = importlib.util.find_spec('aiosqlite') is not None

class AuctionBidMathTests(unittest.TestCase):
    def test_rebid_can_use_own_released_escrow(self):
        from app.economy import available_bid_balance
        self.assertEqual(available_bid_balance(50,100,True),150)
        self.assertGreaterEqual(available_bid_balance(50,100,True),120)

    def test_other_bidder_cannot_use_escrow(self):
        from app.economy import available_bid_balance
        self.assertEqual(available_bid_balance(100,80,False),100)
        self.assertLess(available_bid_balance(100,80,False),150)

    def test_integer_payout_split_conserves_bid(self):
        from app.economy import auction_payout_and_fee
        self.assertEqual(auction_payout_and_fee(99),(89,10))
        self.assertEqual(sum(auction_payout_and_fee(100000001)),100000001)

@unittest.skipUnless(HAS_SQLITE_ASYNC, 'aiosqlite is not installed in this test environment')
class AuctionEconomyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from app.db import Base
        from app import models  # noqa: F401 - register metadata
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as s:
            s.add_all([
                models.User(id=101, first_name='Seller', coins=0),
                models.User(id=201, first_name='Bidder A', coins=150),
                models.User(id=202, first_name='Bidder B', coins=100),
            ])
            card=models.Card(player_name='DEMO Test',player_key='DEMO-test',position='C',ovr=70,
                rarity='Обычный',country='DEMO',club='DEMO',league='DEMO',season='TEST',source='demo')
            s.add(card); await s.flush()
            owned=models.OwnedCard(user_id=101,card_id=card.id,locked=True)
            s.add(owned); await s.flush()
            auction=models.Auction(owner_id=101,owned_card_id=owned.id,start_price=50,min_price=50,
                bid_step=10,max_bid=1000,ends_at=models.utcnow()+timedelta(hours=1),
                status='active')
            s.add(auction); await s.flush()
            self.auction_id=auction.id
            self.owned_id=owned.id
            await s.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_same_bidder_can_raise_using_refunded_escrow(self):
        from app.services import place_bid
        from app.models import User, AuctionBid
        async with self.Session() as s:
            user=await s.get(User,201)
            await place_bid(s,user,self.auction_id,100)
            user=await s.get(User,201)
            self.assertEqual(user.coins,50)
            await place_bid(s,user,self.auction_id,120)
            user=await s.get(User,201)
            self.assertEqual(user.coins,30)
            from sqlalchemy import select
            bids=(await s.scalars(select(AuctionBid).where(AuctionBid.auction_id==self.auction_id))).all()
            self.assertEqual([b.amount for b in bids],[100,120])

    async def test_outbid_player_is_refunded_and_new_bid_is_escrowed(self):
        from app.services import place_bid
        from app.models import User
        async with self.Session() as s:
            a=await s.get(User,201); b=await s.get(User,202)
            await place_bid(s,a,self.auction_id,80)
            await place_bid(s,b,self.auction_id,90)
            a=await s.get(User,201); b=await s.get(User,202)
            self.assertEqual(a.coins,150)  # 80 returned
            self.assertEqual(b.coins,10)   # 90 in escrow

    async def test_other_bidder_cannot_use_previous_bidder_escrow(self):
        from app.services import place_bid
        from app.models import User
        async with self.Session() as s:
            a=await s.get(User,201); b=await s.get(User,202)
            await place_bid(s,a,self.auction_id,80)
            with self.assertRaisesRegex(ValueError, 'Недостаточно монет'):
                await place_bid(s,b,self.auction_id,150)
            a=await s.get(User,201); b=await s.get(User,202)
            self.assertEqual(a.coins,70)  # previous leader's escrow remains held
            self.assertEqual(b.coins,100)

    async def test_reserve_failure_refunds_winner_and_unlocks_card(self):
        from app.services import place_bid, settle_auction, now
        from app.models import User, Auction, OwnedCard
        async with self.Session() as s:
            bidder=await s.get(User,202)
            await place_bid(s,bidder,self.auction_id,90)
            auction=await s.get(Auction,self.auction_id)
            auction.min_price=100
            auction.ends_at=now()-timedelta(seconds=1)
            await s.commit()
            self.assertTrue(await settle_auction(s,auction))
            winner=await s.get(User,202); owner=await s.get(User,101)
            card=await s.get(OwnedCard,self.owned_id)
            self.assertEqual(winner.coins,100)  # 90 refunded
            self.assertEqual(owner.coins,0)
            self.assertEqual(card.user_id,101)
            self.assertFalse(card.locked)

    async def test_settlement_pays_once_and_transfers_card(self):
        from app.services import place_bid, settle_auction, now
        from app.models import User, Auction, OwnedCard, EconomyLedger
        async with self.Session() as s:
            bidder=await s.get(User,202)
            await place_bid(s,bidder,self.auction_id,90)
            auction=await s.get(Auction,self.auction_id)
            auction.ends_at=now()-timedelta(seconds=1)
            await s.commit()
            self.assertTrue(await settle_auction(s,auction))
            self.assertFalse(await settle_auction(s,auction))
            owner=await s.get(User,101); winner=await s.get(User,202)
            card=await s.get(OwnedCard,self.owned_id)
            self.assertEqual(owner.coins,81)  # 90% payout, floor
            self.assertEqual(winner.coins,10)
            self.assertEqual(card.user_id,202)
            self.assertFalse(card.locked)
            from sqlalchemy import select
            ledger=(await s.scalars(select(EconomyLedger).where(EconomyLedger.reference_id==self.auction_id))).all()
            self.assertEqual(sum(row.amount for row in ledger),0)

if __name__=='__main__':
    unittest.main()
