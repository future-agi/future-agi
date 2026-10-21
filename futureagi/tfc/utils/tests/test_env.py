"""env_int: the one parser for integer environment variables."""

import sys

import pytest
from django.core.exceptions import ImproperlyConfigured

from tfc.utils.env import env_int

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
