"""Price calculation. Amounts are integer cents."""


def apply_discount(total_cents: int, percent: int) -> int:
    """Return the total after a percentage discount."""
    if not 0 <= percent <= 100:
        raise ValueError("percent must be between 0 and 100")
    return total_cents - total_cents * percent // 100


def cart_total(items: list[tuple[int, int]]) -> int:
    """Sum of unit_price_cents * quantity."""
    return sum(price * qty for price, qty in items)
