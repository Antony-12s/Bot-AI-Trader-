import unittest

import launcher


class ScriptNameTest(unittest.TestCase):
    def test_accepts_known_scripts_with_or_without_extension(self):
        self.assertEqual(launcher.script_name(["TradeBot.exe", "bot.py"]), "bot")
        self.assertEqual(launcher.script_name(["TradeBot.exe", "export_history", "--days", "9"]), "export_history")

    def test_rejects_unknown_or_missing_script(self):
        for argv in (["TradeBot.exe"], ["TradeBot.exe", "evil.py"], ["TradeBot.exe", "config.py"]):
            with self.assertRaises(SystemExit):
                launcher.script_name(argv)


if __name__ == "__main__":
    unittest.main()
