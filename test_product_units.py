import unittest
from unittest.mock import patch
from bot import costs, report, etsy

def li(title, qty=1, sku=""):
    return {"title": title, "name": title, "quantity": qty, "sku": sku,
            "variant": {"title": "", "sku": sku},
            "product": {"title": title, "productType": "", "tags": []},
            "originalUnitPriceSet": {"shopMoney": {"amount": "10.00"}}}

def order(lines, cancelled=False):
    return {"cancelledAt": "2026-11-27T00:00:00Z" if cancelled else None,
            "lineItems": {"nodes": lines}}

def receipt(items):
    return {"items": [{"title": t, "sku": "", "quantity": q} for t, q in items]}

class ProductLabelsTests(unittest.TestCase):
    def test_coloring_book_is_itself_never_a_book(self):
        labels = costs.product_labels(li("The G.O.A.T. Legends Coloring Book"))
        self.assertEqual(labels, ["Coloring book"])

    def test_standalone_book_counts_under_author(self):
        self.assertEqual(costs.product_labels(li("Tom Brady, The GOAT")), ["Tom Brady"])

    def test_known_bundle_expands_into_books(self):
        labels = sorted(costs.product_labels(li("Jordan & Brady 2-Book Bundle")))
        self.assertEqual(labels, ["Michael Jordan", "Tom Brady"])

    def test_unknown_bundle_counts_as_itself(self):
        # a bundle whose contents costs.json does not know
        labels = costs.product_labels(li("Mystery Duo Bundle"))
        self.assertEqual(labels, ["Mystery Duo Bundle"])

    def test_ultimate_gift_set_expands_into_its_five_books(self):
        labels = sorted(costs.product_labels(li("The GOAT Series Ultimate Gift Set - 5 Kids Sports Books - Mikey The GOAT Plush, Coloring Book, Stickers, Temporary Tattoos & Tote Bag")))
        self.assertEqual(labels, ["Babe Ruth", "Lionel Messi", "Michael Jordan", "Tom Brady", "Wayne Gretzky"])

    def test_big_four_bundle_expands(self):
        labels = sorted(costs.product_labels(li("GOAT Academy: The Big Four Bundle")))
        self.assertEqual(labels, ["Babe Ruth", "Michael Jordan", "Tom Brady", "Wayne Gretzky"])

    def test_champions_bundle_expands_into_three_books(self):
        labels = sorted(costs.product_labels(li("GOAT Academy: Champions Bundle")))
        self.assertEqual(labels, ["Babe Ruth", "Tiger Woods", "Wayne Gretzky"])

    def test_changed_the_game_bundle_expands(self):
        labels = sorted(costs.product_labels(li("GOAT Academy: Changed The Game Bundle")))
        self.assertEqual(labels, ["Babe Ruth", "Michael Jordan", "Tiger Woods", "Wayne Gretzky"])

    def test_champions_bundle_counts_three_books(self):
        orders = [order([li("GOAT Academy: Champions Bundle", 2)])]
        self.assertEqual(report._book_units(orders), 6)

    def test_plush_uses_short_label(self):
        self.assertEqual(costs.product_labels(li("Stuffed Animal - Mikey")), ["Mikey plush"])

    def test_unmapped_line_uses_its_title(self):
        self.assertEqual(costs.product_labels(li("Bath Time with The G.O.A.T. Legends bath book")),
                         ["Bath Time with The G.O.A.T. Legends bath book"])

class UnitsTallyTests(unittest.TestCase):
    def test_tally_expands_bundles_and_skips_cancelled(self):
        orders = [
            order([li("Tom Brady, The GOAT", 2), li("The G.O.A.T. Legends Coloring Book", 3)]),
            order([li("Jordan & Brady 2-Book Bundle", 1)]),
            order([li("Tom Brady, The GOAT", 5)], cancelled=True),
        ]
        u = report._units_tally(orders)
        self.assertEqual(u.get("Tom Brady"), 3)          # 2 standalone + 1 in bundle
        self.assertEqual(u.get("Michael Jordan"), 1)
        self.assertEqual(u.get("Coloring book"), 3)
        self.assertNotIn("Jordan & Brady 2-Book Bundle", u)

    def test_books_line_still_excludes_coloring_book(self):
        orders = [order([li("The G.O.A.T. Legends Coloring Book", 4), li("Tom Brady, The GOAT", 1)])]
        self.assertEqual(report._book_units(orders), 1)

class UnitsLineTests(unittest.TestCase):
    def test_line_merges_website_marketplace_and_etsy(self):
        web = [order([li("Tom Brady, The GOAT", 1)])]
        mp = [order([li("The G.O.A.T. Legends Coloring Book", 2)])]
        with patch("bot.costs.marketplace_name", return_value="Amazon"):
            line = report._units_line(web, mp, extra=[("Coloring book", 1), ("Bath book", 0)])
        self.assertIn("🧩 Sold:", line)
        self.assertIn("Tom Brady x1", line)
        self.assertIn("Coloring book x3", line)          # 2 Amazon + 1 Etsy
        self.assertNotIn("Bath book", line)

    def test_each_product_on_its_own_line(self):
        web = [order([li("Tom Brady, The GOAT", 3), li("The G.O.A.T. Legends Coloring Book", 2)]),
               order([li("Jordan & Brady 2-Book Bundle", 1)])]
        line = report._units_line(web, [])
        lines = line.split("\n")
        self.assertEqual(lines[0], "🧩 Sold:")
        self.assertEqual(lines[1:], ["· Tom Brady x4", "· Coloring book x2",
                                     "· Michael Jordan x1"])

    def test_line_none_when_nothing_sold(self):
        self.assertIsNone(report._units_line([], []))

    def test_line_caps_long_lists(self):
        web = [order([li(f"Unmapped Product {i}", 1)]) for i in range(15)]
        line = report._units_line(web, [])
        self.assertIn("+5 more", line)
        self.assertTrue(line.endswith("· +5 more"))
        self.assertEqual(len(line.split("\n")), 12)     # header + 10 products + more line

class EtsyProductUnitsTests(unittest.TestCase):
    def test_receipts_expand_like_website(self):
        u = etsy._product_units([receipt([("Jordan & Brady 2-Book Bundle", 1),
                                          ("The G.O.A.T. Legends Coloring Book", 2)])])
        self.assertEqual(u.get("Tom Brady"), 1)
        self.assertEqual(u.get("Michael Jordan"), 1)
        self.assertEqual(u.get("Coloring book"), 2)

if __name__ == "__main__":
    unittest.main()
