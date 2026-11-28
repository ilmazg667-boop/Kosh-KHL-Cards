# 🏒 Kosh KHL Cards — Telegram bot

Expanded MVP foundation for the Kosh KHL Cards concept. This is a **normal Telegram bot** (aiogram 3), not a Mini App. It has two processes: the bot handles Telegram updates; the worker runs queued games and closes auctions independently.

## What is implemented

### Core game
- Telegram registration and the 5,000 🪙 + 20 💎 starter wallet.
- Six starter cards and a position-locked lineup: LW, C, RW, LD, RD, G.
- Team OVR is the rounded average of the six card OVR values.
- Card rarity tiers and configurable pack odds; the odds are displayed before opening.
- Coin packs and premium gem packs. Missing rarity stock is reported rather than silently substituting a different rarity.
- Card collection, card detail view, optional image URL, equip/replace, and immutable club-version history through `/transfercard`.
- Shared-game chemistry imported as pair records; simply sharing a club gives no chemistry bonus. Chemistry is capped at 40.

### Matches
- PvE against an AI. When an imported club has a complete six-position roster, the worker uses that real club roster. Otherwise it uses a clearly labelled AI fallback.
- Regular PvP matchmaking and ranked matchmaking. Ranked queue pairs players from the same rating league and within a team-strength window.
- Direct player challenge with an optional coin stake. Both stakes are reserved; declining/expiring a challenge refunds the caller.
- Persistent match state, generated event-by-event text commentary, 60-minute regulation, overtime, and shootout resolution.
- Run the worker separately so matches continue if users close Telegram. The worker stores progress/events in PostgreSQL/SQLite and can resume after process restart.
- PvE reward schedule and regular PvP rewards. Ordinary non-shootout wins award 3 💎; shootout wins do not.
- Ranked rating change uses result, opponent rating, and goal difference. Three-month quarter seasons reset the current rating while retaining permanent stats.

### Economy and social play
- Player-to-player market; buyer pays seller, with no market commission. Listing can be cancelled using `/delist ID`.
- Direct one-card-for-one-card trade offers with confirmation buttons and no commission.
- Admin-submitted auctions with start price, bid increment, duration, reserve/minimum sale price, maximum bid, cancellation, escrow/refund of outbid users, and a 10% admin fee. A leader's repeat bid accounts for their refunded prior escrow; bid escrow/refunds, seller payout and fee are ledgered, and settlement re-locks/rechecks status to avoid double payout. One winning bidder is supported per card lot.
- Daily shared leaderboard tournament; PvP matches award 3/2/1/0 points. The daily winner receives a random imported non-demo card in the 80–90 OVR range (higher OVR has lower weight). If no eligible real cards are imported, the bot notifies the winner instead of handing out a fictional card.
- Clans with owner/admin/member roles, invites, join/leave, and clan battle points.
- Daily/weekly/seasonal quest progress and a 100-level seasonal Battle Pass with Free and Premium reward lines.

### Admin commands
- Wallet management: `/givecoins`, `/givegems`.
- Card management: `/givecard`, `/createcard`, `/editcard`, `/transfercard`.
- Bulk card/chemistry import by uploading CSV files.
- Pack economy: `/setpack` for price, currency, and rarity weights; `/cardactive` toggles future drop eligibility.
- Auction queue and lifecycle: `/auction_queue`, `/auction_create`, `/auction_cancel`.
- Player moderation: `/ban`, `/unban`.
- Battle Pass operations: `/bpgrant USER XP` grants pass XP.
- Operational stats: `/stats`; seasonal rating reset: `/season_reset`.
- Admin actions are recorded in an audit table.

## Important data limitation

The repository does **not** invent a real KHL roster or copy player photographs. The built-in cards are marked `DEMO` and exist only to make a fresh installation playable. To run with real players/teams, import a verified and properly licensed data set using the CSV format below. The same rule applies to photos: only use direct image URLs you have the right to use.

AI opponent lineups are built from imported clubs that have all six positions. If no complete real-club roster is present, the opponent is labelled as AI and uses a fallback rating. Chemistry stays at zero until real shared-games data is imported.

The Battle Pass grants `Семён Кошелев · 99 OVR` at level 100. The game does not fabricate this player card: the administrator must import the verified 99 OVR card (including the correct position and permitted metadata) before the reward can be claimed.

Premium Battle Pass unlock price defaults to 100 💎 and can be changed with `BATTLEPASS_PREMIUM_PRICE` in `.env`.

## Requirements

- Python 3.11+ (Docker image uses Python 3.12)
- Telegram bot token from BotFather
- PostgreSQL 16 + Redis 7 for deployment (SQLite is convenient for local testing)

Install and run locally:

```bash
cp .env.example .env
# Edit .env: set BOT_TOKEN and your numeric Telegram ID in ADMIN_IDS
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
python -m app                      # Telegram bot process
```

In a second terminal, from the same project folder and with the same `.env`:

```bash
python -m app.worker               # Match/auction worker
```

For Docker deployment:

