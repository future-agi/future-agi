"""Hermetic connection aliases for the generated, loopback-only acceptance stack.

This is fixture configuration, not production defaults. Validate the actual
Django/CDC/client settings before any connection or migration, including when
someone accidentally imports the harness into an already-configured process.
"""

import os

HOST = "127.0.0.1"
PG_DATABASE = "cluster_acceptance"
CH_DATABASE = "acceptance_v1"
CATALOG_DATABASE = "acceptance_catalog"

# Empty libpq service/file values are NOT equivalent to absence (libpq will
# attempt to open a service file named ""). Optional overrides must be removed.
ABSENT_LIBPQ_ENV = frozenset(
    {
        "PGSERVICE",
        "PGSERVICEFILE",
        "PGSYSCONFDIR",
        "PGPASSFILE",
        "PGOPTIONS",
        "PGSSLROOTCERT",
        "PGSSLCERT",
        "PGSSLKEY",
        "PGSSLCRL",
        "PGSSLCRLDIR",
        "PGSSLCERTMODE",
        "PGSSLMINPROTOCOLVERSION",
        "PGSSLMAXPROTOCOLVERSION",
        "PGSSLNEGOTIATION",
        "PGSSLSNI",
        "PGCHANNELBINDING",
        "PGREQUIREAUTH",
        "PGREQUIREPEER",
        "PGAPPNAME",
    }
)


