"""pytest plugin for Backend CI: records the id of every test this process collected.

Every shard collects the whole suite in its own process, and pytest-split
then keeps one group of it. That runs each test exactly once only when all
shards collect the same ids. An id that holds a per-process random value (a
uuid4() in a parametrize list) breaks it without any failure: one case runs in
two shards and another in none. Each shard records what it collected, and
check_collected_ids.py fails the run when two records differ.

    PYTHONPATH=.ci COLLECTED_IDS_FILE=ids.txt pytest -p collected_ids ...
"""

import hashlib
import os

import pytest

# A few generated parameter ids are hundreds of kilobytes long. The digest
# covers the whole id; the text is for the person who reads a difference.
SHOWN_ID_LENGTH = 300


def record(nodeids, path):
    lines = sorted(
        hashlib.sha256(nodeid.encode("utf-8", "surrogateescape")).hexdigest()[:24]
        + " "
        + nodeid[:SHOWN_ID_LENGTH]
        for nodeid in nodeids
    )
    with open(path, "w", encoding="utf-8", errors="surrogateescape") as out:
        out.write("\n".join(lines) + "\n")


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items):
    # tryfirst: before -m and pytest-split deselect anything.
    path = os.environ.get("COLLECTED_IDS_FILE")
    if path:
        record((item.nodeid for item in items), path)
