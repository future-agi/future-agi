"""Bounded post-failure observations, NEVER admission or qualification evidence.

No runner/lifecycle entry points. Use existing app exec, fixed shipping probe
bytes and parameterized read-only catalog queries; never create a helper or
ANALYZE. Catalog estimates stay distinct from the shipping observer's counts;
failed shipping measurements never fall back to those estimates.
"""
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
import fcntl
import json
import math
import os
import stat

from deploy.update.compatibility import transition_measurements
from deploy.update.contracts import BootstrapEnvelopeMeasurement, ReasonCode
from deploy.update.docker_host import ReadOnlyRunner, ObservationFailure, fixed_source
from deploy.update.orchestrator import GuardRefused, registered_installation
from deploy.update.processes import ProcessSupervisor
from deploy.update.receipt import StateDir
from deploy.update.runner_state import StoreBusy

MAX_ITEMS = 64
MAX_OUTPUT = 128 * 1024
MAX_NUMBER = 9007199254740991
TIMEOUT = 40
ADMISSION = frozenset(('backup_exceeds_window', 'migration_exceeds_window',
    'insufficient_disk_for_backup', 'insufficient_disk_for_migration',
    'restore_capacity_unknown', 'restore_capacity_insufficient'))


def failure_category(error):
    """Closed categories only, not class names or arbitrary exception strings."""
    if error is None:
        return None
    if isinstance(error, StoreBusy):
        return 'busy'
    if isinstance(error, TimeoutError) or (isinstance(error, ObservationFailure) and error.code == 'timeout'):
        return 'timeout'
    if isinstance(error, ObservationFailure) and error.code == 'output_limit':
        return 'output_limit'
    if isinstance(error, GuardRefused):
        return 'typed_refusal'
    if isinstance(error, AssertionError):
        return 'assertion_failed'
    if isinstance(error, OSError):
        return 'io_failed'
    return 'collection_failed'


def envelope():
    return dict(schema_version=1, qualification_evidence=False, admission_evidence=False,
                sample='after_failure_not_original_tick')


def reference_summary(row):
    """Only call with publisher.transition's authenticated, exact selected row.

    Bounds precede typed construction (envelopes permit much larger plans).
    Authenticated schema/table/migration names are the only open identifiers;
    nothing from SQL results, environment, exceptions or paths is copied out.
    """
    selected = row['migration_measurements']
    plan = selected.get('pending_plan') if isinstance(selected, dict) else selected
    if not isinstance(plan, (list, tuple)) or len(plan) > MAX_ITEMS:
        raise ValueError('reference_limit')
    scopes = selected.get('migration_tables', []) if isinstance(selected, dict) else selected
    if not isinstance(scopes, (list, tuple)) or len(scopes) > MAX_ITEMS or sum(len(m['tables']) for m in scopes) > MAX_ITEMS:
        raise ValueError('reference_limit')
    tables = selected.get('tables', []) if isinstance(selected, dict) else [t for m in selected for t in m['tables']]
    if not isinstance(tables, (list, tuple)) or len(tables) > MAX_ITEMS:
        raise ValueError('reference_limit')
    measurements = transition_measurements(row)
    selected = measurements.migration_measurements
    if isinstance(selected, BootstrapEnvelopeMeasurement):
        basis = selected.measurement_basis
        scopes = [dict(app_label=m['app_label'], name=m['name'], tables=[dict(t) for t in m['tables']])
                  for m in selected.migration_tables]
        tables = [dict(engine=t.engine, table=t.table, reference_rows=t.reference_rows,
                       reference_bytes=t.reference_bytes) for t in selected.tables]
    else:
        basis = 'per-migration'
        scopes = [dict(app_label=m.app_label, name=m.name,
                       tables=[dict(engine=m.engine, table=t.table) for t in m.tables]) for m in selected]
        tables = [dict(engine=m.engine, table=t.table, reference_rows=t.reference_rows,
                       reference_bytes=t.reference_bytes) for m in selected for t in m.tables]
    from deploy.update.manifest import manifest_hash
    result = envelope()
    result.update(sample='authenticated_reference_not_host_sample',
                  reference_origin='publisher_authenticated_exact_transition', selected_row_sha256=manifest_hash(row),
                  measurement_basis=basis, migrations=scopes, tables=tables)
    return result


