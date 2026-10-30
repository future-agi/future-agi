import secrets

from django.core import signing
from django.core.cache import cache

StateData = dict[str, str | None]


def issue_state(
    data: StateData, *, salt: str, cache_prefix: str, ttl_seconds: int, label: str
) -> str:
    state_data: StateData = {"nonce": secrets.token_urlsafe(32), **data}
    if not cache.add(
        f"{cache_prefix}:{state_data['nonce']}", state_data, timeout=ttl_seconds
    ):
        raise ValueError(f"Could not start {label} installation. Please try again.")
    return signing.dumps(state_data, salt=salt)


def consume_state(
    state: str, *, salt: str, cache_prefix: str, ttl_seconds: int, label: str
) -> StateData:
    try:
        state_data = signing.loads(state, salt=salt, max_age=ttl_seconds)
    except signing.BadSignature as exc:
        raise ValueError(f"Invalid or expired {label} installation state.") from exc
    key = f"{cache_prefix}:{state_data.get('nonce', '')}"
    stored = cache.get(key)
    if not stored or stored != state_data:
        raise ValueError(f"{label} installation state has expired or was already used.")
    if not cache.add(f"{key}:used", True, timeout=ttl_seconds):
        raise ValueError(f"{label} installation state was already used.")
    cache.delete(key)
    return state_data
