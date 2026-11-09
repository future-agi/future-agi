"""A path's optional caches are data, never a reason to run metaclass hooks.

The scenario is observed in what the configured sinks emit (checkout_sinks;
ruling 681 addendum 5), not in the in-memory record.
"""

from collections import Counter
from pathlib import PurePosixPath

import pytest

from tfc.tests.checkout_sinks import dispatch, native_emit
from tfc.tests.test_checkout_additional_exports import CHECKOUT, SUPPLIED, capture

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("boundary", ["logger", "handler", "formatter"])
def test_path_cache_types_do_not_execute_equality_hooks(boundary):
    calls = []

    class ApplicationMeta(type):
        def __eq__(cls, other):
            calls.append("type-equality")
            return False

    class ApplicationValue(metaclass=ApplicationMeta):
        pass

    def run():
        carrier = PurePosixPath(CHECKOUT)
        # These are optional cached path representations, not public request
        # data. Exercise both pathlib storage layouts without its registry.
        if hasattr(carrier, "_raw_paths"):
            carrier._raw_paths = ApplicationValue()
        else:
            carrier._parts = ApplicationValue()
        with capture() as (source, sinks):
            record = source.makeRecord(
                source.name,
                40,
                __file__,
                1,
                "metadata",
                (),
                None,
                extra={"payload": carrier, "url": CHECKOUT},
            )
            dispatch(source, sinks, record, boundary)
        observed = Counter(calls)
        calls.clear()
        return sinks, observed

    sinks, with_boundary = run()
    with native_emit():
        _, natively = run()
    assert with_boundary == natively, "the sink boundary ran a metaclass hook"
    outputs = sinks.outputs()
    # The altered cache cannot be rendered by the full-record formatter (as
    # without the boundary); the configured sinks still write their lines.
    for name in ("console-production", "file-production", "console-local"):
        assert outputs[name], name
    assert sinks.leaks(SUPPLIED) == [], "checkout session value reached a sink"
