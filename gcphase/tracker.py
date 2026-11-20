"""Attribute cyclic-GC pause time to named phases of a workload.

CPython's cyclic collector reports a ``start``/``stop`` pair through
``gc.callbacks`` around every collection.  This module timestamps those
pairs with ``time.perf_counter`` and charges the elapsed time to whichever
phase is currently open.  No sampling, no estimation: the pause length is
measured directly, and ``gcphase.calibrate`` cross-checks the sum against
an independent GC-on/GC-off wall-clock difference.
"""

from __future__ import annotations

import gc
import json
import platform
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass
class GenerationStats:
    count: int = 0
    time_s: float = 0.0
    collected: int = 0
    uncollectable: int = 0

    def as_dict(self) -> dict:
        return {
            "count": self.count,
            "time_s": round(self.time_s, 9),
            "collected": self.collected,
            "uncollectable": self.uncollectable,
        }


@dataclass
class PhaseStats:
    wall_s: float = 0.0
    generations: dict = field(
        default_factory=lambda: {g: GenerationStats() for g in (0, 1, 2)}
    )

    @property
    def gc_s(self) -> float:
        return sum(g.time_s for g in self.generations.values())

    @property
    def collections(self) -> int:
        return sum(g.count for g in self.generations.values())

    def as_dict(self) -> dict:
        wall = self.wall_s
        return {
            "wall_s": round(wall, 9),
            "gc_s": round(self.gc_s, 9),
            "gc_share": round(self.gc_s / wall, 6) if wall > 0 else None,
            "collections": self.collections,
            "by_generation": {
                str(g): st.as_dict() for g, st in sorted(self.generations.items())
            },
        }


class Tracker:
    """Collect GC pause statistics while a workload runs.

    Usage::

        with Tracker() as t:
            with t.phase("load"):
                build_objects()
            with t.phase("query"):
                answer_queries()
        print(t.report())
    """

    def __init__(self) -> None:
        self._phases: dict[str, PhaseStats] = {}
        self._stack: list[str] = []
        self._pending: float | None = None
        self._running = False
        self._started_at = 0.0
        self.elapsed_s = 0.0

    # -- lifecycle -----------------------------------------------------
    def __enter__(self) -> "Tracker":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()

    def start(self) -> "Tracker":
        if self._running:
            return self
        # Start from a clean slate so the first phase is not charged for
        # garbage left behind by the caller.
        gc.collect()
        self._phases.clear()
        self._stack.clear()
        self._pending = None
        self._started_at = time.perf_counter()
        self._running = True
        gc.callbacks.append(self._callback)
        return self

    def stop(self) -> "Tracker":
        if not self._running:
            return self
        gc.callbacks.remove(self._callback)
        self.elapsed_s = time.perf_counter() - self._started_at
        self._running = False
        return self

    # -- phases --------------------------------------------------------
    @contextmanager
    def phase(self, name: str):
        """Charge GC pauses that happen inside this block to ``name``."""
        if not self._running:
            raise RuntimeError("Tracker.start() must be called before phase()")
        self._stack.append(name)
        started = time.perf_counter()
        try:
            yield self
        finally:
            wall = time.perf_counter() - started
            self._phases.setdefault(name, PhaseStats()).wall_s += wall
            popped = self._stack.pop()
            assert popped == name, (popped, name)

    # -- callback ------------------------------------------------------
    def _callback(self, phase: str, info: dict) -> None:
        now = time.perf_counter()
        if phase == "start":
            self._pending = now
            return
        if self._pending is None:  # pragma: no cover - defensive
            return
        duration = now - self._pending
        self._pending = None
        name = self._stack[-1] if self._stack else "<outside>"
        stats = self._phases.setdefault(name, PhaseStats())
        generation = stats.generations.setdefault(
            info.get("generation", 0), GenerationStats()
        )
        generation.count += 1
        generation.time_s += duration
        generation.collected += info.get("collected", 0)
        generation.uncollectable += info.get("uncollectable", 0)

    # -- report --------------------------------------------------------
    def report(self) -> dict:
        total_gc = sum(p.gc_s for p in self._phases.values())
        elapsed = self.elapsed_s or (time.perf_counter() - self._started_at)
        return {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "elapsed_s": round(elapsed, 9),
            "total_gc_s": round(total_gc, 9),
            "gc_share_of_elapsed": round(total_gc / elapsed, 6) if elapsed > 0 else None,
            "phases": {
                name: stats.as_dict() for name, stats in self._phases.items()
            },
        }

    def dumps(self, indent: int | None = 2) -> str:
        return json.dumps(self.report(), indent=indent, sort_keys=True)