#!/usr/bin/env python3
"""Reproducible benchmark for gcphase.

For every workload and scale this runs the same callable twice:

* ``tracked`` -- GC left on, every collection timed by gcphase.Tracker;
* ``bulk``    -- GC paused for the call, restored and collected at exit.

Timing is ``time.perf_counter``; warmup runs are discarded; every sample is
written to ``benchmark/raw/`` so the aggregates can be recomputed.  The
bulk timing includes the restoring collection, which makes the reported
speedup conservative.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gcphase import Tracker, bulk  # noqa: E402

import workloads  # noqa: E402

SEED = 0


def percentile(values, q):
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    frac = pos - low
    return ordered[low] * (1 - frac) + ordered[high] * frac


def summarize(samples):
    times = [s["seconds"] for s in samples]
    mean = statistics.fmean(times)
    stdev = statistics.stdev(times) if len(times) > 1 else 0.0
    return {
        "n": len(times),
        "median_s": round(statistics.median(times), 6),
        "p25_s": round(percentile(times, 0.25), 6),
        "p75_s": round(percentile(times, 0.75), 6),
        "mean_s": round(mean, 6),
        "stdev_s": round(stdev, 6),
        "rsd_pct": round(stdev / mean * 100, 3) if mean else None,
    }


def measure(fn, scale, impl):
    gc.collect()
    if impl == "tracked":
        with Tracker() as tracker:
            with tracker.phase("work"):
                fn(scale)
        report = tracker.report()
        phase = report["phases"]["work"]
        return phase["wall_s"], {
            "gc_s": phase["gc_s"],
            "gc_share": phase["gc_share"],
            "collections": phase["collections"],
            "by_generation": phase["by_generation"],
        }
    started = time.perf_counter()
    with bulk() as info:
        fn(scale)
    elapsed = time.perf_counter() - started
    return elapsed, {
        "collections_inside": info["collections_inside"],
        "collected_at_exit": info["collected"],
        "gc_s": 0.0,
        "gc_share": None,
    }


def cpu_model():
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def mem_total():
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "unknown"


def meta():
    return {
        "cpu": cpu_model(),
        "memory": mem_total(),
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "platform": platform.platform(),
        "seed": SEED,
        "gc_threshold": list(gc.get_threshold()),
        "command": " ".join([Path(sys.executable).name, "benchmark/bench.py"] + sys.argv[1:]),
        "corpus": workloads.corpus_meta(),
    }


def run_suite(name, fn, scales, samples, warmup, raw_dir):
    data = {"tracked": {}, "bulk": {}}
    for impl in ("tracked", "bulk"):
        for scale in scales:
            for _ in range(warmup):
                measure(fn, scale, impl)
            records = []
            for index in range(samples):
                seconds, extra = measure(fn, scale, impl)
                record = {
                    "suite": name,
                    "impl": impl,
                    "scale": scale,
                    "sample": index,
                    "seconds": seconds,
                    **extra,
                }
                records.append(record)
                raw_path = raw_dir / f"{name}-{impl}-{scale}-{index}.json"
                raw_path.write_text(json.dumps(record, indent=2, sort_keys=True))
            data[impl][str(scale)] = {
                "summary": summarize(records),
                "samples": [round(r["seconds"], 6) for r in records],
                "gc_s": [round(r.get("gc_s") or 0.0, 6) for r in records],
                "gc_share": [r.get("gc_share") for r in records],
            }
    return data


def rss_worker(suite, impl):
    fn, scales = workloads.suite(suite)
    measure(fn, scales[-1], impl)
    import resource

    print(json.dumps({"rss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}))


def peak_rss(suite, impl, repeats=3):
    values = []
    for _ in range(repeats):
        out = subprocess.check_output(
            [sys.executable, str(Path(__file__)), "--worker", suite, impl],
            text=True,
        )
        values.append(json.loads(out)["rss_kb"])
    return int(statistics.median(values))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--warmup", type=int, default=3)
    parser.add_argument("--out-dir", default=str(ROOT / "benchmark"))
    parser.add_argument("--suite", action="append", default=None)
    parser.add_argument("--worker", nargs=2, metavar=("SUITE", "IMPL"))
    args = parser.parse_args(argv)

    if args.worker:
        rss_worker(*args.worker)
        return 0

    out_dir = Path(args.out_dir)
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for stale in raw_dir.glob("*.json"):
        stale.unlink()

    suites = args.suite or ["bulk_dicts", "ast_batch"]
    runs = {}
    for name in suites:
        fn, scales = workloads.suite(name)
        print(f"[bench] {name} scales={scales}", flush=True)
        runs[name] = run_suite(
            name, fn, scales, args.samples, args.warmup, raw_dir
        )

    common = meta()
    baseline = {"meta": common, "runs": {}}
    optimized = {"meta": common, "runs": {}, "speedups": {}}
    rss = {}
    for name, data in runs.items():
        baseline["runs"][name] = data["tracked"]
        optimized["runs"][name] = data["bulk"]
        for scale in data["tracked"]:
            on = data["tracked"][scale]["summary"]["median_s"]
            off = data["bulk"][scale]["summary"]["median_s"]
            optimized["speedups"].setdefault(name, {})[scale] = round(on / off, 4) if off else None
        if name == "ast_batch":
            rss[name] = {
                "tracked_kb": peak_rss(name, "tracked"),
                "bulk_kb": peak_rss(name, "bulk"),
            }
    optimized["peak_rss"] = rss

    (out_dir / "baseline.json").write_text(json.dumps(baseline, indent=2, sort_keys=True))
    (out_dir / "optimized.json").write_text(json.dumps(optimized, indent=2, sort_keys=True))
    print(json.dumps(optimized["speedups"], indent=2))
    if rss:
        print(json.dumps(rss, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())