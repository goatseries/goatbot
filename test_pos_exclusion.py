import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from bot import costs, main as m

UTC = timezone.utc

def order(source=None, status="PAID", total="20.00", oid="o1", name="#1001", app=None, tags=None, age_h=1):
    now = datetime.now(UTC)
    ts = (now - timedelta(hours=age_h)).isoformat()
    return {
        "id": oid, "name": name, "sourceName": source, "app": app, "tags": tags or [],
        "createdAt": ts, "processedAt": ts, "updatedAt": now.isoformat(),
        "cancelledAt": None, "displayFinancialStatus": status,
        "totalPriceSet": {"shopMoney": {"amount": total}},
        "totalRefundedSet": {"shopMoney": {"amount": "0"}},
        "totalTaxSet": {"shopMoney": {"amount": "0"}},
        "lineItems": {"nodes": [{"title": "GOAT Series Book", "quantity": 1,
                                 "originalUnitPriceSet": {"shopMoney": {"amount": total}}}]},
    }

def fresh_state():
    return {"seen": [], "pending": [], "alerted": [], "recheck": {}}

class IsPosOrderTests(unittest.TestCase):
    def test_exact_source_match_only(self):
        self.assertTrue(costs.is_pos_order(order(source="pos")))
        self.assertTrue(costs.is_pos_order(order(source="POS")))
        self.assertTrue(costs.is_pos_order(order(source="Pos")))
        self.assertFalse(costs.is_pos_order(order(source="web")))
        self.assertFalse(costs.is_pos_order(order(source="shopify_draft_order")))
        self.assertFalse(costs.is_pos_order(order(source=None)))
        self.assertFalse(costs.is_pos_order(order(source="")))
    def test_substring_never_matches(self):
        # an app or source name merely containing "pos" stays counted
        self.assertFalse(costs.is_pos_order(order(source="web", app={"name": "Shopify POS Helper"})))
        self.assertFalse(costs.is_pos_order(order(source="compose")))
        self.assertFalse(costs.is_pos_order(order(source=None, app={"name": "Shopify POS"})))

class SplitPosTests(unittest.TestCase):
    def test_separates_pos(self):
        pos, web = order(source="pos", oid="p1"), order(source="web", oid="w1")
        keep, excluded = costs.split_pos([pos, web])
        self.assertEqual([o["id"] for o in keep], ["w1"])
        self.assertEqual([o["id"] for o in excluded], ["p1"])

class SplitWholesalePosTests(unittest.TestCase):
    def test_pos_removed_not_counted_as_wholesale(self):
        pos, web = order(source="pos", oid="p1"), order(source="web", oid="w1")
        keep, n_ws = costs.split_wholesale([pos, web])
        self.assertEqual([o["id"] for o in keep], ["w1"])
        self.assertEqual(n_ws, 0)
    def test_pos_removed_even_when_wholesale_config_off(self):
        with patch.object(costs, "load_config", lambda: {}):
            keep, n_ws = costs.split_wholesale([order(source="pos", oid="p1"), order(source="web", oid="w1")])
        self.assertEqual([o["id"] for o in keep], ["w1"])
        self.assertEqual(n_ws, 0)
    def test_pos_removed_before_mixed_order_lines(self):
        # a POS order is excluded as a whole order even with ordinary retail line titles
        pos = order(source="pos", oid="p1")
        keep, _ = costs.split_wholesale([pos])
        self.assertEqual(keep, [])

class AlertPathTests(unittest.TestCase):
    """Mirrors main.py: keep_ids = split_wholesale(split_marketplace(got)[0])[0]."""
    def test_pos_order_seeded_never_alerted(self):
        pos = order(source="pos", oid="p1")
        got = [pos]
        keep_ids = {o["id"] for o in costs.split_wholesale(costs.split_marketplace(got)[0])[0]}
        self.assertEqual(m.new_sale_decision(pos, fresh_state(), keep_ids, 6), "seed")
    def test_web_order_still_alerts(self):
        web = order(source="web", oid="w1")
        got = [web]
        keep_ids = {o["id"] for o in costs.split_wholesale(costs.split_marketplace(got)[0])[0]}
        self.assertEqual(m.new_sale_decision(web, fresh_state(), keep_ids, 6), "alert")
    def test_amazon_handling_unaffected(self):
        amz = order(source=None, oid="a1", tags=["A1VR1642OHFXP4"])
        got = [amz]
        keep, mp = costs.split_marketplace(got)
        self.assertEqual(keep, [])
        self.assertEqual([o["id"] for o in mp], ["a1"])
        keep_ids = {o["id"] for o in costs.split_wholesale(keep)[0]}
        self.assertEqual(m.marketplace_sale_decision(amz, fresh_state(), 6), "alert")
    def test_pending_pos_order_never_alerts_after_payment(self):
        pos = order(source="pos", oid="p1", status="PENDING")
        s = fresh_state()
        keep_ids = set()
        self.assertEqual(m.new_sale_decision(pos, s, keep_ids, 6), "skip")   # fresh but unpaid
        pos["displayFinancialStatus"] = "PAID"
        self.assertEqual(m.new_sale_decision(pos, s, keep_ids, 6), "seed")    # paid POS: silent, no alert

if __name__ == "__main__":
    unittest.main()
