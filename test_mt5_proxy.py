import os
import threading
import time
import unittest
from unittest import mock

import mt5_proxy


class ProxyTest(unittest.TestCase):
    """Runs a real helper process (spawned with this test run's PYTHONPATH, so the MT5 stub on CI)."""

    def setUp(self):
        patcher = mock.patch.object(mt5_proxy, "INLINE", False)  # other test files switch it on for themselves
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(mt5_proxy.stop)

    def test_calls_run_in_another_process_and_errors_come_back(self):
        self.assertIsInstance(mt5_proxy.call("installed_terminals"), list)
        helper = mt5_proxy.worker["process"]
        self.assertNotEqual(helper.pid, os.getpid())
        with self.assertRaisesRegex(ValueError, "no live account yet"):
            mt5_proxy.call("switch", "", "", "live")
        self.assertIsNone(helper.poll())  # an error does not kill the helper
        mt5_proxy.call("installed_terminals")
        self.assertIs(mt5_proxy.worker["process"], helper)  # and it is reused, not restarted

    def test_a_dead_helper_is_replaced(self):
        mt5_proxy.call("installed_terminals")
        first = mt5_proxy.worker["process"]
        first.kill()
        first.wait()
        self.assertIsInstance(mt5_proxy.call("installed_terminals"), list)
        self.assertIsNot(mt5_proxy.worker["process"], first)

    def test_this_process_keeps_running_while_the_helper_works(self):
        ticks = []
        stop = threading.Event()

        def tick():
            while not stop.is_set():
                ticks.append(1)
                time.sleep(0.01)

        thread = threading.Thread(target=tick)
        thread.start()
        mt5_proxy.call("installed_terminals")  # includes starting the helper: a second or more of waiting
        stop.set()
        thread.join()
        self.assertGreater(len(ticks), 5)  # with the MT5 call in this process, this thread got 1 tick in 6.6 s


if __name__ == "__main__":
    unittest.main()
