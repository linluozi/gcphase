"""CLI: run a script under the tracker, or gate a saved report in CI."""

from __future__ import annotations

import argparse
import json
import runpy
import sys
from pathlib import Path

from .tracker import Tracker


def _cmd_run(args) -> int:
    script = Path(args.script)
    if not script.is_file():
        print(f"gcphase: no such file: {script}", file=sys.stderr)
        return 2
    # The workload must see its own argv, not gcphase's flags.
    saved_argv = sys.argv
    sys.argv = [str(script)]
    try:
        with Tracker() as tracker:
            with tracker.phase("script"):
                try:
                    runpy.run_path(str(script), run_name="__main__")
                except SystemExit as exc:
                    if exc.code not in (None, 0):
                        print(f"gcphase: workload exited with {exc.code}", file=sys.stderr)
    finally:
        sys.argv = saved_argv
    report = tracker.report()
    if args.json_path:
        Path(args.json_path).write_text(json.dumps(report, indent=2, sort_keys=True))
    if not args.quiet:
        print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def _share_for(report: dict, phase: str | None) -> tuple[str, float | None]:
    phases = report.get("phases", {})
    if phase:
        entry = phases.get(phase)
        return phase, (entry or {}).get("gc_share")
    if not phases:
        return "<none>", None
    name = max(phases, key=lambda k: phases[k].get("gc_s") or 0.0)
    return name, phases[name].get("gc_share")


def _cmd_gate(args) -> int:
    report = json.loads(Path(args.report).read_text())
    name, share = _share_for(report, args.phase)
    if share is None:
        print(f"FAIL no GC measurements for phase {name!r}")
        return 1
    verdict = "PASS" if share <= args.max_share else "FAIL"
    print(
        f"{verdict} phase={name} gc_share={share:.4f} "
        f"budget={args.max_share:.4f} total_gc_s={report.get('total_gc_s')}"
    )
    return 0 if verdict == "PASS" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gcphase")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run a script under the GC phase tracker")
    run.add_argument("script")
    run.add_argument("--json", dest="json_path", default=None)
    run.add_argument("--quiet", action="store_true")
    run.set_defaults(func=_cmd_run)

    gate = sub.add_parser("gate", help="fail if a phase spends too much time in GC")
    gate.add_argument("report")
    gate.add_argument("--max-share", type=float, default=0.15)
    gate.add_argument("--phase", default=None)
    gate.set_defaults(func=_cmd_gate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())