```bash
cp .env.example .env
# Edit BOT_TOKEN and ADMIN_IDS; set POSTGRES_PASSWORD to a long random URL-safe alphanumeric value (avoid @, / and :).
docker compose up -d --build
docker compose logs -f bot worker
```

The compose file first runs a one-shot database bootstrap, then starts the Telegram bot, a separate match worker, PostgreSQL and Redis. Redis supplies a distributed match lock when more than one worker is started; match records/events remain persisted in the database.

## Import real player cards

Upload a `.csv` file to the bot from an admin account. Required headers:

```csv
player_name,player_key,position,ovr,rarity,country,club,league,season,image_url,source
```

- `player_key` is a stable identifier for one real person across team versions. Use the same key for that player after a transfer.
- `position` must be one of `LW,C,RW,LD,RD,G`.
- `ovr` must be 50–100; the game derives one of ten Russian rarity tiers from the OVR ranges in `app/game.py` (the CSV `rarity` column remains for template compatibility).
- `club`, `league`, `season` describe this particular historical card version.
- `image_url` must be a direct, publicly accessible image URL. Leave blank if you have no usable image.
- `source` can identify the official/licensed source. Optional `active` is `true` by default; set it to `false` for historical versions that must remain collectible but should no longer drop from packs or AI rosters.

When a player changes club, import a new card row rather than editing the club on the existing card. Existing cards remain historical. `/transfercard ID NEW_CLUB NEW_LEAGUE SEASON` is a quick admin command that copies the player's current card data into a new club-version card with the same OVR.

## Import real chemistry

Upload a CSV whose filename begins with `chem` (for example `chemistry.csv`) with headers:

```csv
player_key_a,player_key_b,shared_games
```

Each row is the number of real games played together by that pair, not merely games played for the same club. Pair order is normalized during import. The chemistry engine adds a small pair-based contribution and caps team chemistry at 40.

Templates are in `data/cards.csv` and `data/chemistry.csv`.

## Player commands

| Command | Purpose |
|---|---|
| `/start` | Register / open main menu |
| `/equip ID POS` | Equip an owned card in its exact position |
| `/card ID` | View card details/photo |
| `/pve` | Start AI match; select league |
| `/pvp` | Casual PvP matchmaking |
| `/ranked` | Ranked matchmaking within rating league |
| `/challenge USER_ID [BET]` | Direct challenge and optional coin stake |
| `/match` | See latest match status and recent events |
| `/cancelmatch` | Cancel an unpaired PvP queue search |
| `/sell ID PRICE`, `/buy ID`, `/delist ID` | Market operations |
| `/trade USER_ID MY_CARD THEIR_CARD` | Offer a one-for-one exchange |
| `/trade_cancel OFFER_ID` | Cancel your pending trade |
| `/auction_submit ID`, `/auctions`, `/bid ID AMOUNT` | Auction operations |
| `/tournament` | Join/view today's tournament standings |
| `/clan create NAME`, `/clan join ID`, `/clan invite USER_ID`, `/clan leave`, `/clanbattle` | Clan operations |
| `/bp`, `/bp premium`, `/bp claim LEVEL`, `/bp premium_claim LEVEL` | Battle Pass |
| `/quest` | Daily, weekly and seasonal tasks |
| `/leaderboard`, `/season` | Ranking and season details |

## Admin command details

- `/createcard name|position|ovr|rarity|country|club|league|image_url|player_key`
- `/editcard ID ovr|rarity|country|image_url VALUE` (club/team history is intentionally immutable here)
- `/setpack PACK_NAME|PRICE|coins-or-gems|RARITY=WEIGHT,...` (weights must add to 100)
- `/auction_create ID START STEP HOURS MIN MAX`
- `/auction_cancel ID`
- `/bpgrant USER_ID XP`
- `/cardactive CARD_ID on|off`

Admin commands are available only to IDs listed in `ADMIN_IDS`.

## Run and release checklist

Before opening a public game:
1. Import verified real player cards and full team rosters, then import shared-games chemistry data.
2. Verify photo licenses/direct links and official league/team/player naming.
3. Change the sample database password and set a private `ADMIN_IDS` list.
4. Back up PostgreSQL and add Alembic migrations before changing models in an existing live database. `create_all` creates new tables on first start; it does not migrate old columns.
5. Load-test match workers, market/auction concurrency, pack odds and wallet ledgers with multiple concurrent players.
6. Review legal rights for names, photos, team marks, league marks and any paid premium-currency/payment flow.

## Current scope and production caveats

This is a feature-rich MVP codebase, not a finished audited commercial service. The daily tournament has one winner per day and requires imported non-demo reward cards; one winner per auction lot is supported; trade is one-for-one; pack types are configurable but league/team/event-specific filters are not yet implemented. Redis is used for worker locking, while database polling is used for the queue. Payment-provider integration, Mini App, real licensed player dataset, full administrative web dashboard, automated database migrations, anti-fraud/rate-limit policy, observability and formal load/security testing remain deployment work.
