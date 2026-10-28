"""Fixed standalone-runtime-v2 probe. Source files are DATA, never imported.

Delivered with the reviewed migration-apps.json as fixed host-owned data. Run
with -I -B -S, entrypoint bypass, RO root/data and a verified app network. No
Django setup, migration execution, image descriptors or release-supplied SQL.
"""
import ast
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sys
import sysconfig
import urllib.parse
import urllib.request

PERSISTENCE = {"postgres_app": True, "clickhouse": True, "temporal": True,
               "object_store": True, "redis": False, "shared_files": True}
LIMIT = 4 * 1024 * 1024
MAX_MIGRATIONS = 10000
MAX_TABLES = 256
TABLE = re.compile(r"[a-z_][a-z0-9_]{0,127}\.[a-z_][a-z0-9_]{0,127}\Z")


class ProbeError(ValueError):
    pass


def require(value, reason="initialization_required"):
    if not value:
        raise ProbeError(reason)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def read(path, limit=LIMIT):
    path = Path(path)
    # A symlink at ANY ancestor could redirect a fixed path into writable data.
    require(not any(parent.is_symlink() for parent in (path, *path.parents)),
            "migration_inventory_unsupported")
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        import stat
        require(stat.S_ISREG(os.fstat(fd).st_mode), "migration_inventory_unsupported")
        with os.fdopen(os.dup(fd), "rb") as handle:
            raw = handle.read(limit + 1)
        require(len(raw) <= limit, "migration_inventory_unsupported")
        return raw
    finally:
        os.close(fd)


def validate_mapping(mapping):
    require(type(mapping) is dict and set(mapping) == {
        "schema_version", "image_backend_root", "library_root", "supported_settings",
        "oss_environment", "unsupported_conditionals", "apps"}, "migration_inventory_unsupported")
    require(type(mapping["schema_version"]) is int and mapping["schema_version"] == 1
            and mapping["image_backend_root"] == "/app/backend"
            and mapping["library_root"] == "sysconfig.purelib"
            and mapping["supported_settings"] == "tfc.settings.settings"
            and type(mapping["apps"]) is list and 0 < len(mapping["apps"]) <= 128,
            "migration_inventory_unsupported")
    labels, modules = set(), set()
    for app in mapping["apps"]:
        require(type(app) is dict and set(app) == {"label", "module", "source_root", "location", "condition", "migrations"},
                "migration_inventory_unsupported")
        require(type(app["label"]) is str and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", app["label"])
                and type(app["module"]) is str and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", app["module"])
                and app["source_root"] == app["module"].replace(".", "/")
                and app["location"] in ("backend", "python_purelib")
                and app["condition"] in ("always", "if_present", "unsupported_ee")
                and type(app["migrations"]) is bool
                and app["label"] not in labels and app["module"] not in modules,
                "migration_inventory_unsupported")
        labels.add(app["label"])
        modules.add(app["module"])
    return mapping


def _assigned_name(node, name):
    return (isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)) and
            any(isinstance(target, ast.Name) and target.id == name
                for target in (node.targets if isinstance(node, ast.Assign) else [node.target])))


def _literal(node):
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        raise ProbeError("migration_inventory_unsupported") from None


def _app_module(value, by_module):
    require(type(value) is str, "migration_inventory_unsupported")
    matches = [key for key in by_module if value == key or value.startswith(key + ".apps.")]
    require(len(matches) == 1, "migration_inventory_unsupported")
    return matches[0]


