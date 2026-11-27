"""Split deploy/tests/test_update_*.py into chunks of similar measured time.

Update CI's unit job runs one chunk per matrix leg:

  python deploy/tests/update_unit_chunks.py 2/3   # the modules of chunk 2 of 3

The plan is a pure function of the checkout, so every leg computes the same
split and the legs together run every module exactly once. Each module counts
its seconds in update_unit_durations.json (Python 3.9, Update CI on a queue
run); a module without a recorded time counts as the median module. The
longest modules go first, each into the least loaded chunk.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DURATIONS = HERE / "update_unit_durations.json"


def modules(directory: Path = HERE) -> list[str]:
    return sorted(path.stem for path in directory.glob("test_update_*.py"))


def plan(names: list[str], durations: dict[str, float], total: int) -> list[list[str]]:
    if total < 1:
        raise ValueError(f"chunk count must be at least 1, not {total}")
    known = sorted(durations[name] for name in names if name in durations)
    median = known[len(known) // 2] if known else 1.0
    weight = {name: durations.get(name, median) for name in names}
    chunks: list[list[str]] = [[] for _ in range(total)]
    loads = [0.0] * total
    for name in sorted(names, key=lambda name: (-weight[name], name)):
        index = min(range(total), key=lambda i: (loads[i], i))
        chunks[index].append(name)
        loads[index] += weight[name]
    return [sorted(chunk) for chunk in chunks]


def main(argv: list[str]) -> int:
    match = re.fullmatch(r"([1-9][0-9]*)/([1-9][0-9]*)", argv[0] if len(argv) == 1 else "")
    if not match or int(match.group(1)) > int(match.group(2)):
        print("usage: update_unit_chunks.py K/N (1 <= K <= N)", file=sys.stderr)
        return 2
    index, total = int(match.group(1)), int(match.group(2))
    durations = json.loads(DURATIONS.read_text(encoding="utf-8"))
    chunk = plan(modules(), durations, total)[index - 1]
    if not chunk:
        print(f"chunk {index}/{total} has no modules", file=sys.stderr)
        return 1
    print("\n".join(chunk))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
