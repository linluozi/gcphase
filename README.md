# gcphase

Measure how much wall-clock time your Python process spends inside the **cyclic
garbage collector**, attribute every pause to a named phase and generation, and
reclaim that time on batch loads with a reversible three-line context manager.

Upstream: none

**Evidence in one line:** on 400 real CPython stdlib files (7.0 MB), 46.0% of
the AST batch parse is cyclic GC; `bulk()` takes 1.641 s down to 0.862 s
(**1.90x**) with peak RSS unchanged, and the callback attribution is within
1.5% of an independent GC-on/GC-off wall-clock measurement.

This is an original tool, not a fork or a patch of another project. It targets
CPython 3.9+ and uses only the standard library.

## What it does

`gcphase.Tracker` timestamps the `gc.callbacks` start/stop pairs around every
collection and charges the pause to whatever `tracker.phase("...")` block is
open. The result is a per-phase, per-generation bill:

```python
from gcphase import Tracker

with Tracker() as tracker:
    with tracker.phase("load"):
        records = build_index()      # 400k retained dicts
    with tracker.phase("query"):
        answer_queries(records)
print(tracker.dumps())
```

The counter-intuitive part is how large that bill can be. Building a list of
400,000 dicts on CPython 3.12 spends **56.6%** of its time in the collector,
even though the data structure is completely acyclic. The collector is not
finding garbage; it is repeatedly rescanning a live heap that it can never
free.

## Results (this machine, real runs, 5 samples after 3 warmups)

`bulk_dicts` builds N retained dicts (4 keys + a 2-element list) and then reads
a sample of them. `ast_batch` parses **400 real CPython 3.12 stdlib files**
(7,005,221 bytes, corpus sha256 recorded in `benchmark/baseline.json`) and
retains all ASTs, which is what a linter does in one pass.

| workload | scale | GC on, median (p25–p75) | `bulk()` median | speedup | GC share | gen-2 time |
| --- | --- | --- | --- | --- | --- | --- |
| bulk_dicts | 50,000 | 47.82 ms (47.62–48.21) | 27.17 ms | **1.76x** | 36.0–39.6% | 12.3 ms |
| bulk_dicts | 100,000 | 104.78 ms (104.52–105.71) | 53.17 ms | **1.97x** | 42.0–43.2% | 33.3 ms |
| bulk_dicts | 200,000 | 248.22 ms (247.04–248.31) | 118.53 ms | **2.09x** | 50.4–51.3% | 102.5 ms |
| bulk_dicts | 400,000 | 579.40 ms (577.49–583.85) | 244.09 ms | **2.37x** | 56.3–56.9% | 281.7 ms |
| **ast_batch (real corpus)** | **400 files / 7.0 MB** | **1,640.66 ms (1,637.90–1,655.61)** | **861.96 ms** | **1.90x** | **46.0–46.7%** | **491.6 ms** |

Median speedup over the four synthetic scales is **2.03x**; the largest scale is
**2.37x**; the real corpus is **1.90x**.

The real corpus is not dominated by gen-0 noise: of its 765.7 ms median GC time,
69.9 ms is gen-0, 204.2 ms is gen-1 and **491.6 ms is gen-2** (2 collections,
about 246 ms each). That is 30.0% of the whole run spent rescanning retained
ASTs.

### The cost grows superlinearly

Per-doubling gen-2 time from the raw samples: 12.3 → 33.3 → 102.5 → 281.7 ms.
Total gen-2 time grows **22.8x for an 8x larger heap**, i.e. roughly **n^1.5**,
while the number of gen-2 collections only grows from 1 to 7. The mechanism is
simple and measurable: every gen-2 pass walks the entire live heap, and the
live heap grows with n. This is why the GC share rises monotonically from 36%
to 57% and why the speedup rises with scale.

### Calibration: the attribution is independently checked

`gcphase.calibrate()` runs the same callable twice, once with GC on and once
with it paused, and compares the callback-summed pause time with the wall-clock
difference. That difference is measured without trusting the callbacks.

| scale | GC on | GC off | measured delta | tracker sum | error | speedup |
| --- | --- | --- | --- | --- | --- | --- |
| 50,000 | 45.83 ms | 30.61 ms | 15.22 ms | 15.09 ms | **-0.89%** | 1.50x |
| 100,000 | 102.02 ms | 60.75 ms | 41.27 ms | 41.87 ms | **+1.46%** | 1.68x |
| 200,000 | 238.97 ms | 121.23 ms | 117.74 ms | 116.59 ms | **-0.98%** | 1.97x |
| 400,000 | 561.22 ms | 244.74 ms | 316.49 ms | 312.12 ms | **-1.38%** | 2.29x |

Raw output: `benchmark/calibration.json`. These runs call the workload through
the `calibrate()` wrapper (no query pass), so their ratios differ slightly from
the main table. The `bulk()` timings in the main table include the restoring
`gc.collect()` at block exit, so they are conservative.

### Peak RSS does not move

`/usr/bin/time`-style `ru_maxrss` from a fresh process, median of 3, ast corpus:

| mode | peak RSS |
| --- | --- |
| GC on | 224,172 KB |
| `bulk()` | 224,172 KB |

Ratio **1.000x**. The ASTs are retained either way; pausing the collector does
not increase the live set. This is a speed/memory-latency trade, not a
speed/memory-size trade: cycles created inside the block are only reclaimed at
exit.

## Install

No third-party dependencies:

