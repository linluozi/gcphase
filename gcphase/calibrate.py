"""Cross-check a Tracker report against the GC-on/GC-off wall-clock delta.

This is the falsifiable part of the tool: if the sum of callback-timed
pauses does not match an independent measurement, the attribution is wrong
and the report should not be trusted.
"""

from __future__ import annotations

import gc
import statistics
import time

from .bulk import bulk
from .tracker import Tracker


def calibrate(fn, repeats: int = 3) -> dict:
    """Run ``fn`` with GC on and off and compare the two independent measurements.

    ``fn`` must be repeatable and allocate the same workload each call.
    Returns medians plus the signed relative error of the callback sum.
    """
    if repeats < 1:
        raise ValueError("repeats must be >= 1")
    on_times: list[float] = []
    off_times: list[float] = []
    tracker_sums: list[float] = []

    for _ in range(repeats):
        gc.collect()
        with Tracker() as tracker:
            started = time.perf_counter()
            fn()
            on_times.append(time.perf_counter() - started)
        tracker_sums.append(tracker.report()["total_gc_s"])

        gc.collect()
        started = time.perf_counter()
        with bulk():
            fn()
        off_times.append(time.perf_counter() - started)

    on = statistics.median(on_times)
    off = statistics.median(off_times)
    measured = on - off
    tracked = statistics.median(tracker_sums)
    if measured > 0:
        error = (tracked - measured) / measured
    else:  # pragma: no cover - only when the workload triggers no GC
        error = None
    return {
        "repeats": repeats,
        "gc_on_s": round(on, 6),
        "gc_off_s": round(off, 6),
        "measured_delta_s": round(measured, 6),
        "tracker_sum_s": round(tracked, 6),
        "relative_error": round(error, 6) if error is not None else None,
        "speedup": round(on / off, 6) if off > 0 else None,
        "consistent": error is not None and abs(error) <= 0.15,
    }