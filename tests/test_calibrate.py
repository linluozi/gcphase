import unittest

from gcphase import calibrate


def workload():
    data = [{"i": i, "name": f"n{i % 97}", "tags": ["a", "b"]} for i in range(100_000)]
    return len(data)


class CalibrationTests(unittest.TestCase):
    def test_callback_sum_matches_wall_clock_delta(self):
        result = calibrate(workload, repeats=3)
        self.assertGreater(result["measured_delta_s"], 0.0)
        self.assertGreater(result["speedup"], 1.0)
        self.assertIsNotNone(result["relative_error"])
        self.assertLess(abs(result["relative_error"]), 0.25, result)
        self.assertTrue(result["consistent"], result)

    def test_rejects_zero_repeats(self):
        with self.assertRaises(ValueError):
            calibrate(workload, repeats=0)


if __name__ == "__main__":
    unittest.main()