@contextmanager
def idle_read_locks(store):
    """Open EXISTING locks read-only; never mkdir/chmod/create or wait/retry.

    Shared locks fence a scheduled/manual writer for this short observation.
    Unlike runner_lock()/StateDir.lock(), this cannot initialize state.
    """
    @contextmanager
    def lock(directory, name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
                raise ValueError('lock_unavailable')
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                raise StoreBusy() from None
            current = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError('lock_unavailable')
            yield
        finally:
            os.close(fd)
    with ExitStack() as stack:
        directory = stack.enter_context(store._directory(global_lock=True))
        stack.enter_context(lock(directory, 'runner.lock'))
        directory = stack.enter_context(StateDir(store.checkout).private_directory())
        stack.enter_context(lock(directory, 'update.lock'))
        yield


def completed_admission_block(status, state, version, *, checked_after, store):
    """Fail fast only for a fresh same-candidate refusal with no live owner."""
    try:
        discovery, operation = status['discovery'], status['operation']
        checked = datetime.fromisoformat(discovery['checked_at'].replace('Z', '+00:00'))
        if (state.active_attempt_id is not None or checked < checked_after
                or discovery.get('state') != 'blocked' or discovery.get('candidate_version') != version
                or not ADMISSION.intersection(discovery.get('reason_codes') or [])):
            return False
        # Notify-only refusals retain the preceding operation, including a
        # successful check/apply. Only an unambiguous terminal operation that
        # predates this discovery is safe; staged/recovery/unknown are not done.
        idle = (operation.get('state') == 'idle' and operation.get('kind') is None
                and operation.get('started_at') is None and operation.get('finished_at') is None)
        if not idle:
            if (operation.get('kind') not in ('check', 'download', 'apply', 'rollback', 'restore')
                    or operation.get('state') not in ('succeeded', 'failed', 'interrupted', 'blocked',
                                                      'rolled_back', 'restored')):
                return False
            started = datetime.fromisoformat(operation['started_at'].replace('Z', '+00:00'))
            finished = datetime.fromisoformat(operation['finished_at'].replace('Z', '+00:00'))
            if not started <= finished <= checked:
                return False
        with idle_read_locks(store):
            return store.read_state().active_attempt_id is None
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError):
        return False  # Ambiguity means continue the ordinary bounded wait.


# Executed after the fixed shipping standalone probe definitions in an isolated
# existing app process. No Django/site startup and no helper-container lifecycle.
# Catalog query identifiers are VALUES. SQL rows are reduced before leaving the app.
CATALOG_PROBE = r'''
def catalog_sample(tables):
    query = ("SELECT c.relkind,c.reltuples,pg_total_relation_size(c.oid) "
             "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
             "WHERE n.nspname=%s AND c.relname=%s")
    result = []
    with postgres_connection() as db:
        try:
            with db.cursor() as cursor:
                cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                cursor.execute("SHOW transaction_read_only")
                if cursor.fetchone() != ("on",):
                    raise ProbeError("restore_capacity_unknown")
                for table in tables:
                    schema, name = table.split(".")
                    cursor.execute("SELECT current_database()=%s,current_schema()=%s,"
                        "EXISTS(SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname=%s)",
                        (os.environ["PG_DB"], schema, schema))
                    identity = cursor.fetchmany(2)
                    if len(identity) != 1 or not all(type(v) is bool for v in identity[0]):
                        raise ProbeError("restore_capacity_unknown")
                    cursor.execute(query, (schema, name))
                    found = cursor.fetchmany(2)
                    if len(found) > 1:
                        raise ProbeError("restore_capacity_unknown")
                    result.append(dict(database_matches_config=identity[0][0],
                        current_schema_matches_reference=identity[0][1], schema_exists=identity[0][2],
                        relation_exists=bool(found), kind=found[0][0] if found else None,
                        reltuples=found[0][1] if found else None, bytes=found[0][2] if found else None))
        finally:
            db.rollback()
    return result


def diagnostic_main():
    signal.alarm(30)  # Docker client timeout alone does not stop a remote exec.
    out = {}
    try:
        # Bound stdin BEFORE JSON decoding. No script/SQL is taken from input.
        raw = sys.stdin.buffer.read(32769)
        if len(raw) > 32768:
            raise ValueError()
        data = json.loads(raw)
        pg, ch = table_names(data['postgres']), table_names(data['clickhouse'])
        if len(pg) + len(ch) > 64:
            raise ValueError()
        os.environ.update(NO_STARTUP_DB_MUTATIONS='true', SERVICE_TYPE='readonly-observer')
        fixed_environment()
        try:
            out['catalog'] = catalog_sample(pg)
        except Exception:
            out['catalog_error'] = 'catalog_unavailable'
        try:
            out['shipping_tables'] = measure_tables(pg, ch)['table_sizes']
        except ProbeError as exc:
            frames = []
            trace = exc.__traceback__
            while trace is not None:
                name = trace.tb_frame.f_code.co_name
                if name in ('measure_tables', 'postgres_table_sizes', 'clickhouse_table_sizes',
                            'table_names', 'fixed_environment', 'require'):
                    frames.append(dict(module='standalone_probe', function=name, line=trace.tb_lineno))
                trace = trace.tb_next
            out['shipping_refusal'] = dict(reason_code=str(exc) if str(exc) in
                ('restore_capacity_unknown', 'initialization_required') else None, frames=frames[-6:])
        except Exception:
            out['shipping_error'] = 'observer_unavailable'
    except Exception:
        out = dict(error='probe_unavailable')
    print(json.dumps(out, allow_nan=False, separators=(',', ':')))

diagnostic_main()
'''