def environment(fixture):
    port = int(fixture["port"])
    if not 1024 <= port <= 65527 or any(
        int(fixture[key]) != port + offset
        for key, offset in (("pg_port", 6), ("redis_port", 7), ("native_port", 8))
    ):
        raise ValueError("application fixture must use its generated nine-port layout")
    password = fixture["password"]
    if not isinstance(password, str) or not password:
        raise ValueError("acceptance fixture requires an explicit nonempty password")

    def redis(database):
        return f"redis://{HOST}:{fixture['redis_port']}/{database}"

    result = {
        "DJANGO_SETTINGS_MODULE": "tfc.settings.settings",
        "PYTHON_DOTENV_DISABLED": "1",
        "LITELLM_LOCAL_MODEL_COST_MAP": "true",
        "ENV_TYPE": "local",
        "CLOUD_DEPLOYMENT": "",
        "EE_LICENSE_KEY": "",
        "FI_SKIP_CH25_MIGRATION": "1",
        "FI_CDC_MODE": "outbox",
        "NO_STARTUP_DB_MUTATIONS": "true",
        "READ_REPLICA_OPT_IN": "",
        "CH_USE_REPLICATED_ENGINES": "false",
        "CH_ENABLED": "true",
        "CH_DUAL_WRITE": "false",
        "CH_SERVER_ENFORCED_READONLY": "false",
        "CH25_SERVER_ENFORCED_READONLY": "false",
        "FUTURE_AGI_TELEMETRY_DISABLED": "true",
        "OTEL_ENABLED": "false",
        "SENTRY_DSN": "",
        "USAGE_EVENTS_ENABLED": "false",
        "CELERY_BROKER_URL": "memory://",
        "TEMPORAL_HOST": f"{HOST}:9",
        "TEMPORAL_NAMESPACE": "cluster_acceptance",
        "PEERDB_FLOW_SERVER_HTTP": f"http://{HOST}:9",
        "PG_MIGRATION_LOCK_TIMEOUT_MS": "20000",
        "PGSSLMODE": "disable",
        # libpq's standard variables must not redirect even explicit host/user
        # connection parameters through an ambient service/hostaddr definition.
        "PGHOST": HOST,
        "PGHOSTADDR": HOST,
        "PGPORT": str(fixture["pg_port"]),
        "PGDATABASE": PG_DATABASE,
        "PGUSER": "test",
        "PGPASSWORD": password,
        "PGGSSENCMODE": "disable",
        "PGTARGETSESSIONATTRS": "any",
        "PGCLIENTENCODING": "UTF8",
        "PGTZ": "UTC",
        "CH_HTTP_PORT": str(port),
        "CH_PORT": str(fixture["native_port"]),
        "CH25_HTTP_PORT": str(port),
        "CH25_TCP_PORT": str(fixture["native_port"]),
        "DST_CH_PORT": str(fixture["native_port"]),
        "FI_CH_URL": f"http://{HOST}:{port}",
        "FI_CH_USERNAME": "default",
        "FI_CH_PASSWORD": password,
        "CH_CLUSTER_ENABLED": "true",
        "CH_CLUSTER_NAME": "acceptance",
        "CH_CLUSTER_KEEPER_PREFIX": "/acceptance",
        "CH_CLUSTER_SHARDS": "3",
        "CH_CLUSTER_REPLICAS": "2",
        "CH_CLUSTER_INSERT_QUORUM": "2",
        "PROPERTY_CATALOG_DATABASE": CATALOG_DATABASE,
        "PROPERTY_CATALOG_CH_HOST": HOST,
        "PROPERTY_CATALOG_CH_PORT": str(fixture["native_port"]),
        "PROPERTY_CATALOG_CH_USER": "observed_catalog_reader",
        "PROPERTY_CATALOG_CONSUMER_PASSWORD": password + "writer",
        "PROPERTY_CATALOG_CH_PASSWORD": password + "reader",
        "REDIS_HOST": HOST,
        "REDIS_PORT": str(fixture["redis_port"]),
        "REDIS_USERNAME": "",
        "REDIS_PASSWORD": "",
        "REDIS_URL": redis(0),
        "REDIS_CACHE_URL": redis(1),
        "REDIS_LOCK_URL": redis(2),
        "REDIS_STATE_URL": redis(2),
        "DJANGO_CACHE_BACKEND": "redis",
        "CHANNEL_LAYER_BACKEND": "redis",
        "CHANNEL_REDIS_URL": redis(3),
        "CHANNEL_LAYER_AMQP_URL": "",
        "GRANIAN_WORKERS": "1",
        "HTTP_PROXY": "",
        "HTTPS_PROXY": "",
        "ALL_PROXY": "",
        "http_proxy": "",
        "https_proxy": "",
        "all_proxy": "",
        "NO_PROXY": "127.0.0.1,localhost,::1",
        "no_proxy": "127.0.0.1,localhost,::1",
    }
    for prefix in ("PG", "PGBOUNCER", "PGBOUNCER_READ", "PG_DIRECT", "SRC_PG"):
        result[f"{prefix}_HOST"] = HOST
        result[f"{prefix}_PORT"] = str(fixture["pg_port"])
    for key in ("PG_DB", "PG_READ_DB", "SRC_PG_DB"):
        result[key] = PG_DATABASE
    for prefix in ("PG", "SRC_PG"):
        result[f"{prefix}_USER"] = "test"
        result[f"{prefix}_PASSWORD"] = password
    for key in ("CH_HOST", "CH25_HOST", "DST_CH_HOST"):
        result[key] = HOST
    for key in ("CH_USER", "CH_USERNAME", "CH25_USER", "DST_CH_USER"):
        result[key] = "default"
    for key in ("CH_PASSWORD", "CH25_PASSWORD", "DST_CH_PASSWORD"):
        result[key] = password
    for key in ("CH_DATABASE", "CH25_DATABASE", "DST_CH_DB", "FI_CH_DATABASE"):
        result[key] = CH_DATABASE
    return result


def configure(fixture):
    for key in ABSENT_LIBPQ_ENV:
        os.environ.pop(key, None)
    os.environ.update(environment(fixture))


def verify_libpq_environment():
    if ABSENT_LIBPQ_ENV.intersection(os.environ):
        raise RuntimeError("acceptance fixture optional libpq overrides must be absent")


