"""Injected UTC clock, real IANA DST tables, no sleeps and no scheduling side effects."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfoNotFoundError

from deploy.update.contracts import AutomationPolicy, ContractError, MaintenanceWindowSpec, ReasonCode
from deploy.update.schedule import (
    Clock, ClockSample, check_due, jitter_seconds, next_open, occurrence,
    remaining_seconds, retry_deadline, start_gate, successful_check_deadline, validate_clock,
)


def at(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def window(start="01:00", end="03:00", zone="UTC", days=(1, 2, 3, 4, 5, 6, 7)):
    return MaintenanceWindowSpec("interval", zone, days, start, end)


class WindowTests(unittest.TestCase):
    def test_start_inclusive_end_exclusive(self):
        spec = window()
        for stamp, expected in (("2026-10-24T00:59:59Z", False), ("2026-10-24T01:00:00Z", True),
                                ("2026-10-24T02:59:59.999999Z", True), ("2026-10-24T03:00:00Z", False)):
            self.assertEqual(occurrence(spec, at(stamp)) is not None, expected)
        self.assertEqual(next_open(spec, at("2026-10-24T00:59:59Z")), at("2026-10-24T01:00:00Z"))
        now = at("2026-10-24T01:01:00.123456Z")
        self.assertEqual(next_open(spec, now), now)

    def test_equal_endpoints_not_full_day(self):
        with self.assertRaises(ContractError):
            window("00:00", "00:00")

    def test_unset_prohibits_and_always_explicit(self):
        now = at("2026-10-24T12:00:00Z")
        self.assertIsNone(occurrence(None, now))
        self.assertIsNone(next_open(None, now))
        self.assertEqual(remaining_seconds(None, now), 0)
        self.assertEqual(start_gate(AutomationPolicy(mode="apply"), now), ReasonCode.OUTSIDE_WINDOW)
        spec = MaintenanceWindowSpec("always", "UTC")
        self.assertEqual(occurrence(spec, now), "always")
        self.assertEqual(next_open(spec, now), now)
        self.assertEqual(remaining_seconds(spec, now), float("inf"))

    def test_cross_midnight_tail_uses_previous_start_day(self):
        spec = window("23:00", "02:00", days=(5,))
        friday = at("2026-10-23T23:30:00Z")
        saturday = at("2026-10-24T01:59:00Z")
        self.assertEqual(occurrence(spec, friday), occurrence(spec, saturday))
        self.assertIn("2026-10-23", occurrence(spec, saturday))
        self.assertIsNone(occurrence(spec, at("2026-10-23T01:00:00Z")))
        self.assertIsNone(occurrence(spec, at("2026-10-24T02:00:00Z")))
        self.assertEqual(next_open(spec, at("2026-10-24T03:00:00Z")), at("2026-10-30T23:00:00Z"))

    def test_leap_day_and_year_boundary(self):
        for first, tail in (("2028-02-29T23:00:00Z", "2028-03-01T01:00:00Z"),
                            ("2026-12-31T23:00:00Z", "2027-01-01T01:00:00Z")):
            start = at(first)
            spec = window("22:00", "02:00", days=(start.isoweekday(),))
            self.assertEqual(occurrence(spec, start), occurrence(spec, at(tail)))

    def test_zone_offsets_never_use_host_local_time(self):
        spec = window(zone="Asia/Kolkata")
        self.assertIsNotNone(occurrence(spec, at("2026-10-23T20:00:00Z")))
        self.assertIsNone(occurrence(spec, at("2026-10-24T01:30:00Z")))
        self.assertEqual(next_open(spec, at("2026-10-23T19:00:00Z")), at("2026-10-23T19:30:00Z"))

    def test_spring_gap_wholly_nonexistent_is_skipped(self):
        spec = window("02:10", "02:40", "America/New_York", (7,))
        now = at("2026-03-08T06:59:59Z")
        self.assertIsNone(occurrence(spec, at("2026-03-08T07:10:00Z")))
        self.assertEqual(next_open(spec, now), at("2026-03-15T06:10:00Z"))

    def test_spring_partial_window_uses_real_instants_only(self):
        spec = window("02:15", "03:30", "America/New_York", (7,))
        self.assertEqual(next_open(spec, at("2026-03-08T06:59:59Z")), at("2026-03-08T07:00:00Z"))
        self.assertEqual(remaining_seconds(spec, at("2026-03-08T07:10:00Z")), 1200)

    def test_fall_repeated_times_share_key_but_separate_contiguous_budget(self):
        spec = window("01:10", "01:40", "America/New_York", (7,))
        first, second = at("2026-11-01T05:20:00Z"), at("2026-11-01T06:20:00Z")
        self.assertEqual(occurrence(spec, first), occurrence(spec, second))
        self.assertEqual(next_open(spec, at("2026-11-01T05:45:00Z")), at("2026-11-01T06:10:00Z"))
        self.assertEqual(remaining_seconds(spec, first), 1200)
        self.assertEqual(remaining_seconds(spec, second), 1200)
        policy = AutomationPolicy(mode="apply", timezone=spec.timezone, maintenance_window=spec)
        self.assertEqual(start_gate(policy, second, consumed=occurrence(spec, first)), ReasonCode.OUTSIDE_WINDOW)

    def test_fall_broad_window_includes_both_folds(self):
        spec = window("00:30", "02:30", "America/New_York", (7,))
        self.assertEqual(remaining_seconds(spec, at("2026-11-01T04:30:00Z")), 10800)

    def test_half_hour_dst_and_skipped_calendar_date(self):
        spec = window("02:00", "02:20", "Australia/Lord_Howe", (7,))
        self.assertEqual(next_open(spec, at("2026-10-03T15:20:00Z")), at("2026-10-10T15:00:00Z"))
        apia = window("10:00", "11:00", "Pacific/Apia", (5,))
        self.assertEqual(next_open(apia, at("2011-12-30T00:00:00Z")), at("2012-01-05T20:00:00Z"))

    def test_next_open_always_obeys_predicate_for_multiple_zones_and_instants(self):
        for zone in ("UTC", "Europe/Berlin", "America/New_York", "Australia/Lord_Howe"):
            for text in ("2026-03-29T00:30:00Z", "2026-10-25T01:45:00Z", "2026-11-01T06:15:00Z"):
                spec = window("01:20", "03:00", zone, (7,))
                now = at(text)
                opening = next_open(spec, now)
                self.assertGreaterEqual(opening, now)
                self.assertIsNotNone(occurrence(spec, opening))
                if opening > now:
                    self.assertIsNone(occurrence(spec, opening - timedelta(microseconds=1)))

    def test_minimum_budget_rechecked_at_each_boundary(self):
        spec = window()
        policy = AutomationPolicy(mode="apply", maintenance_window=spec)
        self.assertIsNone(start_gate(policy, at("2026-10-24T02:45:00Z")))
        self.assertEqual(start_gate(policy, at("2026-10-24T02:45:00.000001Z")), ReasonCode.INSUFFICIENT_WINDOW)
        self.assertEqual(start_gate(policy, at("2026-10-24T02:59:00Z")), ReasonCode.INSUFFICIENT_WINDOW)
        self.assertEqual(start_gate(policy, at("2026-10-24T03:00:00Z")), ReasonCode.OUTSIDE_WINDOW)

    def test_missing_zone_data_blocks_not_silent_utc(self):
        spec = window(zone="Europe/Berlin")
        with mock.patch("deploy.update.schedule.ZoneInfo", side_effect=ZoneInfoNotFoundError):
            for operation in (occurrence, next_open, remaining_seconds):
                with self.assertRaises(ContractError) as raised:
                    operation(spec, at("2026-10-24T12:00:00Z"))
                self.assertEqual(raised.exception.reason_code, "timezone_unavailable")
            self.assertIsNotNone(occurrence(window(), at("2026-10-24T02:00:00Z")))

    def test_naive_clock_refused(self):
        for operation in (occurrence, next_open, remaining_seconds):
            with self.assertRaises(ContractError):
                operation(window(), datetime(2026, 10, 24))


class DeadlineTests(unittest.TestCase):
    def setUp(self):
        self.now = at("2026-10-24T12:00:00Z")
        self.policy = AutomationPolicy()

    def test_success_interval_jitter_stable_positive_bounded_and_persistable(self):
        for seed in ("a", "b", "installation-1"):
            deadline = successful_check_deadline(self.policy, self.now, seed)
            self.assertEqual(deadline, successful_check_deadline(self.policy, self.now, seed))
            self.assertGreaterEqual((deadline - self.now).total_seconds(), 21600)
            self.assertLessEqual((deadline - self.now).total_seconds(), 23760)
            self.assertEqual(datetime.fromisoformat(deadline.isoformat()), deadline)
        self.assertNotEqual(successful_check_deadline(self.policy, self.now, "a"),
                            successful_check_deadline(self.policy, self.now, "b"))

    def test_exponential_retry_cap_large_count_and_first_attempt(self):
        for count in (0, 1, 2, 7, 8, 9, 10, 1000000):
            deadline = retry_deadline(self.policy, self.now, "seed", count)
            base = min(86400, 300 * 2 ** min(count, 9))
            self.assertGreaterEqual((deadline.at - self.now).total_seconds(), base)
            self.assertLessEqual((deadline.at - self.now).total_seconds(), min(86400, base * 1.1))
            self.assertEqual(deadline.retry_count, count + 1)
        for bad in (-1, True, 1.5, 2**63 - 1):
            with self.assertRaises(ContractError):
                retry_deadline(self.policy, self.now, "seed", bad)

    def test_retry_after_date_reset_and_above_bound_are_conservative(self):
        for seconds in (-10, 600, 7200, 86400, 172800):
            result = retry_deadline(self.policy, self.now, "seed", 0, self.now + timedelta(seconds=seconds))
            self.assertGreaterEqual((result.at - self.now).total_seconds(), min(max(0, seconds), 86400))
            self.assertLessEqual((result.at - self.now).total_seconds(), 86400)
            self.assertEqual(result.reason_code, ReasonCode.RATE_LIMITED)

    def test_retry_deadline_replaces_discovery_interval(self):
        next_check = self.now + timedelta(hours=6)
        retry = self.now + timedelta(minutes=5)
        self.assertFalse(check_due(self.now, next_check, retry))
        self.assertTrue(check_due(retry, next_check, retry))
        self.assertFalse(check_due(retry, next_check, None))
        self.assertTrue(check_due(next_check, next_check, None))
        self.assertTrue(check_due(self.now, None, None))

    def test_forward_jump_coalesces_no_missed_work_queue(self):
        future = self.now + timedelta(days=3)
        validate_clock(ClockSample(future, 10), ClockSample(self.now, 0))
        self.assertTrue(check_due(future, self.now, None))
        new = successful_check_deadline(self.policy, future, "seed")
        self.assertGreater(new, future)
        self.assertFalse(check_due(future, new, None))

    def test_monotonic_clock_detects_backward_jump_and_future_operations(self):
        validate_clock(ClockSample(self.now - timedelta(seconds=290), 10), ClockSample(self.now, 0))
        with self.assertRaises(ContractError):
            validate_clock(ClockSample(self.now - timedelta(seconds=291), 10), ClockSample(self.now, 0))
        with self.assertRaises(ContractError):
            validate_clock(ClockSample(self.now, 0), ClockSample(self.now, 10))
        with self.assertRaises(ContractError):
            validate_clock(ClockSample(self.now, 0), observed=[self.now + timedelta(seconds=301)])
        validate_clock(ClockSample(self.now, 0), observed=[None, self.now - timedelta(days=3)])

    def test_injected_clock_and_invalid_monotonic(self):
        clock = Clock(lambda: self.now, lambda: 123.5)
        self.assertEqual(clock.sample(), ClockSample(self.now, 123.5))
        for bad in (float("nan"), float("inf"), "0"):
            with self.assertRaises(ContractError):
                ClockSample(self.now, bad)
        self.assertEqual(jitter_seconds("seed", "check", 0), 0)


if __name__ == "__main__":
    unittest.main()
