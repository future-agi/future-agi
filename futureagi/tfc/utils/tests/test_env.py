"""env_int and env_datetime: the parsers for integer and date-time environment
variables."""

import sys
from datetime import UTC, datetime, timedelta

import pytest
from django.core.exceptions import ImproperlyConfigured

from tfc.utils.env import env_datetime, env_int

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("env", [{}, {"N": ""}, {"N": "  "}])
def test_unset_or_blank_is_the_default(env):
    assert env_int("N", 7, env=env) == 7


@pytest.mark.parametrize("raw, value", [("12", 12), (" 12 ", 12), ("0", 0), ("-3", -3)])
def test_an_integer_is_read(raw, value):
    assert env_int("N", 7, env={"N": raw}) == value


@pytest.mark.parametrize("raw", ["lots", "1.5", "1e3", "0x10"])
def test_anything_else_raises_and_names_the_variable(raw):
    with pytest.raises(ImproperlyConfigured, match=r"^N must be an integer"):
        env_int("N", 7, env={"N": raw})


def test_a_minimum_is_enforced():
    assert env_int("N", 7, env={"N": "1"}, minimum=1) == 1
    for raw in ("0", "-1"):
        with pytest.raises(ImproperlyConfigured, match=r"^N must be between 1 and "):
            env_int("N", 7, env={"N": raw}, minimum=1)


def test_a_minimum_of_zero_is_enforced():
    assert env_int("N", 7, env={"N": "0"}, minimum=0) == 0
    with pytest.raises(ImproperlyConfigured, match=r"^N must be between 0 and "):
        env_int("N", 7, env={"N": "-1"}, minimum=0)


def test_a_value_beyond_64_bits_is_refused():
    # Postgres and ClickHouse take these as LIMITs and sizes: Int64 at most.
    assert env_int("N", 7, env={"N": str(sys.maxsize)}) == sys.maxsize
    for raw in (str(sys.maxsize + 1), str(-sys.maxsize - 2)):
        with pytest.raises(ImproperlyConfigured, match=r"^N must be between "):
            env_int("N", 7, env={"N": raw})


def test_reads_the_process_environment_by_default(monkeypatch):
    monkeypatch.setenv("FI_TEST_ENV_INT", "42")
    assert env_int("FI_TEST_ENV_INT", 7) == 42
    monkeypatch.delenv("FI_TEST_ENV_INT")
    assert env_int("FI_TEST_ENV_INT", 7) == 7


# env_datetime: an ISO-8601 date-time, compared against aware database times.


@pytest.mark.parametrize("env", [{}, {"T": ""}, {"T": "  "}])
def test_unset_or_blank_date_time_is_none(env):
    assert env_datetime("T", env=env) is None


@pytest.mark.parametrize(
    "raw",
    [
        "2026-10-14T12:30:00Z",
        "2026-10-14T18:00:00+05:30",
        " 2026-10-14T12:30:00+00:00 ",
    ],
)
def test_a_date_time_with_a_zone_is_the_same_instant(raw):
    assert env_datetime("T", env={"T": raw}) == datetime(
        2026, 10, 14, 12, 30, tzinfo=UTC
    )


def test_a_date_time_without_a_zone_is_utc():
    # Comparing a naive value with an aware created_at raises TypeError.
    value = env_datetime("T", env={"T": "2026-10-14T12:30:00"})
    assert value == datetime(2026, 10, 14, 12, 30, tzinfo=UTC)
    assert value.utcoffset() == timedelta(0)


@pytest.mark.parametrize("raw", ["soon", "14/10/2026 18:00", "2026-13-01T00:00:00Z"])
def test_anything_else_raises_and_names_the_date_time_variable(raw):
    with pytest.raises(ImproperlyConfigured, match=r"^T must be an ISO-8601"):
        env_datetime("T", env={"T": raw})


@pytest.mark.parametrize("raw", ["2026-10-14", "20261014", "2026-W42-3"])
def test_a_date_without_a_time_raises(raw):
    # A cutoff is an instant; a bare date would silently mean midnight UTC.
    with pytest.raises(ImproperlyConfigured, match=r"^T must be an ISO-8601"):
        env_datetime("T", env={"T": raw})
