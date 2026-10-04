import unittest

import wizard


class WizardTest(unittest.TestCase):
    def test_filling_prefers_ioc_then_fok_then_return(self):
        self.assertEqual(wizard.filling_for(3), "IOC")
        self.assertEqual(wizard.filling_for(2), "IOC")
        self.assertEqual(wizard.filling_for(1), "FOK")
        self.assertEqual(wizard.filling_for(0), "RETURN")

    def test_spread_limit_scales_with_the_symbol(self):
        self.assertEqual(wizard.spread_limit(12), 50)  # tight gold spread keeps the default
        self.assertEqual(wizard.spread_limit(30), 90)
        self.assertEqual(wizard.spread_limit(2400), 7200)  # a crypto CFD quoted in cents

    def test_gold_like_symbols_plainest_first(self):
        names = ["EURUSD", "GOLDmicro", "GOLD", "XAUUSD.m", "XAUEUR", "US30"]
        self.assertEqual(wizard.gold_like(names), ["GOLD", "XAUEUR", "XAUUSD.m", "GOLDmicro"])
        self.assertEqual(wizard.gold_like(["EURUSD"]), [])

    def test_render_env_fills_values_and_keeps_comments(self):
        template = "# mode\nMODE=dry\n# symbol as the broker shows it\nSYMBOL=XAUUSD\nLOT=0.01\nTELEGRAM_TOKEN=\n"
        text = wizard.render_env(template, {"SYMBOL": "GOLD", "LOT": "0.02", "TELEGRAM_TOKEN": "123:abc"})
        self.assertEqual(text, "# mode\nMODE=dry\n# symbol as the broker shows it\nSYMBOL=GOLD\nLOT=0.02\nTELEGRAM_TOKEN=123:abc\n")

    def test_chat_ids_newest_first_without_duplicates(self):
        updates = [
            {"update_id": 1, "message": {"chat": {"id": 111}}},
            {"update_id": 2, "message": {"chat": {"id": 222}}},
            {"update_id": 3, "edited_message": {"chat": {"id": 333}}},
            {"update_id": 4, "message": {"chat": {"id": 111}}},
        ]
        self.assertEqual(wizard.chat_ids(updates), ["111", "222"])
        self.assertEqual(wizard.chat_ids([]), [])


if __name__ == "__main__":
    unittest.main()
