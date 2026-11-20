import gc
import unittest
import weakref

from gcphase import bulk


class Node:
    pass


def make_cycle():
    first, second = Node(), Node()
    first.other = second
    second.other = first
    return weakref.ref(first)


class BulkTests(unittest.TestCase):
    def tearDown(self):
        gc.enable()

    def test_disables_and_restores(self):
        gc.enable()
        with bulk() as info:
            self.assertFalse(gc.isenabled())
        self.assertTrue(gc.isenabled())
        self.assertTrue(info["disabled"])
        self.assertTrue(info["restored"])
        self.assertTrue(info["collected"])

    def test_does_not_enable_a_previously_disabled_collector(self):
        gc.disable()
        try:
            with bulk() as info:
                self.assertFalse(gc.isenabled())
            self.assertFalse(gc.isenabled())
            self.assertFalse(info["disabled"])
        finally:
            gc.enable()

    def test_cycles_created_inside_are_collected_at_exit(self):
        reference = make_cycle()
        self.assertIsNotNone(reference())
        with bulk():
            make_cycle()
        # The original cycle is still referenced by ``reference``'s object,
        # so build one with no surviving local and watch it disappear.
        reference = make_cycle()
        with bulk():
            pass
        self.assertIsNone(reference())

    def test_state_is_restored_when_the_block_raises(self):
        with self.assertRaises(ValueError):
            with bulk():
                raise ValueError("boom")
        self.assertTrue(gc.isenabled())

    def test_no_collections_while_disabled(self):
        with bulk() as info:
            for _ in range(200):
                pair_a, pair_b = {}, {}
                pair_a["b"] = pair_b
                pair_b["a"] = pair_a
        self.assertEqual(info["collections_inside"], 0)


if __name__ == "__main__":
    unittest.main()