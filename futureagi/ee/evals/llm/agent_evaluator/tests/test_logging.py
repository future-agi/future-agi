"""Tests for ``context/logging.safe_info`` / ``safe_warning``.

These shims must NEVER propagate an exception — even if the underlying
structlog logger itself raises (handler IO error, processor crash). The
defense pipeline depends on this: a logger blowing up must not abort
an eval.
"""

from unittest.mock import patch

from ee.evals.llm.agent_evaluator.context import logging as L


def test_safe_info_normal_call():
    # Just verify it does not raise on a happy path
    L.safe_info("my_event", key="value", count=1)


def test_safe_warning_normal_call():
    L.safe_warning("my_event", key="value", count=1)


def test_safe_info_swallows_logger_exception():
    with patch.object(L.logger, "info", side_effect=RuntimeError("logger exploded")):
        # MUST NOT raise
        L.safe_info("my_event", key="value")


def test_safe_warning_swallows_logger_exception():
    with patch.object(L.logger, "warning", side_effect=RuntimeError("logger exploded")):
        L.safe_warning("my_event", key="value")


def test_safe_info_swallows_typeerror_in_processor():
    # Pass an un-serializable object — logger might choke on it
    class Weird:
        def __repr__(self):
            raise RuntimeError("repr explodes")

    L.safe_info("evt", weird=Weird())  # MUST NOT raise


def test_safe_warning_swallows_typeerror_in_processor():
    class Weird:
        def __repr__(self):
            raise RuntimeError("repr explodes")

    L.safe_warning("evt", weird=Weird())  # MUST NOT raise
