"""The fast path must not change what the workload computes.

Parses 400 real stdlib source files twice -- once with the collector active,
once inside ``bulk()`` -- and compares a sha256 of ``ast.dump`` for every file.
"""

import ast
import glob
import hashlib
import os
import unittest

from gcphase import bulk


def corpus(limit=400):
    pattern = os.path.join(os.path.dirname(os.__file__), "**", "*.py")
    sources = []
    for path in sorted(glob.glob(pattern, recursive=True)):
        try:
            size = os.path.getsize(path)
        except OSError:
            continue
        if 200 < size <= 200_000:
            with open(path, "rb") as handle:
                sources.append((path, handle.read()))
        if len(sources) >= limit:
            break
    return sources


def digest(source):
    tree = ast.parse(source)
    return hashlib.sha256(ast.dump(tree, include_attributes=False).encode()).hexdigest()


class EquivalenceTests(unittest.TestCase):
    def test_bulk_does_not_change_parse_output(self):
        sources = corpus()
        self.assertGreaterEqual(len(sources), 400)
        plain = [digest(source) for _, source in sources]
        with bulk():
            fast = [digest(source) for _, source in sources]
        mismatches = [path for (path, _), a, b in zip(sources, plain, fast) if a != b]
        self.assertEqual(mismatches, [])
        self.assertEqual(len(plain), 400)


if __name__ == "__main__":
    unittest.main()