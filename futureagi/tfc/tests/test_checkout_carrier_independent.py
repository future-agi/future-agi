"""Independent carrier inventory across record fields and dispatch boundaries.

This table is not derived from the implementation registry. Each concrete
standard type must render its stored URL and keep its original storage intact.
Each scenario is observed in what the configured sinks emit (checkout_sinks),
not in the in-memory record.
"""

import io
from argparse import Namespace
from collections import Counter
from contextlib import contextmanager
from email.header import Header

import httpx
import pytest
from django.core.files.base import File
from starlette.datastructures import UploadFile

from tfc.tests.checkout_sinks import RecordFormatter, configured_sinks, native_emit
from tfc.tests.test_checkout_context_boundaries import VALUE, dispatch, path

pytestmark = pytest.mark.integration


class StackRecordFormatter(RecordFormatter):
    """The full-record sink, with an extensible formatStack for a dict stack."""

    def formatStack(self, stack_info):
        # stack_info is normally text, but formatStack is extensible.
        if isinstance(stack_info, dict):
            return str(stack_info["target"])
        return super().formatStack(stack_info)


@contextmanager
def capture():
    with configured_sinks(
        "independent.further.boundaries", extra={"record": StackRecordFormatter()}
    ) as (source, sinks):
        yield source, sinks


FACTORIES = {
    "namespace": (Namespace, lambda cls, p: cls(target=p)),
    "header": (Header, lambda cls, p: cls(p)),
    "file": (File, lambda cls, p: cls(io.BytesIO(b"body"), name=p)),
    "proxy": (httpx.Proxy, lambda cls, p: cls("http://localhost" + p)),
    "upload": (UploadFile, lambda cls, p: cls(io.BytesIO(b"body"), filename=p)),
}


@pytest.mark.parametrize("name", sorted(FACTORIES))
@pytest.mark.parametrize(
    "placement", ["msg", "args", "extra", "exc_info", "stack_info"]
)
@pytest.mark.parametrize("boundary", ["logger", "handler", "formatter"])
@pytest.mark.parametrize("checkout", [True, False])
def test_independent_storage_fields(name, placement, boundary, checkout):
    cls, build = FACTORIES[name]
    carrier = build(cls, path(checkout))
    before = str(carrier)
    assert VALUE in before
    buffer = carrier.file if name in {"file", "upload"} else None
    with capture() as (source, sinks):
        record = source.makeRecord(source.name, 40, __file__, 1, "metadata", (), None)
        if placement == "msg":
            record.msg = carrier
        elif placement == "args":
            record.msg, record.args = "metadata %s", (carrier,)
        elif placement == "extra":
            record.payload = carrier
        elif placement == "exc_info":
            error = ValueError(carrier)
            record.exc_info = (ValueError, error, None)
        else:
            # stack_info is normally text, but formatStack is extensible.
            record.stack_info = {"target": carrier}
        if placement == "stack_info" and boundary == "formatter":
            # The scenario's own formatter must handle the dict stack too.
            StackRecordFormatter().format(record)
            sinks.emit(record)
        else:
            dispatch(source, sinks, record, boundary)
    assert str(carrier) == before
    if buffer is not None:
        assert buffer.tell() == 0
    assert sinks.emitted(), "a configured sink wrote nothing"
    if checkout:
        assert sinks.leaks(VALUE) == [], "checkout session value reached a sink"
        assert sinks.structured(), "a production line is not one JSON document"
    else:
        assert VALUE in sinks.outputs()["record"]


HOOKED = ("__getattribute__", "__repr__", "__str__", "__iter__", "__bool__")


@pytest.mark.parametrize("name", sorted(FACTORIES))
def test_sink_boundary_adds_no_subclass_hook_calls(name):
    base, build = FACTORIES[name]
    hooks = []

    class Derived(base):
        pass

    def install():
        for method in HOOKED:
            original = getattr(base, method, None)
            if original is None:
                continue

            def counted(self, *args, _original=original, _method=method, **kwargs):
                hooks.append(_method)
                return _original(self, *args, **kwargs)

            setattr(Derived, method, counted)

    def remove():
        for method in HOOKED:
            if method in vars(Derived):
                delattr(Derived, method)

    def run():
        carrier = build(Derived, path(True))
        # Add hooks after construction, as the original scenario did.
        install()
        try:
            with capture() as (source, sinks):
                source.error("metadata", extra={"payload": carrier})
        finally:
            remove()
        observed = Counter(hooks)
        hooks.clear()
        return carrier, sinks, observed

    carrier, sinks, with_boundary = run()
    with native_emit():
        _, _, natively = run()
    assert with_boundary == natively, "the sink boundary ran a carrier hook"
    assert sinks.emitted(), "a configured sink wrote nothing"
    assert sinks.leaks(VALUE) == [], "checkout session value reached a sink"
    # Records are not rewritten: the application's object is delivered as is.
    assert sinks.records[-1].payload is carrier
