"""Workloads used by the benchmark.

Two suites:

* ``bulk_dicts`` -- synthetic but representative of an ETL batch: build N
  dict records, keep them alive, then read a sample of them back.
* ``ast_batch``  -- the real corpus: parse hundreds of real CPython stdlib
  source files and keep the resulting ASTs alive, which is what a linter
  (flake8, pyflakes, radon) does when it processes a project in one pass.

The corpus is read from the local CPython installation; its exact bytes are
hashed into the report so a rerun can prove it used the same input.
"""

from __future__ import annotations

import ast
import glob
import hashlib
import os
import sys

STDLIB_DIR = os.path.dirname(os.__file__)


def find_python_files(limit: int = 400, max_bytes: int = 200_000) -> list[str]:
    pattern = os.path.join(STDLIB_DIR, "**", "*.py")
    files = []
    for path in sorted(glob.glob(pattern, recursive=True)):
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        if 200 < size <= max_bytes:
            files.append(path)
        if len(files) >= limit:
            break
    if len(files) < 20:
        raise RuntimeError(
            f"only {len(files)} stdlib files found under {STDLIB_DIR}; "
            "the real-corpus suite needs a normal CPython installation"
        )
    return files


def read_corpus(paths: list[str]) -> list[bytes]:
    sources = []
    for path in paths:
        with open(path, "rb") as handle:
            sources.append(handle.read())
    return sources


def corpus_digest(paths: list[str], sources: list[bytes]) -> str:
    digest = hashlib.sha256()
    for path, source in zip(paths, sources):
        digest.update(path.encode())
        digest.update(b"\0")
        digest.update(source)
        digest.update(b"\0")
    return digest.hexdigest()


def bulk_dicts(n: int) -> int:
    data = [
        {
            "id": i,
            "name": f"user{i % 1000}",
            "value": i * 1.5,
            "tags": ["a", "b"],
        }
        for i in range(n)
    ]
    total = 0
    for i in range(0, n, 8):
        total += data[i]["value"]
    return total


def ast_batch(sources: list[bytes]) -> int:
    trees = [ast.parse(source) for source in sources]
    return len(trees)


def ast_batch_no_gc(sources: list[bytes]) -> int:
    """Same parse, used only to verify the parser is not the variable."""
    return ast_batch(sources)


def suite(name: str):
    if name == "bulk_dicts":
        return bulk_dicts, [50_000, 100_000, 200_000, 400_000]
    if name == "ast_batch":
        paths = find_python_files(limit=400)
        sources = read_corpus(paths)
        digest = corpus_digest(paths, sources)
        return (lambda _n: ast_batch(sources)), [len(sources)]
    raise SystemExit(f"unknown suite: {name}")


def corpus_meta() -> dict:
    paths = find_python_files(limit=400)
    sources = read_corpus(paths)
    return {
        "files": len(paths),
        "bytes": sum(len(s) for s in sources),
        "sha256": corpus_digest(paths, sources),
        "root": STDLIB_DIR,
        "python": sys.version.split()[0],
    }