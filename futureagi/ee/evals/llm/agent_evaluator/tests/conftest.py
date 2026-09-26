"""Random-sample @pytest.mark.live_llm tests when the matrix is too big to run in full.

Set ``LIVE_LLM_SAMPLE_FRACTION=0.1`` to run a random 10% slice of the
live_llm tests per invocation. Set ``LIVE_LLM_SAMPLE_SEED`` to a fixed
integer for a reproducible sample.

When the env var is unset (the default), this hook is a no-op and all
collected tests run.
"""

import os
import random


def pytest_collection_modifyitems(config, items):
    fraction_raw = os.environ.get("LIVE_LLM_SAMPLE_FRACTION")
    if not fraction_raw:
        return
    try:
        fraction = float(fraction_raw)
    except ValueError:
        return
    if fraction >= 1.0:
        return
    live = [i for i in items if i.get_closest_marker("live_llm")]
    if not live:
        return
    other = [i for i in items if not i.get_closest_marker("live_llm")]
    keep = max(1, int(len(live) * fraction))
    seed = os.environ.get("LIVE_LLM_SAMPLE_SEED")
    if seed is not None:
        try:
            random.seed(int(seed))
        except ValueError:
            pass
    items[:] = other + random.sample(live, keep)
