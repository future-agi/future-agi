"""Replay-aware observed-catalog spool witness for owned rehearsal fixtures.

The collector writes canonical ``futureagi.observed-attributes`` JSON records
under ``/data/collector/observed-catalog`` and removes them after a successful
ClickHouse replay. This witness therefore compares the same normalized row set
across the pending spool and the two canonical index tables. It never creates
or edits spool files. The only fixture fault is a narrowly scoped, temporary
REVOKE of INSERT from the bootstrap-created ``observed_catalog_writer`` user.

Integration API::

    witness = ObservedSpoolWitness(driver)
    witness.pause_delivery()
    # Caller performs normal authenticated ingestion through the product API.
    witness.capture(label, namespace, organization_id, workspace_id, project_id)
    before = witness.read(label)
    # Normal target/previous bootstrap restores grants and may drain the queue.
    witness.restore_delivery()
    after = witness.read(label)  # same shared_files/product_spool witness
    witness.assert_absent(target_only_label)

All product programs, ClickHouse queries, roles, table names, and DDL are fixed
here. SHOW GRANTS output is parsed as data and is never re-executed as SQL.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import time
import uuid
from datetime import datetime

DATABASE = "property_catalog"
WRITER = "observed_catalog_writer"
TABLES = ("observed_attribute_keys", "observed_attribute_values")
SPOOL_DIRECTORY = "/data/collector/observed-catalog"
MAX_RECORD_BYTES = 512 * 1024
MAX_CAPTURE_FILES = 32
MAX_CAPTURE_BYTES = 4 * 1024 * 1024
MAX_SCAN_FILES = 128
MAX_SCAN_BYTES = 32 * 1024 * 1024
MAX_INDEX_ROWS = 128
NAMESPACE_KEY = "qualification.namespace"

SHOW_GRANTS_QUERY = "SHOW GRANTS FOR observed_catalog_writer FORMAT JSONEachRow"
TABLE_LAYOUT_QUERY = """SELECT name, engine, sorting_key, partition_key
FROM system.tables
WHERE database = 'property_catalog'
  AND name IN ('observed_attribute_keys', 'observed_attribute_values')
ORDER BY name
FORMAT JSONEachRow"""
COLUMN_LAYOUT_QUERY = """SELECT table, name, type, position
FROM system.columns
WHERE database = 'property_catalog'
  AND table IN ('observed_attribute_keys', 'observed_attribute_values')
