"""A reversible fast path for bulk, cycle-free object construction.

The measurement in ``benchmark/`` shows that gen-2 collections repeatedly
rescan objects that are still reachable and are never going to be freed.
For a load-then-serve batch this is pure overhead.  ``bulk()`` pauses the
collector for the block, then restores the previous state and optionally
runs one full collection at exit.

The trade-off is explicit: while the block runs, reference cycles created
inside it are not reclaimed.  Use it when the block creates a bounded
amount of garbage; do not use it for unbounded streaming work.
"""

from __future__ import annotations

import gc
from contextlib import contextmanager


@contextmanager
def bulk(disable: bool = True, collect: bool = True):
    """Pause cyclic GC inside the block and restore the prior state.

    Yields a dict that is filled on exit with what actually happened, so a
    caller can report the trade-off instead of guessing::

        with bulk() as info:
            data = load()
        assert info["restored"] is True
    """
    info = {
        "was_enabled": gc.isenabled(),
        "disabled": False,
        "collected": False,
        "collections_inside": 0,
    }
    before = gc.get_stats()
    try:
        if disable and info["was_enabled"]:
            gc.disable()
            info["disabled"] = True
        yield info
    finally:
        after = gc.get_stats()
        info["collections_inside"] = sum(
            a["collections"] - b["collections"] for a, b in zip(after, before)
        )
        if info["disabled"]:
            gc.enable()
            info["restored"] = True
            if collect:
                gc.collect()
                info["collected"] = True
        else:
            info["restored"] = info["was_enabled"] == gc.isenabled()