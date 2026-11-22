"""Pure economy helpers; deliberately independent of database/runtime dependencies."""


def available_bid_balance(balance: int, previous_bid: int, is_same_bidder: bool) -> int:
    """Balance available for a new bid, counting only the bidder's own released escrow."""
    return balance + (previous_bid if is_same_bidder else 0)


def auction_payout_and_fee(amount: int) -> tuple[int, int]:
    """Split a winning bid using exact integer arithmetic: 90% seller, remainder fee."""
    payout = amount * 90 // 100
    return payout, amount - payout