def active_apps(mapping, backend_root=None, library_root=None):
    """Positively recognize the supported settings registry without executing it.

    Producer cross-check API: compare these labels/modules with Django's REAL
    registry and MigrationLoader in the disposable producer, never on a host.
    Optional roots only implement the reviewed has_ee(presence) OSS conditions.
    """
    validate_mapping(mapping)
    backend = Path(backend_root or mapping["image_backend_root"])
    library = Path(library_root or sysconfig.get_path("purelib"))
    by_module = {row["module"]: row for row in mapping["apps"]}
    settings = backend / "tfc/settings/settings.py"
    tree = ast.parse(read(settings, 2 * 1024 * 1024))
    # A custom override anywhere in this settings package is unsupported. Do not
    # execute settings imports or trust an image-owned inventory/marker instead.
    settings_files = sorted(settings.parent.glob("*.py"))
    require(len(settings_files) <= 64, "migration_inventory_unsupported")
    for path in settings_files:
        parsed = ast.parse(read(path, 2 * 1024 * 1024))
        require(not any(_assigned_name(node, "MIGRATION_MODULES") for node in ast.walk(parsed)),
                "migration_inventory_unsupported")
    assignments = [node for node in tree.body if _assigned_name(node, "INSTALLED_APPS")]
    require(len(assignments) == 1 and isinstance(assignments[0], ast.Assign), "migration_inventory_unsupported")
    base = _literal(assignments[0].value)
    require(type(base) is list and len(base) <= 128, "migration_inventory_unsupported")
    active = [_app_module(value, by_module) for value in base]
    handled = {id(assignments[0])}
    conditional_modules = {
        "ee.usage": 'has_ee("ee.usage")',
        "ee.licensing": 'has_ee("ee.licensing")',
        "ee.falcon_ai": 'ee_feature_enabled("ee.falcon_ai")',
        "ee.cloud.control_plane": 'has_ee("ee.cloud.control_plane") and is_cloud_env()',
    }
    for node in tree.body:
        if not isinstance(node, ast.If) or node.orelse or len(node.body) != 1:
            continue
        stmt = node.body[0]
        if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
            continue
        call = stmt.value
        if not (isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "INSTALLED_APPS" and call.func.attr == "append"):
            continue
        require(len(call.args) == 1 and not call.keywords, "migration_inventory_unsupported")
        value = _literal(call.args[0])
        module = value if value == "ee.cloud.control_plane.apps.CloudControlPlaneConfig" else _app_module(value, by_module)
        if module.endswith(".apps.CloudControlPlaneConfig"):
            module = "ee.cloud.control_plane"
        expected = conditional_modules.get(module)
        require(expected is not None and ast.dump(node.test) == ast.dump(ast.parse(expected, mode="eval").body),
                "migration_inventory_unsupported")
        handled.add(id(call))
        if module in ("ee.usage", "ee.licensing"):
            require(by_module[module]["condition"] == "if_present", "migration_inventory_unsupported")
            if (backend / by_module[module]["source_root"]).exists():
                active.append(module)
        else:
            # Both are inactive only in the positively checked OSS environment.
            require(not os.environ.get("EE_LICENSE_KEY") and not os.environ.get("CLOUD_DEPLOYMENT"),
                    "migration_inventory_unsupported")
    # Any other use of the registry (extend, +=, alias, computed app, hidden
    # conditional) could change active apps; fail instead of guessing.
    parents = {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "INSTALLED_APPS":
            ancestor = node
            while id(ancestor) in parents and id(ancestor) not in handled:
                ancestor = parents[id(ancestor)]
            require(id(ancestor) in handled, "migration_inventory_unsupported")
    require(len(active) == len(set(active)), "migration_inventory_unsupported")
    expected = {row["module"] for row in mapping["apps"] if row["condition"] == "always" or (
        row["condition"] == "if_present" and (backend / row["source_root"]).exists())}
    require(set(active) == expected, "migration_inventory_unsupported")
    result = []
    for module in sorted(active):
        row = by_module[module]
        root = (backend if row["location"] == "backend" else library) / row["source_root"]
        require(root.is_dir() and not any(p.is_symlink() for p in (root, *root.parents)),
                "migration_inventory_unsupported")
        config = root / "apps.py"
        if config.exists():
            configs = []
            for node in ast.parse(read(config)).body:
                if not isinstance(node, ast.ClassDef):
                    continue
                fields = {stmt.targets[0].id: _literal(stmt.value) for stmt in node.body
                          if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                          and isinstance(stmt.targets[0], ast.Name) and stmt.targets[0].id in ("name", "label")}
                if fields.get("name") == module:
                    configs.append(fields)
            require(len(configs) == 1 and configs[0].get("label", module.rsplit(".", 1)[-1]) == row["label"],
                    "migration_inventory_unsupported")
        else:
            require(row["label"] == module.rsplit(".", 1)[-1], "migration_inventory_unsupported")
        result.append((row, root))
    return result


def migration_inventory(mapping, backend_root=None, library_root=None):
    """Canonical sorted [{app_label,name,source_sha256}] inventory, source as data."""
    inventory = []
    for app, root in active_apps(mapping, backend_root, library_root):
        directory = root / "migrations"
        if not app["migrations"]:
            require(not directory.exists(), "migration_inventory_unsupported")
            continue
        require(directory.is_dir() and not directory.is_symlink(), "migration_inventory_unsupported")
        paths = sorted(directory.iterdir())
        require(len(paths) <= MAX_MIGRATIONS, "migration_inventory_unsupported")
        for path in paths:
            if path.name == "__pycache__" or path.name.startswith(("_", "~")) or path.suffix != ".py":
                continue
            require(re.fullmatch(r"[A-Za-z0-9_]{1,128}", path.stem), "migration_inventory_unsupported")
            raw = read(path, 1024 * 1024)
            tree = ast.parse(raw)
            require(not any(_assigned_name(node, "replaces") and (
                not isinstance(node, ast.Assign) or _literal(node.value) not in ([], (), None))
                for node in ast.walk(tree)), "migration_inventory_unsupported")
            inventory.append({"app_label": app["label"], "name": path.stem,
                              "source_sha256": hashlib.sha256(raw).hexdigest()})
            require(len(inventory) <= MAX_MIGRATIONS, "migration_inventory_unsupported")
    require(bool(inventory), "migration_inventory_unsupported")
    return sorted(inventory, key=lambda row: (row["app_label"], row["name"]))


def migration_names(inventory):
    return sorted([[row["app_label"], row["name"]] for row in inventory])


def bounded_rows(cursor, limit=MAX_MIGRATIONS):
    rows = cursor.fetchmany(limit + 1)
    require(len(rows) <= limit and len(json.dumps(rows, default=str)) <= LIMIT)
    return rows


def postgres_connection():
    import psycopg
    return psycopg.connect(host="postgres", port=5432, dbname=os.environ["PG_DB"],
        user=os.environ["PG_USER"], password=os.environ["PG_PASSWORD"], connect_timeout=5,
        options="-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000")


def postgres_migrations(expected):
    with postgres_connection() as db:
        try:
            with db.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                cursor.execute("SHOW transaction_read_only")
                require(cursor.fetchone() == ("on",))
                cursor.execute("SELECT app,name FROM django_migrations ORDER BY app,name")
                applied = [list(row) for row in bounded_rows(cursor)]
                require(all(len(row) == 2 and all(type(v) is str for v in row) for row in applied))
                require(len(applied) == len({tuple(row) for row in applied}) and sorted(applied) == expected,
                        "migration_inventory_mismatch")
                return digest(sorted(applied))
        finally:
            db.rollback()


def http(url, *, headers=None, limit=LIMIT, data=None):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            raise ProbeError("initialization_required")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(urllib.request.Request(url, data=data, headers=headers or {}), timeout=5) as response:
        require(response.status == 200)
        raw = response.read(limit + 1)
        require(len(raw) <= limit)
        return raw


def clickhouse(query, parameters=None):
    token = base64.b64encode((os.environ["CH_USERNAME"] + ":" + os.environ["CH_PASSWORD"]).encode()).decode()
    params = {"query": query, "readonly": "1", "max_execution_time": "5", "max_result_rows": "1000",
              "result_overflow_mode": "throw", "max_result_bytes": str(LIMIT)}
    params.update(parameters or {})
    # GET implicitly installs ClickHouse readonly=2, which then REFUSES changing
    # readonly to 1. POST the fixed catalog SELECT with explicit readonly=1;
    # this is stricter than GET's default and cannot execute release SQL.
    return json.loads(http("http://clickhouse:8123/?" + urllib.parse.urlencode(params),
                           headers={"Authorization": "Basic " + token}, data=b""))["data"]


def redis_readiness():
    import redis
    client = redis.Redis(host="127.0.0.1", port=6379, password=os.environ["REDIS_PASSWORD"],
                         socket_timeout=3, socket_connect_timeout=3, decode_responses=True)
    try:
        require(client.ping() is True)
        require(client.config_get("save") == {"save": ""}
                and client.config_get("appendonly") == {"appendonly": "no"})
    finally:
        client.close()


async def temporal_readiness():
    from temporalio.client import Client
    from temporalio.api.workflowservice.v1 import GetSystemInfoRequest
    from datetime import timedelta
    client = await Client.connect("127.0.0.1:7233", namespace="default")
    result = await client.workflow_service.get_system_info(GetSystemInfoRequest(), timeout=timedelta(seconds=5))
    require(bool(result.server_version))


def health_and_directories():
    for url in ("http://127.0.0.1:8000/health/", "http://127.0.0.1:9464/healthz", "http://127.0.0.1:8080/healthz",
                "http://127.0.0.1:3000/", "http://127.0.0.1:9005/minio/health/live", "http://127.0.0.1:8060/health"):
        http(url)
    for name in ("collector", "telemetry", "objects", "temporal"):
        path = Path("/data") / name
        require(path.is_dir() and not path.is_symlink() and os.statvfs(path).f_flag & os.ST_RDONLY)


def fixed_environment():
    require(os.environ.get("NO_STARTUP_DB_MUTATIONS") == "true"
            and os.environ.get("SERVICE_TYPE") == "readonly-observer")
    fixed = {"PG_HOST": "postgres", "PG_PORT": "5432", "CH_HOST": "clickhouse", "CH_HTTP_PORT": "8123",
             "REDIS_HOST": "127.0.0.1", "REDIS_PORT": "6379", "TEMPORAL_HOST": "127.0.0.1:7233",
             "STORAGE_BACKEND": "minio", "S3_ENDPOINT_URL": "http://127.0.0.1:9005", "FI_CDC_MODE": "outbox"}
    require(all(os.environ.get(key) == value for key, value in fixed.items()))
    require(not os.environ.get("EE_LICENSE_KEY") and not os.environ.get("CLOUD_DEPLOYMENT")
            and not os.environ.get("COMPOSE_PROFILES"))
    # Explicit dependency directory only: no site.py, .pth or application roots.
    sys.path.insert(0, sysconfig.get_path("purelib"))


def observe(mapping):
    fixed_environment()
    inventory = migration_inventory(mapping)
    applied_sha = postgres_migrations(migration_names(inventory))
    require(clickhouse("SELECT 1 FORMAT JSONCompact") == [[1]])
    asyncio.run(temporal_readiness())
    redis_readiness()
    health_and_directories()
    require(not any(name == "django" or name.startswith(("django.", "tfc.")) for name in sys.modules))
    return {"schema_version": 2, "contract": "standalone-runtime-v2", "persistence": PERSISTENCE,
            "migration_inventory_sha256": digest(inventory), "applied_migrations_sha256": applied_sha,
            "active_app_mapping_sha256": digest(mapping), "ready": True}


def table_names(values):
    require(type(values) in (list, tuple) and len(values) <= MAX_TABLES
            and all(type(value) is str and TABLE.fullmatch(value) for value in values)
            and len(values) == len(set(values)), "restore_capacity_unknown")
    return tuple(values)


def postgres_table_sizes(tables):
    tables = table_names(tables)
    if not tables:
        return []
    rows = []
    # Parameterized catalog lookup only; never interpolate identifiers/SQL.
    query = ("SELECT c.reltuples::bigint,pg_total_relation_size(c.oid),c.relkind "
             "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
             "WHERE n.nspname=%s AND c.relname=%s")
    with postgres_connection() as db:
        try:
            with db.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                cursor.execute("SHOW transaction_read_only")
                require(cursor.fetchone() == ("on",), "restore_capacity_unknown")
                for table in tables:
                    cursor.execute(query, tuple(table.split(".")))
                    found = cursor.fetchmany(2)
                    require(len(found) == 1 and found[0][2] in ("r", "m"), "restore_capacity_unknown")
                    count, size, _ = found[0]
                    require(type(count) is int and count >= 0 and type(size) is int and size >= 0,
                            "restore_capacity_unknown")
                    # pg_total_relation_size includes indexes and TOAST. reltuples
                    # is an estimate, not COUNT(*); unknown (-1) never becomes 0.
                    rows.append({"engine": "postgres", "table": table, "rows": count, "bytes": size})
        finally:
            db.rollback()
    return rows


def clickhouse_table_sizes(tables):
    rows = []
    for table in table_names(tables):
        database, name = table.split(".")
        params = {"param_database": database, "param_table": name}
        exists = clickhouse("SELECT toUInt8(count() = 1) FROM system.tables WHERE database={database:String} AND name={table:String} AND endsWith(engine,'MergeTree') FORMAT JSONCompact", params)
        require(exists == [[1]], "restore_capacity_unknown")
        result = clickhouse("SELECT sum(rows),sum(bytes_on_disk) FROM system.parts WHERE active AND database={database:String} AND table={table:String} FORMAT JSONCompact", params)
        require(type(result) is list and len(result) == 1 and len(result[0]) == 2, "restore_capacity_unknown")
        # ClickHouse JSON quotes UInt64 by default. Accept ONLY decimal digits.
        count, size = result[0]
        require(all((type(v) is int and v >= 0) or (type(v) is str and re.fullmatch(r"[0-9]{1,16}", v))
                    for v in (count, size)), "restore_capacity_unknown")
        rows.append({"engine": "clickhouse", "table": table, "rows": int(count), "bytes": int(size)})
    return rows


def measure_tables(postgres_tables, clickhouse_tables):
    fixed_environment()
    return {"schema_version": 1, "table_sizes": postgres_table_sizes(postgres_tables) + clickhouse_table_sizes(clickhouse_tables)}


def main(mapping, *, postgres_tables=None, clickhouse_tables=None):
    signal.alarm(45)
    try:
        result = observe(mapping) if postgres_tables is None else measure_tables(postgres_tables, clickhouse_tables)
        print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
    except ProbeError as exc:
        code = str(exc)
        allowed = {"initialization_required", "migration_inventory_unsupported", "migration_inventory_mismatch", "restore_capacity_unknown"}
        print(json.dumps({"error": code if code in allowed else "initialization_required"}))
        sys.exit(2)
    except Exception:
        sys.exit(2)  # no credentials, rows, raw SQL/library diagnostics