def _number(value, *, unknown=False):
    return (type(value) in (int, float) and (-1 if unknown else 0) <= value <= MAX_NUMBER
            and math.isfinite(value))


def closed_probe_result(raw, reference):
    """Do not trust app stdout, even though the executed source is fixed."""
    if not isinstance(raw, bytes) or len(raw) > MAX_OUTPUT:
        return dict(error='output_limit')
    try:
        doc = json.loads(raw)
        if not isinstance(doc, dict):
            raise ValueError()
        tables = sorted({(t['engine'], t['table']) for t in reference['tables']})
        pg = [t for engine, t in tables if engine == 'postgres']
        result = {}
        catalog = doc.get('catalog')
        if isinstance(catalog, list) and len(catalog) == len(pg):
            rows = []
            for table, row in zip(pg, catalog):
                flags = ('database_matches_config', 'current_schema_matches_reference', 'schema_exists', 'relation_exists')
                if not isinstance(row, dict) or not all(type(row.get(k)) is bool for k in flags):
                    raise ValueError()
                clean = dict(table=table, **{k: row[k] for k in flags})
                kind = row.get('kind')
                clean['kind'] = kind if kind in ('r', 'm', 'p', 'v', 'f', 'i', 'I', 'S', 't', 'c') else None
                count, size = row.get('reltuples'), row.get('bytes')
                clean['reltuples'] = count if _number(count, unknown=True) else None
                clean['bytes'] = size if type(size) is int and _number(size) else None
                clean['estimate_state'] = ('missing_schema' if not row['schema_exists'] else
                    'missing_relation' if not row['relation_exists'] else
                    'unsupported_kind' if kind not in ('r', 'm') else
                    'invalid' if clean['reltuples'] is None or clean['bytes'] is None else
                    'unknown' if count < 0 else 'estimated_zero' if count == 0 else 'estimated')
                rows.append(clean)
            result['postgres'] = rows
        else:
            result['catalog_error'] = 'catalog_unavailable'
        shipping = doc.get('shipping_tables')
        if isinstance(shipping, list) and len(shipping) == len(tables):
            sizes = {}
            for row in shipping:
                key = (row['engine'], row['table'])
                if key not in tables or key in sizes or not all(type(row[k]) is int and _number(row[k]) for k in ('rows', 'bytes')):
                    raise ValueError()
                sizes[key] = dict(engine=key[0], table=key[1], rows=row['rows'], bytes=row['bytes'])
            result['shipping_table_observer'] = dict(status='measured', tables=[sizes[k] for k in tables])
        else:
            refusal = doc.get('shipping_refusal')
            result['shipping_table_observer'] = dict(status='unavailable')
            if isinstance(refusal, dict) and refusal.get('reason_code') in ('restore_capacity_unknown', 'initialization_required'):
                frames = []
                for f in (refusal.get('frames') or [])[:6]:
                    if (isinstance(f, dict) and f.get('module') == 'standalone_probe'
                            and f.get('function') in ('measure_tables', 'postgres_table_sizes', 'clickhouse_table_sizes',
                                                     'table_names', 'fixed_environment', 'require')
                            and type(f.get('line')) is int and 0 < f['line'] < 10000):
                        frames.append({k: f[k] for k in ('module', 'function', 'line')})
                result['shipping_table_observer'] = dict(status='refused', reason_code=refusal['reason_code'], frames=frames)
        return result
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return dict(error='invalid_probe_output')


class _RegisteredReadOnlyRunner:
    """Default discovery calls to registered env, with one shared I/O budget."""
    def __init__(self, bounded, supervisor):
        self.bounded = bounded
        self.docker_context, self.environment = supervisor.docker_context, supervisor.environment

    def __call__(self, args, *, env=None, **kwargs):
        return self.bounded(args, env=self.environment if env is None else env, **kwargs)


