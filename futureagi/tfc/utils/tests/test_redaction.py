import pytest

from tfc.utils.redaction import redact_secret, redact_url_credentials


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("redis://:s3cret@redis:6379/2", "redis://:***@redis:6379/2"),
        ("rediss://default:s3cret@redis:6380/0", "rediss://default:***@redis:6380/0"),
        ("amqp://user:p%40ss:w0rd@rabbitmq:5672//", "amqp://user:***@rabbitmq:5672//"),
        (
            "connected to redis://:a@redis:6379/2 and postgres://u:b@db/x",
            "connected to redis://:***@redis:6379/2 and postgres://u:***@db/x",
        ),
        # An unencoded "@" in the password: parsers split on the last "@".
        ("redis://:p@ss@futureagi-redis:6379/2", "redis://:***@futureagi-redis:6379/2"),
        ("redis://default:p@ss:w@rd@host:6379/0", "redis://default:***@host:6379/0"),
        # The password as a query parameter (redis-py and ClickHouse accept it).
        ("redis://redis:6379/2?password=s3cret", "redis://redis:6379/2?password=***"),
        (
            "http://clickhouse:8123/?user=default&password=s3cret&query=x",
            "http://clickhouse:8123/?user=default&password=***&query=x",
        ),
    ],
)
def test_masks_the_password(raw, expected):
    assert redact_url_credentials(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "redis://redis:6379/0",
        "amqp://guest@rabbitmq:5672//",
        "http://clickhouse:8123/?query=a@b",
        "https://example.com:443/users/someone@example.com",
        "no url here: user:pass@host",
    ],
)
def test_leaves_urls_without_a_password_alone(raw):
    assert redact_url_credentials(raw) == raw


def test_redact_secret_hides_the_raw_and_sql_escaped_forms():
    secret = "p'a\\ss\"word"
    escaped = "p\\'a\\\\ss\"word"  # inside a ClickHouse string literal
    text = f"failed near PASSWORD '{escaped}' (raw {secret})"
    redacted = redact_secret(text, secret)
    assert redacted == "failed near PASSWORD '[HIDDEN]' (raw [HIDDEN])"


def test_redact_secret_without_a_secret_changes_nothing():
    assert redact_secret("PASSWORD ''", "") == "PASSWORD ''"
