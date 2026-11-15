"""Static admission-cache qualification; no clients or network I/O at import."""

from urllib.parse import urlsplit

from django.conf import LazySettings
from django.core.exceptions import ImproperlyConfigured

REDIS_BACKEND = "django_redis.cache.RedisCache"
REDIS_CLIENT = "django_redis.client.DefaultClient"
REDIS_FACTORY = "django_redis.pool.ConnectionFactory"


def verify_admission_cache(settings: LazySettings) -> None:
    """Reject unqualified counters before an attachments-enabled process starts.

    This deliberately supports only the deployment's standard django-redis
    writer configuration, not any backend exposing add/incr. LocMem is atomic
    only within one process; BaseCache's generic incr is not atomic at all.
    All workers must use the same Redis database, key prefix and version.
    """
    if not getattr(settings, "FI_ATTACHMENTS_ENABLED", False):
        return
    config = settings.CACHES.get("default", {})
    options = config.get("OPTIONS", {})
    location = config.get("LOCATION")
    try:
        url = urlsplit(location) if isinstance(location, str) else None
    except ValueError:
        url = None
    supported = (
        config.get("BACKEND") == REDIS_BACKEND
        and isinstance(location, str)
        and location.strip() == location
        and "," not in location
        and url is not None
        and url.scheme in {"redis", "rediss"}
        and bool(url.hostname)
        and options.get("CLIENT_CLASS", REDIS_CLIENT) == REDIS_CLIENT
        and not options.get(
            "IGNORE_EXCEPTIONS",
            getattr(settings, "DJANGO_REDIS_IGNORE_EXCEPTIONS", False),
        )
        and getattr(settings, "DJANGO_REDIS_CONNECTION_FACTORY", REDIS_FACTORY)
        == REDIS_FACTORY
        and "KEY_FUNCTION" not in config
        and options.keys()
        <= {
            "CLIENT_CLASS",
            "IGNORE_EXCEPTIONS",
            "SOCKET_CONNECT_TIMEOUT",
            "SOCKET_TIMEOUT",
            "PASSWORD",
        }
    )
    if not supported:
        # Do not include LOCATION/options: they can contain credentials.
        raise ImproperlyConfigured(
            "FI_ATTACHMENTS_ENABLED requires a shared atomic admission cache: "
            "CACHES['default'] must use django_redis.cache.RedisCache with "
            "DefaultClient, one Redis writer URL, the standard connection "
            "factory/key function and no ignored exceptions or custom options. "
            "All workers must share its database, KEY_PREFIX and VERSION. "
            "LocMem, Dummy and other cache implementations are unsupported."
        )
