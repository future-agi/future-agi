"""Observations preserve arguments/results/errors and never retain row contents."""

import unittest
from pathlib import Path
from unittest.mock import Mock

from operation_profile import OperationProfile


class OperationProfileTests(unittest.TestCase):
    def test_original_result_and_exception_are_unchanged(self):
        value = object()
        failure = ValueError("original failure")
        original = Mock(side_effect=[value, failure])

        class Subject:
            def work(self, *args, **kwargs):
                return original(*args, **kwargs)

        clock = Mock(side_effect=[0, 0, 3, 3, 3, 5, 5])
        write = Mock()
        profile = OperationProfile(
            Path("/unused/profile.json"), clock=clock, write=write
        )
        profile.wrap(Subject, "work", "operation")
        subject = Subject()
        self.assertIs(subject.work("opaque", secret="not captured"), value)
        with self.assertRaises(ValueError) as raised:
            subject.work("second")
        self.assertIs(raised.exception, failure)
        self.assertEqual(original.call_count, 2)
        original.assert_any_call("opaque", secret="not captured")
        self.assertEqual(
            profile.counts["operation"],
            {
                "calls": 2,
                "errors": 1,
                "seconds": 5.0,
                "max_seconds": 3,
            },
        )
        self.assertEqual(write.call_count, 2)
        self.assertNotIn("not captured", repr(write.call_args))

    def test_diagnostic_write_failure_cannot_replace_original_failure(self):
        failure = RuntimeError("original")

        class Subject:
            def work(self):
                raise failure

        profile = OperationProfile(
            Path("/unused/profile.json"),
            clock=Mock(side_effect=[0, 0, 3, 3]),
            write=Mock(side_effect=OSError("diagnostic only")),
        )
        profile.wrap(Subject, "work", "operation")
        with self.assertRaises(RuntimeError) as raised:
            Subject().work()
        self.assertIs(raised.exception, failure)
        with self.assertRaises(ValueError):
            profile.wrap(Subject, "work", "operation")


if __name__ == "__main__":
    unittest.main()