```bash
python3 -m pip install -e .
```

Or run it from the checkout with `PYTHONPATH=.`.

## Use it

Library:

```python
from gcphase import Tracker, bulk, calibrate

with Tracker() as t:
    with t.phase("load"):
        data = [{"id": i, "tags": ["a", "b"]} for i in range(400_000)]
print(t.dumps())

with bulk() as info:
    data = [{"id": i, "tags": ["a", "b"]} for i in range(400_000)]
assert info["restored"] is True
```

CLI, profiling an existing script as one `script` phase and writing JSON:

```bash
python3 -m gcphase run your_script.py --json report.json
```

CI gate: fail when the busiest phase spends more than 15% of its wall time in
the collector.

```bash
python3 -m gcphase gate report.json --max-share 0.15
```

`bulk()` restores the previous collector state even when the block raises, and
runs one full collection on exit by default. Do not use it for unbounded
streaming work: cycles created while it is active are not reclaimed until the
block exits.

## Quality gates

This project does not modify an upstream library, so the upstream gates are not
applicable. The same axes are reported with the evidence that does exist:

| gate | verdict | evidence / command |
| --- | --- | --- |
| G1 upstream tests | **N/A** | no upstream repository is modified; this is original code (`Upstream: none`) |
| G2 byte-identical differential | **N/A for upstream**; substitute passes | `tests/test_equivalence.py`: 400 real stdlib files parsed with and without `bulk()`, sha256 of `ast.dump` compared, 0 mismatches. `python3 -m unittest tests.test_equivalence -v` |
| G3 median speedup | **2.03x** | median of the four `bulk_dicts` scales in the table; `python3 benchmark/bench.py --samples 5 --warmup 3` |
| G3b real-corpus speedup | **1.90x** | 400 real stdlib files, 5 samples; same command |
| G4 largest scale | **2.37x** | 400,000 retained dicts; same command |
| G5 peak RSS | **1.000x** | 224,172 KB both modes; `python3 benchmark/bench.py` prints `peak_rss` |

Test suite: **17 tests**, all passing in about 4.5 seconds:

```bash
python3 -m unittest discover -s tests -v
```

The suite covers phase attribution, callback cleanup, JSON serialisation,
collector state restoration (enabled, already disabled, exception path), cycle
collection at exit, zero collections while paused, the calibration cross-check
(tolerance 25%), the CLI argv-isolation bug class, gate pass/fail/missing-phase
exit codes, and the 400-file parse-equivalence check.

## Reproduce

```bash
git clone git@github-linluozi:linluozi/gcphase.git  # see note below
cd gcphase
python3 -m unittest discover -s tests -v
python3 benchmark/bench.py --samples 5 --warmup 3
```

Use the SSH alias configured on this machine (`github-linluozi`). From a normal
machine use `git clone https://github.com/linluozi/gcphase.git`.

Every sample is kept: `benchmark/raw/` contains 50 JSON files (one per suite ×
implementation × scale × sample), and `benchmark/baseline.json` /
`benchmark/optimized.json` are derived only from those files.

## Environment

| item | value |
| --- | --- |
| CPU | AMD EPYC 7K62 48-Core Processor (4 vCPU) |
| memory | 3,812,564 kB |
| Python | 3.12.3 (`/usr/bin/python3`) |
| platform | Linux-6.8.0-124-generic-x86_64-with-glibc2.39 |
| GC thresholds | `(700, 10, 10)` |
| seed | 0 (no randomness in any workload) |
| sampler | `time.perf_counter()` |
| samples | 3 warmups + 5 timed per (implementation, scale) |

Maximum relative standard deviation across all timed cells: **2.70%**
(`bulk_dicts` 50k, GC-on). No cell exceeded the 10% noise threshold, so no
GC/cache/page-fault correction is needed.

## Limitations

- This measures **CPython's cyclic collector**, not reference counting, malloc,
  or OS paging.
- The speedup applies to **bulk construction of a large retained heap**. A
  streaming workload with a bounded live set will show a much smaller share;
  the 400 real JSON files measured during development showed only ~10% GC and
  ~1.1x, because that workload never grew the gen-2 heap.
- Pausing the collector defers cycle reclamation. Measure your own peak RSS with
  the reported helper before adopting `bulk()` in a long-running process.
- The `gc.callbacks` timestamps are only as good as `perf_counter`; the
  calibration table above bounds that error at 1.5% on this machine.

## 本轮创新

首次把 CPython 3.12 的 **gen-2 全堆重扫**量化成批量构建的超线性来源：在 400 个
真实 stdlib 源文件（7.0 MB）的 AST 批量解析上，46.0–46.7% 的墙钟时间在循环 GC
里，其中 gen-2 占 491.6 ms（30.0%），且 gen-2 总耗时随存活堆约 **n^1.5** 增长；
一个可回滚的 `bulk()` 把真实语料从 1.641 s 降到 0.862 s（**1.90x**），峰值 RSS
不变。与 `slotfit`/`slotcert`/`slotforge`（对象布局）、`memtruth`（tracemalloc
与 RSS 对拍）、`coldbudget`（import 冷启动）不同，这里测的是**收集器自身**，并
用 GC-on/GC-off 墙钟差独立校准 callback 归因（误差 ≤1.46%）。这不是"发现
`gc.disable()` 会变快"；新的是按阶段/代际归因、超线性标度证书、以及带校准的
CI 判据。

## License

MIT. See `LICENSE`. No upstream code is included.