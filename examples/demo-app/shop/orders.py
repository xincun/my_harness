"""Order lifecycle."""

from shop.pricing import apply_discount, cart_total

STATES = ("created", "paid", "shipped", "cancelled")


class Order:
    def __init__(self, items: list[tuple[int, int]], discount_percent: int = 0):
        self.items = items
        self.discount_percent = discount_percent
        self.state = "created"

    @property
    def total_cents(self) -> int:
        return apply_discount(cart_total(self.items), self.discount_percent)

    def pay(self) -> None:
        if self.state != "created":
            raise ValueError(f"cannot pay an order in state {self.state}")
        self.state = "paid"

    def cancel(self) -> None:
        if self.state == "shipped":
            raise ValueError("cannot cancel a shipped order")
        self.state = "cancelled"