ORDER BY table, position
FORMAT JSONEachRow"""

KEY_FIELDS = (
    "organization_id", "workspace_id", "project_id", "source_kind",
    "attribute_key", "attribute_type", "key_folded", "first_seen", "last_seen",
)
VALUE_FIELDS = (
    "organization_id", "workspace_id", "project_id", "source_kind",
    "attribute_key", "attribute_type", "value_fingerprint", "value_json",
    "value_search_text_folded", "first_seen", "last_seen",
)
KEY_COLUMNS = (
    ("organization_id", "String"),
    ("workspace_id", "String"),
    ("project_id", "String"),
    ("source_kind", "LowCardinality(String)"),
    ("attribute_key", "String"),
    ("attribute_type", "LowCardinality(String)"),
    ("key_folded", "String"),
    ("first_seen", "SimpleAggregateFunction(min, DateTime64(6, 'UTC'))"),
    ("last_seen", "SimpleAggregateFunction(max, DateTime64(6, 'UTC'))"),
)
VALUE_COLUMNS = (
    ("organization_id", "String"),
    ("workspace_id", "String"),
    ("project_id", "String"),
    ("source_kind", "LowCardinality(String)"),
    ("attribute_key", "String"),
    ("attribute_type", "LowCardinality(String)"),
    ("value_fingerprint", "FixedString(64)"),
    ("value_json", "String"),
    ("value_search_text_folded", "String"),
    ("first_seen", "SimpleAggregateFunction(min, DateTime64(6, 'UTC'))"),
    ("last_seen", "SimpleAggregateFunction(max, DateTime64(6, 'UTC'))"),
)
KEY_ORDER = "organization_id,workspace_id,project_id,source_kind,attribute_key,attribute_type"
VALUE_ORDER = KEY_ORDER + ",value_fingerprint,value_json"
PARTITION = "cityhash64(workspace_id)%64"

# This program has no path, command, role, table, or SQL argument. It reads
# only real collector spool entries and returns bounded bytes for host parsing.
SPOOL_SCAN_PROGRAM = r"""
import base64,hashlib,json,os,re,stat,sys
from pathlib import Path
args=json.load(sys.stdin)
namespace=args['namespace'].encode('utf-8')
project=args['project_id'].encode('ascii')
root=Path('/data/collector/observed-catalog')
rows=[]
total=0
scanned_files=0
scanned_bytes=0
if root.exists():
    root_info=root.lstat()
    if not stat.S_ISDIR(root_info.st_mode):
        raise RuntimeError('invalid observed-catalog spool directory')
    entries=[]
    for entry_number,path in enumerate(root.iterdir(),1):
        if entry_number>256:
            raise RuntimeError('observed-catalog directory bound exceeded')
        if re.fullmatch(r'observed-[0-9a-f]{64}\.json',path.name):
            entries.append(path)
    for path in sorted(entries,key=lambda item:item.name):
        scanned_files+=1
        if scanned_files>128:
            raise RuntimeError('observed-catalog scan file bound exceeded')
        info=path.lstat()
        scanned_bytes+=info.st_size
        if scanned_bytes>33554432:
            raise RuntimeError('observed-catalog scan byte bound exceeded')
        if not stat.S_ISREG(info.st_mode) or info.st_size<=0 or info.st_size>524288:
            raise RuntimeError('invalid observed-catalog spool entry')
        fd=os.open(str(path),os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,'rb') as stream:
            raw=stream.read(524289)
        after=path.lstat()
        if len(raw)!=info.st_size or (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):
            raise RuntimeError('observed-catalog spool changed during read')
        if namespace not in raw and project not in raw:
            continue
        total+=len(raw)
        if len(rows)>=32 or total>4194304:
            raise RuntimeError('observed-catalog witness bound exceeded')
        digest=hashlib.sha256(raw).hexdigest()
        if path.name!='observed-'+digest+'.json':
            raise RuntimeError('observed-catalog spool digest mismatch')
        rows.append({'path':str(path),'sha256':digest,'payload_b64':base64.b64encode(raw).decode('ascii')})
print(json.dumps(rows,separators=(',',':'),sort_keys=True))
"""

KEYS_QUERY = """SELECT organization_id, workspace_id, project_id, source_kind,
attribute_key, attribute_type, key_folded,
formatDateTime(first_seen, '%Y-%m-%d %H:%i:%S.%f', 'UTC') AS first_seen,
formatDateTime(last_seen, '%Y-%m-%d %H:%i:%S.%f', 'UTC') AS last_seen
FROM property_catalog.observed_attribute_keys FINAL
WHERE organization_id = {org:String} AND workspace_id = {workspace:String}
  AND project_id = {project:String}
LIMIT 129
SETTINGS max_result_rows=129, max_result_bytes=1048576, result_overflow_mode='throw',
         max_execution_time=10, max_memory_usage=67108864
FORMAT JSONEachRow"""
VALUES_QUERY = """SELECT organization_id, workspace_id, project_id, source_kind,
attribute_key, attribute_type, value_fingerprint, value_json,
value_search_text_folded,
formatDateTime(first_seen, '%Y-%m-%d %H:%i:%S.%f', 'UTC') AS first_seen,
formatDateTime(last_seen, '%Y-%m-%d %H:%i:%S.%f', 'UTC') AS last_seen
FROM property_catalog.observed_attribute_values FINAL
WHERE organization_id = {org:String} AND workspace_id = {workspace:String}
  AND project_id = {project:String}
LIMIT 129
SETTINGS max_result_rows=129, max_result_bytes=1048576, result_overflow_mode='throw',
         max_execution_time=10, max_memory_usage=67108864
