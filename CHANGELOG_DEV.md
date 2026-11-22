# Development progress

## 2026-11-21 — Auction escrow correctness

- Fixed repeat bidding by the current leader: the wallet-availability check includes the leader's previous escrow that is refunded in the same transaction.
- Bid replacement now locks/reloads the auction row and affected wallet rows in stable ID order; the previous bidder's escrow is refunded before the new bid is debited. Settlement also refreshes locked rows after waiting for a concurrent transaction.
- Added ledger entries for bid escrow, outbid refunds, seller payout, and the 10% fee.
- Settlement reloads and locks the authoritative auction row, rechecks status/expiry, verifies that the auction card remains locked and owned by the seller, and only then transfers/pays. Reserve failure or an invalid lot refunds escrow.
- Added 5 SQLite integration tests for repeat bids, outbid refunds, preventing use of another player's escrow, reserve refunds, and idempotent settlement.

### Validation in this environment

- `python -m compileall -q app tests`: passed.
- Existing game-logic unittest suite: 6 passed.
- Auction integration tests: 5 skipped because `aiosqlite` is not installed. Installing it was attempted, but package-index network access is unavailable in this environment.
- Bot/worker startup and Docker integration were not run: project runtime dependencies (`aiogram`, `redis`, `aiosqlite`, `asyncpg`) are not all installed and no Telegram token/database services are configured.

### Next

Run the auction integration suite in a project environment after `pip install -r requirements.txt`, then add concurrency tests using PostgreSQL for simultaneous bids/settlement.