def observe_admission(store, configuration, reference):
    """At most 40s aggregate host I/O, 30s remote process, no lock waiting.

    Full measure_update_sizes creates/removes volume helpers. Deliberately use
    its SAME fixed DB-size APIs only; never enter that helper lifecycle here.
    """
    with idle_read_locks(store):
        if store.read_state().active_attempt_id is not None:
            return dict(error='active_attempt')
        registration = store.read_registration()
        launch = registration.launcher
        runner = ProcessSupervisor(configuration.policy, docker_context=registration.binding.docker_context,
            environment={'HOME': str(launch.home), 'PATH': launch.path}, output_limit=MAX_OUTPUT, grace_seconds=.2)
        # registered_installation expects the supervisor's routing attributes.
        # All actual I/O uses the read-only bounded runner, never a tick runner.
        bounded = _RegisteredReadOnlyRunner(ReadOnlyRunner(TIMEOUT, max_output=MAX_OUTPUT), runner)
        inst = registered_installation(registration.binding, bounded)
        from deploy.update.standalone_state import _runtime
        from deploy.update.compatibility import topology_profile
        from deploy.update.docker_host import checked_daemon
        checked_daemon(inst, registration.binding)
        before = _runtime(inst, registration.binding, None, topology_profile('standalone', ()))
        app = before[1]['app']
        tables = sorted({(t['engine'], t['table']) for t in reference['tables']})
        data = {engine: [table for e, table in tables if e == engine] for engine in ('postgres', 'clickhouse')}
        # Match the shipping probe's isolated dependency loading, not Django.
        source = fixed_source('standalone.py').decode() + '\n' + CATALOG_PROBE
        output = bounded([*inst.docker, 'exec', '-i', app['id'], '/usr/local/bin/python3',
                          '-I', '-B', '-S', '-c'+source], cwd=str(inst.root), env=inst.subprocess_env(),
                         input=json.dumps(data), timeout=TIMEOUT, text=False)
        if output.returncode:
            return dict(error='probe_failed')
        result = closed_probe_result(output.stdout, reference)
        try:
            result['runtime_unchanged'] = _runtime(inst, registration.binding, None, topology_profile('standalone', ())) == before
        except Exception as error:
            # Keep the earlier sample even when the final identity read expires;
            # a partial diagnostic can never authorize anything.
            result['runtime_unchanged'] = None
            result['runtime_check_error'] = failure_category(error)
        return result


def collect_admission_diagnostics(checkout, store, configuration, row):
    result = envelope()
    result['observed_at'] = datetime.now(timezone.utc).isoformat()
    result['full_size_observer'] = dict(status='not_sampled', reason='requires_helper_lifecycle')
    try:
        reference = reference_summary(row)
        result['reference'] = reference
        result.update(observe_admission(store, configuration, reference))
    except Exception as error:
        result['error'] = failure_category(error)
        if isinstance(error, GuardRefused) and isinstance(error.reason_code, ReasonCode):
            result['reason_code'] = error.reason_code.value
    try:
        checkout.record('admission-diagnostics.json', result)
    except Exception:
        pass  # A failed diagnostic is not permission to replace the assertion.
    return result


def finish_acceptance(checkout, *, failure, deactivate):
    """Keep original failure independent; never clean up after deactivation fails.

    This is the existing teardown, NOT part of the read-only admission sampler.
    On an otherwise successful test every teardown/evidence error still fails.
    """
    from .real_automatic import setup_failure_metadata
    result = dict(envelope(), original_failure=failure_category(failure), errors=[],
                  deactivation='pending', cleanup='skipped')
    original_setup = setup_failure_metadata(failure)
    if original_setup is not None:
        result['original_setup'] = original_setup
    first = None
    def attempt(stage, function):
        nonlocal first
        try:
            value = function()
            if stage == 'deactivation' and isinstance(value, dict):
                result['deactivation_details'] = value
            return True
        except Exception as error:
            if first is None:
                first = error
            entry = dict(stage=stage, category=failure_category(error))
            setup = setup_failure_metadata(error)
            if setup is not None:
                entry['setup'] = setup
            if stage == 'deactivation':
                details = getattr(error, 'deactivation_metadata', None)
                if isinstance(details, dict):
                    result['deactivation_details'] = details
            result['errors'].append(entry)
            return False
    attempt('diagnostics', lambda: checkout.collect_diagnostics(failure))
    attempt('logs', checkout.collect_logs)
    if attempt('deactivation', deactivate):
        result['deactivation'] = 'succeeded'
        if attempt('cleanup', checkout.cleanup):
            result['cleanup'] = 'succeeded'
        else:
            result['cleanup'] = 'failed'
    else:
        result['deactivation'] = 'failed'
    attempt('record', lambda: checkout.record('teardown-diagnostics.json', result))
    if failure is None and first is not None:
        raise first
    return result
