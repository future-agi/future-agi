"""Tests for bench_guard.py: the allocation budget and the unpinned benchmarks.

Run: python3 -I agentcc-gateway/scripts/scenarios/gates/lib/bench_guard_test.py
Standard library only.
"""

import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("bench_guard", os.path.join(_HERE, "bench_guard.py"))
bench_guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bench_guard)


def bench_lines(name, allocs, samples=12, ns=1000):
    """Go's output for one benchmark: the name line, then one result line per sample."""
    return "".join(
        f"{name}-4\n{name}-4   \t    1000\t   {ns + i % 3} ns/op\t  5000 B/op\t     {allocs} allocs/op\n"
        for i in range(samples)
    )


class BenchGuardTest(unittest.TestCase):
    def run_guard(self, pin, head, *args):
        with tempfile.TemporaryDirectory() as d:
            pin_path, head_path = os.path.join(d, "pin.txt"), os.path.join(d, "head.txt")
            with open(pin_path, "w") as fh:
                fh.write(pin)
            with open(head_path, "w") as fh:
                fh.write(head)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = bench_guard.cli(list(args) + [pin_path, head_path])
            return code, out.getvalue()

    def test_allocs_within_the_budget_pass_and_one_more_fails(self):
        pin = bench_lines("BenchmarkChatCompletionNonStreaming", 303)
        code, out = self.run_guard(pin, bench_lines("BenchmarkChatCompletionNonStreaming", 310), "--allocs-budget", "7")
        self.assertEqual(code, 0, out)
        code, out = self.run_guard(pin, bench_lines("BenchmarkChatCompletionNonStreaming", 311), "--allocs-budget", "7")
        self.assertEqual(code, 1, out)

    def test_a_head_only_benchmark_named_unpinned_is_reported_not_judged(self):
        pin = bench_lines("BenchmarkChatCompletionNonStreaming", 303)
        head = bench_lines("BenchmarkChatCompletionNonStreaming", 310) + bench_lines("BenchmarkChatCompletionOwnKey", 317)
        code, out = self.run_guard(pin, head, "--allocs-budget", "7", "--unpinned", "BenchmarkChatCompletionOwnKey")
        self.assertEqual(code, 0, out)
        self.assertIn("BenchmarkChatCompletionOwnKey-4: unpinned", out)
        self.assertIn("head median 317", out)
        self.assertIn("bench_guard: PASS", out)

    def test_a_head_only_benchmark_not_named_unpinned_is_still_bad_input(self):
        pin = bench_lines("BenchmarkChatCompletionNonStreaming", 303)
        head = bench_lines("BenchmarkChatCompletionNonStreaming", 303) + bench_lines("BenchmarkChatCompletionOwnKey", 317)
        code, out = self.run_guard(pin, head, "--allocs-budget", "7")
        self.assertEqual(code, 2, out)
        code, out = self.run_guard(pin, head, "--allocs-budget", "7", "--unpinned", "BenchmarkSomethingElse")
        self.assertEqual(code, 2, out)

    def test_a_benchmark_missing_from_the_head_is_bad_input_even_when_named(self):
        pin = bench_lines("BenchmarkChatCompletionNonStreaming", 303) + bench_lines("BenchmarkChatCompletionOwnKey", 317)
        head = bench_lines("BenchmarkChatCompletionNonStreaming", 303)
        code, out = self.run_guard(pin, head, "--unpinned", "BenchmarkChatCompletionOwnKey")
        self.assertEqual(code, 2, out)

    def test_an_unpinned_benchmark_the_pin_has_is_judged(self):
        pin = bench_lines("BenchmarkChatCompletionNonStreaming", 303) + bench_lines("BenchmarkChatCompletionOwnKey", 310)
        head = bench_lines("BenchmarkChatCompletionNonStreaming", 303) + bench_lines("BenchmarkChatCompletionOwnKey", 318)
        code, out = self.run_guard(pin, head, "--allocs-budget", "7", "--unpinned", "BenchmarkChatCompletionOwnKey")
        self.assertEqual(code, 1, out)

    def test_too_few_samples_of_a_judged_benchmark_is_bad_input(self):
        pin = bench_lines("BenchmarkChatCompletionNonStreaming", 303, samples=4)
        head = bench_lines("BenchmarkChatCompletionNonStreaming", 303)
        code, out = self.run_guard(pin, head)
        self.assertEqual(code, 2, out)

    def test_nothing_judged_is_bad_input(self):
        head = bench_lines("BenchmarkChatCompletionOwnKey", 317)
        code, out = self.run_guard("", head, "--unpinned", "BenchmarkChatCompletionOwnKey")
        self.assertEqual(code, 2, out)

    def test_bad_arguments_are_refused(self):
        for args in (["--allocs-budget", "x"], ["--unpinned"], ["--allocs-budget", "-1"], ["--what", "1"]):
            code, _ = self.run_guard("", "", *args)
            self.assertEqual(code, 2, args)


if __name__ == "__main__":
    unittest.main()
