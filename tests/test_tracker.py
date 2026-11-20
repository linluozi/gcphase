import gc
import json
import unittest

from gcphase import Tracker


def make_cycles(count):
    for _ in range(count):
        left = {}
        right = {"left": left}
        left["right"] = right


class TrackerTests(unittest.TestCase):
    def tearDown(self):
        gc.enable()

    def test_collections_are_charged_to_the_open_phase(self):
        with Tracker() as tracker:
            with tracker.phase("cycles"):
                make_cycles(400)
            with tracker.phase("quiet"):
                sum(range(1000))
        report = tracker.report()
        self.assertGreater(report["phases"]["cycles"]["collections"], 0)
        self.assertEqual(report["phases"]["quiet"]["collections"], 0)
        self.assertGreater(report["phases"]["cycles"]["gc_s"], 0.0)

    def test_callback_is_removed_on_stop(self):
        before = len(gc.callbacks)
        tracker = Tracker().start()
        self.assertEqual(len(gc.callbacks), before + 1)
        tracker.stop()
        self.assertEqual(len(gc.callbacks), before)

    def test_allocations_outside_a_phase_are_labelled(self):
        with Tracker() as tracker:
            make_cycles(1000)
        self.assertIn("<outside>", tracker.report()["phases"])

    def test_report_is_json_serialisable_and_consistent(self):
        with Tracker() as tracker:
            with tracker.phase("work"):
                make_cycles(200)
        report = tracker.report()
        encoded = json.dumps(report)
        decoded = json.loads(encoded)
        self.assertEqual(decoded["python"], report["python"])
        phase = report["phases"]["work"]
        generation_sum = sum(g["time_s"] for g in phase["by_generation"].values())
        self.assertAlmostEqual(generation_sum, phase["gc_s"], places=6)
        self.assertGreaterEqual(phase["gc_share"], 0.0)
        self.assertLessEqual(phase["gc_share"], 1.0)

    def test_phase_requires_started_tracker(self):
        tracker = Tracker()
        with self.assertRaises(RuntimeError):
            with tracker.phase("nope"):
                pass


if __name__ == "__main__":
    unittest.main()