FORMAT JSONEachRow"""


class ObservedSpoolWitnessError(RuntimeError):
    """A bounded, sanitized refusal to claim the shared-files witness."""


def _fail(message: str) -> None:
    raise ObservedSpoolWitnessError(message)


def _reject_constant(_value: str):
    _fail("non-finite number in observed-catalog record")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("duplicate JSON field in observed-catalog record")
        result[key] = value
    return result


def _load_json(raw: bytes):
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant)
    except ObservedSpoolWitnessError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        _fail("invalid observed-catalog JSON")


def _canonical_json(value) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False,
                          sort_keys=True, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        _fail("observed-catalog witness cannot be normalized")


def _timestamp(value) -> str:
    if type(value) is not str:
        _fail("invalid observed-catalog timestamp")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S.%f")
    except ValueError:
        _fail("invalid observed-catalog timestamp")
    if parsed.strftime("%Y-%m-%d %H:%M:%S.%f") != value:
        _fail("noncanonical observed-catalog timestamp")
    return value


def _canonical_uuid(value: str) -> str:
    if type(value) is not str:
        _fail("invalid observed-catalog identity")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        _fail("invalid observed-catalog identity")
    if parsed.int == 0 or parsed.version is None or str(parsed) != value:
        _fail("noncanonical observed-catalog identity")
    return value


def _scope_matches(row: dict, scope: dict) -> bool:
    return all(row.get(key) == scope[key] for key in ("organization_id", "workspace_id", "project_id"))


def _validate_row(row, fields, scope):
    if type(row) is not dict or set(row) != set(fields):
        _fail("unsupported observed-catalog row layout")
    if any(type(row[field]) is not str for field in fields):
        _fail("invalid observed-catalog row value")
    normalized = dict(row)
    normalized["first_seen"] = _timestamp(row["first_seen"])
    normalized["last_seen"] = _timestamp(row["last_seen"])
    if normalized["last_seen"] < normalized["first_seen"]:
        _fail("invalid observed-catalog observation interval")
    if row["source_kind"] not in ("custom_attribute", "system_attribute"):
        _fail("unsupported observed-catalog source kind")
    if row["attribute_type"] not in ("string", "number", "boolean", "array", "map", "json"):
        _fail("unsupported observed-catalog attribute type")
    if row["attribute_key"] == "" or len(row["attribute_key"].encode("utf-8")) > 4096:
        _fail("invalid observed-catalog attribute key")
    if fields == VALUE_FIELDS:
        if re.fullmatch(r"[a-f0-9]{64}", row["value_fingerprint"]) is None:
            _fail("invalid observed-catalog value fingerprint")
        _load_json(row["value_json"].encode("utf-8"))
    if not scope or _scope_matches(row, scope):
        return normalized
    return None


def _table_identity(table: str, row: dict) -> tuple:
    fields = KEY_FIELDS if table == "keys" else VALUE_FIELDS
    return (table,) + tuple(row[field] for field in fields if field not in ("first_seen", "last_seen"))


def _merge_rows(destination: dict, table: str, row: dict) -> None:
    identity = _table_identity(table, row)
    prior = destination.get(identity)
    if prior is None:
        destination[identity] = dict(row)
    else:
        prior["first_seen"] = min(prior["first_seen"], row["first_seen"])
        prior["last_seen"] = max(prior["last_seen"], row["last_seen"])


def _schema_compact(value: str) -> str:
    return re.sub(r"[\s`()'`]", "", value).lower()


def _expected_grant(table: str, privileges: str = "SELECT, INSERT") -> str:
    if table not in TABLES or privileges not in ("SELECT, INSERT", "SELECT"):
        _fail("unsupported observed-catalog privilege layout")
    return "GRANT " + privileges + " ON `" + DATABASE + "`.`" + table + "` TO " + WRITER


def privilege_sql(action: str, table: str) -> str:
    """Construct the only DDL this witness can issue; never accept raw SQL."""
    if action == "REVOKE" and table in TABLES:
        return "REVOKE INSERT ON `" + DATABASE + "`.`" + table + "` FROM " + WRITER
    if action == "GRANT" and table in TABLES:
        return "GRANT INSERT ON `" + DATABASE + "`.`" + table + "` TO " + WRITER
    _fail("unsupported observed-catalog privilege operation")


def _parse_grant_line(line: str):
    """Parse only the exact bootstrap grant; the returned line is never SQL input."""
    if type(line) is not str:
        _fail("unsupported observed-catalog writer grant")
    for table in TABLES:
        for privileges in ("SELECT, INSERT", "SELECT"):
            canonical = _expected_grant(table, privileges)
            unquoted = "GRANT " + privileges + " ON " + DATABASE + "." + table + " TO " + WRITER
            if line in (canonical, unquoted):
                return table, privileges
    _fail("unsupported observed-catalog writer grant")


def _scope_rows_sql_rows(driver, scope):
    params = {"org": scope["organization_id"], "workspace": scope["workspace_id"],
              "project": scope["project_id"]}
    try:
        key_rows = driver.clickhouse(KEYS_QUERY, params=params)
        value_rows = driver.clickhouse(VALUES_QUERY, params=params)
    except Exception:
        _fail("observed-catalog index read failed")
    if (type(key_rows) is not list or type(value_rows) is not list
            or len(key_rows) > MAX_INDEX_ROWS or len(value_rows) > MAX_INDEX_ROWS):
        _fail("invalid or over-limit observed-catalog index response")
    result = {"keys": [], "values": []}
    for table, rows, fields in (("keys", key_rows, KEY_FIELDS), ("values", value_rows, VALUE_FIELDS)):
        for row in rows:
            normalized = _validate_row(row, fields, scope)
            if normalized is None:
                _fail("observed-catalog index returned another tenant")
            result[table].append(normalized)
    return result


class ObservedSpoolWitness:
    """Temporarily pause only fixture writer INSERTs and witness real replay."""

    def __init__(self, driver):
        self.driver = driver
        self._baseline_grants = None
        self._paused = False
        self._captures = {}
        project = getattr(driver, "project", None)
        if type(project) is not str or re.fullmatch(
                r"fagi-upd-e2e-(?:driver-m1|release)-[a-f0-9]{8,32}", project) is None:
            _fail("observed-catalog witness requires an owned disposable rehearsal project")
        if not callable(getattr(driver, "app_python", None)) or not callable(getattr(driver, "clickhouse", None)):
            _fail("observed-catalog witness requires fixed product operations")

    def _read_grants(self):
        try:
            rows = self.driver.clickhouse(SHOW_GRANTS_QUERY)
        except Exception:
            _fail("observed-catalog writer grants are unavailable")
        if type(rows) is not list or len(rows) != 2:
            _fail("unsupported observed-catalog writer grant layout")
        parsed = []
        for row in rows:
            if type(row) is not dict or set(row) != {"grants"}:
                _fail("unsupported observed-catalog writer grant layout")
            parsed.append(_parse_grant_line(row["grants"]))
        if len(set(parsed)) != 2 or set(parsed) != {(table, "SELECT, INSERT") for table in TABLES}:
            _fail("observed-catalog writer grants differ from bootstrap")
        return tuple(sorted(_expected_grant(table) for table in TABLES))

    def _validate_layout(self):
        try:
            tables = self.driver.clickhouse(TABLE_LAYOUT_QUERY)
            columns = self.driver.clickhouse(COLUMN_LAYOUT_QUERY)
        except Exception:
            _fail("observed-catalog table layout is unavailable")
        if type(tables) is not list or len(tables) != 2:
            _fail("unsupported observed-catalog table layout")
        expected_tables = {
            "observed_attribute_keys": (KEY_ORDER, "AggregatingMergeTree"),
            "observed_attribute_values": (VALUE_ORDER, "AggregatingMergeTree"),
        }
        seen = set()
        for row in tables:
            if type(row) is not dict or set(row) != {"name", "engine", "sorting_key", "partition_key"}:
                _fail("unsupported observed-catalog table layout")
            name = row["name"]
            if (type(name) is not str or type(row["engine"]) is not str
                    or type(row["sorting_key"]) is not str or type(row["partition_key"]) is not str
                    or name not in expected_tables or name in seen
                    or row["engine"] != expected_tables[name][1]):
                _fail("unsupported observed-catalog table layout")
            expected_order = expected_tables[name][0]
            if (_schema_compact(row["sorting_key"]) != expected_order
                    or _schema_compact(row["partition_key"]) != _schema_compact(PARTITION)):
                _fail("unsupported observed-catalog table layout")
            seen.add(name)
        if seen != set(TABLES):
            _fail("unsupported observed-catalog table layout")
        expected_columns = {
            "observed_attribute_keys": KEY_COLUMNS,
            "observed_attribute_values": VALUE_COLUMNS,
        }
        if type(columns) is not list or len(columns) != len(KEY_COLUMNS) + len(VALUE_COLUMNS):
            _fail("unsupported observed-catalog column layout")
        observed = {table: [] for table in TABLES}
        for row in columns:
            if type(row) is not dict or set(row) != {"table", "name", "type", "position"}:
                _fail("unsupported observed-catalog column layout")
            table = row["table"]
            if (type(table) is not str or table not in observed
                    or type(row["name"]) is not str or type(row["type"]) is not str
                    or type(row["position"]) is not int):
                _fail("unsupported observed-catalog column layout")
            observed[table].append((row["name"], _schema_compact(row["type"]), row["position"]))
        for table in TABLES:
            expected = expected_columns[table]
            actual = observed[table]
            normalized_expected = [(name, _schema_compact(kind), index)
                                   for index, (name, kind) in enumerate(expected, 1)]
            if actual != normalized_expected:
                _fail("unsupported observed-catalog column layout")

    def _issue_privilege(self, action: str, table: str) -> None:
        statement = privilege_sql(action, table)
        try:
            self.driver.clickhouse(statement)
        except Exception:
            _fail("observed-catalog fixture privilege operation failed")

    def _current_grants_unchecked(self):
        """Read/parse the fixed account without emitting its values in errors."""
        try:
            rows = self.driver.clickhouse(SHOW_GRANTS_QUERY)
            if type(rows) is not list or len(rows) not in (2,):
                return None
            parsed = []
            for row in rows:
                if type(row) is not dict or set(row) != {"grants"}:
                    return None
                parsed.append(_parse_grant_line(row["grants"]))
            if len(set(parsed)) != len(parsed):
                return None
            return tuple(sorted(_expected_grant(table, privilege) for table, privilege in parsed))
        except Exception:
            return None

    def pause_delivery(self) -> None:
        if self._paused:
            _fail("observed-catalog delivery is already paused")
        self._validate_layout()
        current = self._read_grants()
        if self._baseline_grants is not None and current != self._baseline_grants:
            _fail("observed-catalog writer grants changed outside bootstrap")
        self._baseline_grants = current
        try:
            for table in TABLES:
                self._issue_privilege("REVOKE", table)
            expected_paused = tuple(sorted(_expected_grant(table, "SELECT") for table in TABLES))
            if self._current_grants_unchecked() != expected_paused:
                _fail("observed-catalog INSERT pause was not confirmed")
            self._paused = True
        except BaseException:
            # If the first revoke succeeded and the second failed, restore only
            # the two known bootstrap INSERT grants before propagating refusal.
            try:
                self.restore_delivery()
            except BaseException:
                _fail("observed-catalog fixture fault cleanup was not confirmed")
            raise

    def observe_bootstrap_delivery(self, label: str):
        """Require normal bootstrap to restore grants and replay; never repair it."""
        if self._baseline_grants is None or self._current_grants_unchecked() != self._baseline_grants:
            _fail('normal bootstrap did not restore observed-catalog grants')
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            result = self.read(label)
            pending = self._scope_rows_from_spool(label)
            if time.monotonic() >= deadline:
                break
            if not pending['keys'] and not pending['values']:
                self._paused = False
                return result
            time.sleep(.25)
        _fail('normal bootstrap did not replay the observed-catalog witness')

    def restore_delivery(self) -> None:
        """Release the test fault, preserving the exact bootstrap grant state."""
        if self._baseline_grants is None:
            return
        current = self._current_grants_unchecked()
        baseline = self._baseline_grants
        if current != baseline:
            # The only permissible repair is re-granting a known INSERT on one
            # of the two fixed tables. Never replay SHOW GRANTS output as SQL.
            if current is None:
                missing = list(TABLES)
            else:
                parsed_current = set()
                for grant in current:
                    table, privilege = _parse_grant_line(grant)
                    if privilege == "SELECT, INSERT":
                        parsed_current.add(table)
                missing = [table for table in TABLES if table not in parsed_current]
            for table in missing:
                self._issue_privilege("GRANT", table)
        confirmed = self._current_grants_unchecked()
        if confirmed != baseline:
            _fail("observed-catalog writer grants were not restored exactly")
        self._paused = False

    def _scan_spool(self, namespace: str, project_id: str):
        try:
            result = self.driver.app_python(SPOOL_SCAN_PROGRAM,
                                           {"namespace": namespace, "project_id": project_id})
        except Exception:
            _fail("observed-catalog spool observation failed")
        if type(result) is not list or len(result) > MAX_CAPTURE_FILES:
            _fail("observed-catalog spool observation exceeded bounds")
        records = []
        seen_paths = set()
        total = 0
        for item in result:
            if type(item) is not dict or set(item) != {"path", "sha256", "payload_b64"}:
                _fail("invalid observed-catalog spool observation")
            path, digest, encoded = item["path"], item["sha256"], item["payload_b64"]
            if type(path) is not str or not path.startswith(SPOOL_DIRECTORY + "/"):
                _fail("invalid observed-catalog spool path")
            filename = path.rsplit("/", 1)[-1]
            if re.fullmatch(r"observed-[a-f0-9]{64}\.json", filename) is None or path in seen_paths:
                _fail("invalid observed-catalog spool identity")
            if type(digest) is not str or filename != "observed-" + digest + ".json":
                _fail("invalid observed-catalog spool digest")
            if type(encoded) is not str:
                _fail("invalid observed-catalog spool payload")
            try:
                raw = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                _fail("invalid observed-catalog spool payload")
            if len(raw) <= 0 or len(raw) > MAX_RECORD_BYTES or hashlib.sha256(raw).hexdigest() != digest:
                _fail("corrupt observed-catalog spool record")
            total += len(raw)
            if total > MAX_CAPTURE_BYTES:
                _fail("observed-catalog spool observation exceeded bounds")
            records.append(_decode_spool(raw))
            seen_paths.add(path)
        return records

    def capture(self, label: str, namespace: str, organization_id: str,
                workspace_id: str, project_id: str):
        if type(label) is not str or re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", label) is None:
            _fail("invalid observed-catalog witness label")
        if label in self._captures:
            _fail("duplicate observed-catalog witness label")
        if type(namespace) is not str or not namespace or len(namespace) > 255 or any(ord(c) < 32 for c in namespace):
            _fail("invalid observed-catalog witness namespace")
        scope = {"organization_id": _canonical_uuid(organization_id),
                 "workspace_id": _canonical_uuid(workspace_id),
                 "project_id": _canonical_uuid(project_id)}
        records = self._scan_spool(namespace, scope["project_id"])
        namespace_json = json.dumps(namespace, ensure_ascii=False, separators=(",", ":"))
        marker_intervals = set()
        marker_key_found = False
        for record in records:
            for row in record["keys"]:
                if _scope_matches(row, scope) and row["attribute_key"] == NAMESPACE_KEY:
                    marker_key_found = True
            for row in record["values"]:
                if (_scope_matches(row, scope) and row["attribute_key"] == NAMESPACE_KEY
                        and row["attribute_type"] == "string" and row["value_json"] == namespace_json):
                    marker_intervals.add((row["first_seen"], row["last_seen"]))
        if not marker_key_found or len(marker_intervals) != 1:
            _fail("attributable observed-catalog spool record unavailable")
        interval = next(iter(marker_intervals))
        normalized = {"keys": {}, "values": {}}
        for record in records:
            for table in ("keys", "values"):
                fields = KEY_FIELDS if table == "keys" else VALUE_FIELDS
                for raw_row in record[table]:
                    row = _validate_row(raw_row, fields, scope)
                    if row is not None and (row["first_seen"], row["last_seen"]) == interval:
                        _merge_rows(normalized[table], table, row)
        if not any(row["attribute_key"] == NAMESPACE_KEY for row in normalized["keys"].values()):
            _fail("observed-catalog namespace key is missing")
        if not any(row["attribute_key"] == NAMESPACE_KEY and row["value_json"] == namespace_json
                   for row in normalized["values"].values()):
            _fail("observed-catalog namespace value is missing")
        expected = {table: sorted(normalized[table].values(), key=_canonical_json)
                    for table in ("keys", "values")}
        self._captures[label] = {"scope": scope, "namespace": namespace, "expected": expected}
        return self.read(label)

    def _scope_rows_from_spool(self, label: str):
        capture = self._captures[label]
        scope, namespace, expected = capture["scope"], capture["namespace"], capture["expected"]
        records = self._scan_spool(namespace, scope["project_id"])
        expected_identities = {
            table: {_table_identity(table, row): row for row in expected[table]}
            for table in ("keys", "values")
        }
        merged = {"keys": {}, "values": {}}
        for record in records:
            for table in ("keys", "values"):
                fields = KEY_FIELDS if table == "keys" else VALUE_FIELDS
                for raw_row in record[table]:
                    row = _validate_row(raw_row, fields, scope)
                    if row is None:
                        continue
                    identity = _table_identity(table, row)
                    if identity not in expected_identities[table]:
                        _fail("unexpected observed-catalog spool row for witness tenant")
                    _merge_rows(merged[table], table, row)
        return merged

    def read(self, label: str):
        if label not in self._captures:
            _fail("unknown observed-catalog witness label")
        capture = self._captures[label]
        actual = self._scope_rows_from_spool(label)
        indexed = _scope_rows_sql_rows(self.driver, capture["scope"])
        for table in ("keys", "values"):
            for row in indexed[table]:
                identity = _table_identity(table, row)
                known = {_table_identity(table, expected) for expected in capture["expected"][table]}
                if identity not in known:
                    _fail("unexpected observed-catalog index row for witness tenant")
                _merge_rows(actual[table], table, row)
            expected_map = {_table_identity(table, row): row for row in capture["expected"][table]}
            if actual[table] != expected_map:
                _fail("observed-catalog spool/index witness differs")
        semantic_rows = {table: sorted(actual[table].values(), key=_canonical_json)
                         for table in ("keys", "values")}
        digest = hashlib.sha256(_canonical_json(semantic_rows)).hexdigest()
        return {"domain": "shared_files", "identity": "observed_catalog/" + capture["namespace"],
                "origin": "product_spool", "payload_sha256": digest}

    def assert_absent(self, label: str) -> bool:
        if label not in self._captures:
            _fail("unknown observed-catalog witness label")
        capture = self._captures[label]
        scope, namespace = capture["scope"], capture["namespace"]
        records = self._scan_spool(namespace, scope["project_id"])
        for record in records:
            for table in ("keys", "values"):
                fields = KEY_FIELDS if table == "keys" else VALUE_FIELDS
                if any(_validate_row(row, fields, scope) is not None for row in record[table]):
                    _fail("target-only observed-catalog record remains in spool")
        indexed = _scope_rows_sql_rows(self.driver, scope)
        if indexed["keys"] or indexed["values"]:
            _fail("target-only observed-catalog record remains in index")
        return True


def _decode_spool(raw: bytes):
    document = _load_json(raw)
    if type(document) is not dict or set(document) != {"format", "version", "keys", "values"}:
        _fail("unsupported observed-catalog spool envelope")
    if document["format"] != "futureagi.observed-attributes" or type(document["version"]) is not int or document["version"] != 1:
        _fail("unsupported observed-catalog spool version")
    if type(document["keys"]) is not list or type(document["values"]) is not list:
        _fail("invalid observed-catalog spool rows")
    if not document["keys"] and not document["values"]:
        _fail("empty observed-catalog spool record")
    rows = {"keys": [], "values": []}
    for table, fields in (("keys", KEY_FIELDS), ("values", VALUE_FIELDS)):
        for row in document[table]:
            normalized = _validate_row(row, fields, {})
            rows[table].append(normalized if normalized is not None else dict(row))
    return rows
