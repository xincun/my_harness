import unittest

from shop.orders import Order
from shop.pricing import apply_discount


class PricingTest(unittest.TestCase):
    def test_discount(self):
        self.assertEqual(apply_discount(1000, 10), 900)

    def test_order_total(self):
        self.assertEqual(Order([(250, 2), (100, 1)], discount_percent=50).total_cents, 300)

    def test_pay_twice_rejected(self):
        o = Order([(100, 1)])
        o.pay()
        with self.assertRaises(ValueError):
            o.pay()


if __name__ == "__main__":
    unittest.main()