def verify(fixture):
    verify_libpq_environment()
    from django.conf import settings
    from django.db import connections

    def require(condition, label):
        if not condition:
            # Values can contain credentials; report only the mismatching path.
            raise RuntimeError(f"acceptance fixture connection mismatch: {label}")

    expected = environment(fixture)
    connection_keys = {
        key
        for key in expected
        if key.startswith(
            (
                "PG",
                "SRC_PG",
                "DST_CH",
                "CH_",
                "CH25_",
                "FI_CH_",
                "PROPERTY_CATALOG_",
                "REDIS_",
                "CHANNEL_REDIS_",
            )
        )
    }
    connection_keys.add("PYTHON_DOTENV_DISABLED")
    require(
        all(os.environ.get(key) == expected[key] for key in connection_keys),
        "environment aliases changed",
    )
    databases = connections.databases  # resolves Django aliases, opens no sockets
    require(
        set(databases) == {"default", "default_direct", "replica"},
        "Django database aliases",
    )
    for alias, config in databases.items():
        require(
            config["ENGINE"] == "django.db.backends.postgresql"
            and config["HOST"] == HOST
            and int(config["PORT"]) == fixture["pg_port"]
            and config["NAME"] == PG_DATABASE
            and config["USER"] == "test"
            and config["PASSWORD"] == fixture["password"],
            f"DATABASES.{alias}",
        )
        require(
            set(config.get("OPTIONS", {})) <= {"connect_timeout", "options"},
            f"DATABASES.{alias}.OPTIONS",
        )
    from tracer.services.clickhouse.oss_cdc_install import Config
    from tracer.services.clickhouse.v2 import get_v2_config

    legacy = settings.CLICKHOUSE
    require(
        legacy["CH_HOST"] == HOST
        and int(legacy["CH_PORT"]) == fixture["native_port"]
        and legacy["CH_DATABASE"] == CH_DATABASE
        and legacy["CH_USERNAME"] == "default"
        and legacy["CH_PASSWORD"] == fixture["password"],
        "CLICKHOUSE",
    )
    v2 = get_v2_config()
    require(
        v2["host"] == HOST
        and v2["http_port"] == fixture["port"]
        and v2["tcp_port"] == fixture["native_port"]
        and v2["database"] == CH_DATABASE
        and v2["user"] == "default"
        and v2["password"] == fixture["password"],
        "CLICKHOUSE_V2",
    )
    require(
        settings.PROPERTY_CATALOG_DATABASE == CATALOG_DATABASE
        and settings.PROPERTY_CATALOG_CH_HOST == HOST
        and settings.PROPERTY_CATALOG_CH_PORT == fixture["native_port"]
        and settings.PROPERTY_CATALOG_CH_USER == "observed_catalog_reader"
        and settings.PROPERTY_CATALOG_CH_PASSWORD == fixture["password"] + "reader",
        "PROPERTY_CATALOG",
    )
    require(settings.REDIS_URL == expected["REDIS_URL"], "REDIS_URL")
    require(
        settings.CACHES["default"]["BACKEND"] == "django_redis.cache.RedisCache"
        and settings.CACHES["default"]["LOCATION"] == expected["REDIS_CACHE_URL"],
        "CACHES.default",
    )
    require(
        settings.CHANNEL_LAYER_BACKEND == "redis"
        and settings.CHANNEL_LAYERS["default"]["CONFIG"]["hosts"]
        == [expected["CHANNEL_REDIS_URL"]],
        "CHANNEL_LAYERS",
    )
    cdc = Config.from_env(os.environ)
    require(
        cdc.source.host == HOST
        and cdc.source.port == fixture["pg_port"]
        and cdc.source.database == PG_DATABASE
        and cdc.pg_user == "test"
        and cdc.pg_password == fixture["password"],
        "CDC source",
    )
    require(
        cdc.destination.host == HOST
        and cdc.destination.port == fixture["native_port"]
        and cdc.destination.database == CH_DATABASE
        and cdc.http_port == fixture["port"]
        and cdc.ch_user == "default"
        and cdc.ch_password == fixture["password"]
        and not cdc.hosted,
        "CDC destination",
    )


def probe_postgres(*, port, database, user, password):
    """Real libpq handshake and identity check; constant read-only SQL only."""
    verify_libpq_environment()
    import psycopg

    with psycopg.connect(
        host=HOST,
        port=port,
        dbname=database,
        user=user,
        password=password,
        connect_timeout=2,
        autocommit=True,
        options="-c default_transaction_read_only=on -c statement_timeout=5000",
    ) as connection:
        identity = connection.execute(
            "SELECT current_database(), current_user"
        ).fetchone()
        if (
            identity != (database, user)
            or connection.info.host != HOST
            or int(connection.info.port) != port
        ):
            raise RuntimeError("acceptance PostgreSQL connection identity mismatch")


def probe_django_postgres(fixture):
    """Exercise every resolved alias after startup, before running migrations."""
    verify(fixture)
    from django.db import connections

    checked = []
    for alias in sorted(connections):
        connection = connections[alias]
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT current_database(), current_user")
                if cursor.fetchone() != (PG_DATABASE, "test"):
                    raise RuntimeError("acceptance Django connection identity mismatch")
            checked.append(alias)
        finally:
            connection.close()
    return checked
