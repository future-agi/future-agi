#!/usr/bin/env python3
"""Nonpublishing native product qualification, Python 3.9+ stdlib host code.

Only frozen candidates and authenticated predecessors are inputs. No external
success report, command, customer bypass, trial startup or image rollback is
accepted. Qualification requires six executed shipping-transaction suites.
Full qualify first dry-runs EVERY MILESTONES entry through run_milestone (no
Docker) and binds each job plan's pinned source/target digests and platform to
the requested pair. Only a milestone that plans all six suites for exactly that
pair is then dispatched for real, once, with an in-process QualificationSession
sink at args.qualification; otherwise it refuses before provisioning
(shipping_executor_unavailable, or suite_unrun for a six-suite plan of another
pair). A zero stage exit, JSON verdict or healthy image is not six-suite
evidence: rows hold only assertions the sink itself executed. The protected
product entrypoint uses the same exact-pair sink. The legacy PR-only synthetic
factory cannot authorize product rows.
Missing integration, witnesses, measurements or assertions produce a bounded
notify-only decision, NEVER an affirmative row. Unit fixtures are not product
qualification. Full runs belong on native amd64 Actions, not the local Mac.

CI/local-test interface (no publication or production-key signing)
----------------------------------------------------------------
Milestone 1 is a SEPARATE bounded, nonqualifying mode. On the isolated native
Actions runner it assembles v1.53.0's retained deployment with the current host
updater, boots its fixed published digest, enumerates A4 witnesses/gaps and
calls StandaloneBackup.run_quiesced, leaving A cold until disposable teardown.
It never creates proof-row.json, policy.json, a release proof or passing suites.
Public --through choices are the module's bounded milestones; the internal
qualification dispatch additionally plans and executes an authenticated pair.
Both use run_milestone(name, args). No bounded run can certify a release.

  python3 -I scripts/rehearse_update_compatibility.py qualify \\
    --through source_snapshot --boot-timeout 4200 \\
    --docker-context colima-fagi-update --out <new-home-directory>

Milestone 2 continues in that SAME session/stack through --through target_readiness:
B=v1.54.0 is prefetched by digest before quiescence, its real offline Django
loader plans against A's observed applied set, and Updater's shared lock-held
entry commits the env and starts B with the real AutomaticRuntime guards. It
checks B's inventory, every retained witness and final identities, then records
an explicitly unsigned milestone success decision. No enrollment/admission,
signed A6 preflight, restore suite or six-suite qualification is claimed.
A nonzero plan without exact execution measurements fails, never becomes zero.

Add --dry-run to write only the labeled job plan without Git/Docker/trust work.
The live milestone requires a private runner Docker config whose dedicated
colima-fagi-update context points at that runner's native daemon, NOT Colima or
a shared host. Its BackupProof binds a snapshot-only intent, not a target release.

  python3 -I scripts/rehearse_update_compatibility.py qualify \\
    --source-ref <image@digest> --target-ref <image@digest> \\
    --platform linux/amd64 --deployment-dir <retained-A-files> \\
    --reference-scale registered-100k --boot-timeout 4200 --out <new-dir>

The retained directory must also contain source-manifest.json (the exact source
manifest), target-manifest.json (the frozen uncertified candidate) and
source-manifest.sigstore.json. A certified source additionally needs its exact
source-compatibility.json, bound by that source manifest. The driver selects the
matching retained execution layout from that signed proof (so B -> C may keep
A's files); it never substitutes B's published deployment map. These are DATA,
not executed or imported. The standalone image pins must equal the explicit refs. For a test-key predecessor,
pass --public-key <test-public.pem>; host verification is still mandatory.
Milestone-backed full qualify requires the private colima-fagi-update routing
on a native amd64 Actions runner. The historical protected producer may use its
isolated native default context; the Mac always requires colima-fagi-update and HOME-backed files. Each failure writes
failure.json and proof-row.json with eligible=false, and exits NONZERO. A row
never reports passed suites that did not execute. registered-100k is the reviewed
100000-row workload, not a user-controlled bypass of the A6 reference envelope.

  python3 -I scripts/rehearse_update_compatibility.py package --rows <out> \\
    --target <candidate.json> --public-key <predecessor-public.pem> \\
    --ephemeral-key --out <new-package-dir> --run-id <same-run-id>

Packaging reuses the strict product packager, revalidates all raw evidence and
source trust, and signs using --tlog-upload=false. CI must use a fresh ephemeral
key; the legacy --key option accepts an explicitly supplied local test key only.
No keyless signing, registry writes or release publication. --evidence
<same-job-evidence> also accepts strict raw evidence directly. Private .key files
are NEVER upload artifacts.
The historical protected release CLI below remains available.
The checked-in disposable driver calls StandaloneBackup.run_quiesced,
AutomaticRuntime.start_and_wait and StandaloneRestore with RecoveryLedger;
manual migrate, ad-hoc backup hooks and prewritten success rows are forbidden.

Private A6 evidence contract (unreleased; not customer input)
---------------------------------------------------------
large_volume_reference is {snapshot_generation, registry_sha256, reference_rows,
tables:[{engine,table,rows,bytes}]}. A9 requires exactly the registered tables
in pending_migration_tables, the complete ordered PRE-START real-loader table
analysis ({app_label,name,tables:[{engine,table}]}). Only that intersection is
seeded to >=100000 rows; a positively measured empty intersection seeds nothing.
Every pending migration executes within the measured interval, including normal
non-scaling witness-corpus migrations. snapshot_generation is SHA256(canonical
JSON of source_snapshot_inventory), not an arbitrary run label.
A complete inventory alone is not association evidence. Retain
source_runtime_observation (all RuntimeObservationV2 fields),
source_installation_binding (all InstallationBinding fields),
source_image_inspections (service -> {Id,Os,Architecture,RepoDigests}),
source_backup_proof, source_backup_seal and source_snapshot_members (role ->
complete normalized rows from the shipping archive verifier). The validator
compares the retained source with the independently supplied predecessor raw
bytes, platform, image digests and execution deployment. The backup expectation
must name SHA256(canonical JSON of the frozen UNCERTIFIED target candidate), not
an arbitrary target or the later certified manifest (which would be circular).

Transfer records bind snapshot_generation and measurement_basis=
"snapshot-logical-payload-bytes-v1". Re-derive each role's logical payload from
its hash-bound complete member tree, counting a hardlinked payload only once;
compare that independently derived value with the archive and both transfer
records. Neither tar padding, sparse allocated blocks nor an adapter-provided
byte total is accepted as this basis. This intentionally conservative reference
is no larger than the host's max(logical,allocated)+metadata restore-work basis.

In exact-per-migration mode, each resource_samples item binds that generation and contains app_label, name,
engine, risk_class, tables, duration_seconds, started_monotonic,
completed_monotonic, samples. The signed table denominators must equal the
independent measurements, never a second claimed scale in the migration item.

ContinuousResourceSampler.measure wraps the REAL migration execution. Every
sample has disk_bytes (including WAL/temp), cumulative wal_bytes, current
 temp_bytes, rss_bytes, processed_bytes, monotonic, boundary. A synchronous
before_start sample precedes the actual operation and an after_end sample
follows it; ongoing during samples have <=2-second gaps. Raw operation boundaries
are retained separately; both must be bracketed and their difference must equal
duration_seconds. A missing boundary, read failure or excessive gap refuses the
row. Retain the highest observed disk/WAL/temp/RSS values, not only the final
sample. This bounded sampling evidence is not a claim that arbitrary spikes
between observations were measured. The adapter must expose resource reads and
real per-migration bootstrap boundaries; manual migrate is not a substitute.

Historical images without those events use a separately tagged
normal-bootstrap-envelope-v1 object, never invented individual durations or
throughput. bootstrap_envelope retains the whole normal start/readiness interval,
ordered plan, independently probed registered-table scope, target/snapshot
identities, and bounded raw allocated-disk, WAL/temp and memory observations.
Disk allocation is attributable to the exact enrolled stores and target container
writable layers/logs, not a filesystem-wide delta that unrelated cleanup can
cancel. WAL/temp are physical observed peaks in this mode. Admission charges the
whole interval and growth; missing readers, scopes, identities or samples refuse.
The private qualification-driver record also binds the actual ProductHarness
source, separately from the installed executor closure. Neither measurement mode
nor a passing bounded milestone can replace the complete six-suite execution.
"""
from __future__ import annotations

import argparse
import base64
import binascii
import copy
import hashlib
import importlib
import importlib.machinery
import importlib.util
import json
import math
import os
import re
import signal
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, fields as dataclass_fields
from datetime import datetime, timezone
from pathlib import Path
import posixpath

ROOT = Path(__file__).resolve().parents[1]
if 'deploy' not in sys.modules:
    spec = importlib.machinery.ModuleSpec('deploy', None, is_package=True)
    spec.submodule_search_locations = [str(ROOT / 'deploy')]
    sys.modules['deploy'] = importlib.util.module_from_spec(spec)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.update.compatibility import (  # noqa: E402
    SUITES, decode_document, inputs_hash,
    topology_profile, transition_measurements, validate_release_proof,
)
from deploy.update.apply import Interrupted  # noqa: E402
from deploy.update.contracts import pending_migration_plan_hash  # noqa: E402
from deploy.update.discovery import ReleaseDiscovery  # noqa: E402
from deploy.update.httpcache import HttpCache  # noqa: E402
from deploy.update.manifest import (  # noqa: E402
    BUNDLE_ASSET, MANIFEST_ASSET, canonical_json, manifest_hash, parse_version, validate_manifest,
)
from deploy.update.settings import load_settings  # noqa: E402
from deploy.update.trust import Verifier, has_release_assets  # noqa: E402
from scripts.build_update_compatibility_proof import (  # noqa: E402
    MANUAL_REASONS, MARKERS, MAX_EVIDENCE_BYTES, MEASURED_FIELDS,
    implementation_inventory, inventory_hash, read_bytes, required_cases, write_pair,
)

REPOSITORY = 'future-agi/future-agi'
SUPPORTED_PLATFORM = 'linux/amd64'
SUPPORTED_PLATFORMS = ('linux/amd64', 'linux/arm64')
PREDECESSOR_LIMIT, ROW_LIMIT = 3, 64
PRODUCER = 'standalone-product-rehearsal-v2'
RESOURCE_PREFIX = 'fagi-upd-e2e-release-'
SHIPPING_REHEARSAL_CONTRACT = 'standalone-disposable-rehearsal-v1'
REFERENCE_ROWS = 100000
VOLUME_ROLES = ('app-data', 'postgres-data', 'clickhouse-data')


class RehearsalError(RuntimeError):
    pass


class ManualRelease(RehearsalError):
    def __init__(self, reason):
        if reason not in MANUAL_REASONS:
            raise ValueError('unknown notify-only reason')
        super().__init__(reason)


def need(ok, reason):
    if not ok:
        raise RehearsalError(reason)


def equal(actual, expected, reason):
    need(actual == expected, reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


class CommandError(RehearsalError):
    """Safe subprocess provenance; never interpolate argv, env or raw stderr.

    In particular the acceptance publisher captures the qualify subprocess's
    stderr. Preserve its closed refusal code in the exception that reaches CI,
    instead of reducing every failure to 'rehearsal command failed'.
    """
    def __init__(self, executable, returncode, stderr):
        name = Path(str(executable)).name
        self.command_name = name if re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', name) else 'command'
        self.returncode = returncode
        self.refusal_code = 'suite_failed'
        text = stderr.decode('utf-8', errors='replace') if isinstance(stderr, bytes) else str(stderr)
        for code in re.findall(r'(?m)^(?:qualification|release rehearsal) refused: ([a-z_]+)$', text):
            if code in MANUAL_REASONS:
                self.refusal_code = code
        super().__init__('rehearsal command failed (%s, exit %d, refusal %s)' %
                         (self.command_name, returncode, self.refusal_code))


def command(argv, *, cwd=ROOT, input=None, timeout=1800, env=None):
    """Fixed caller-owned argv; never execute an artifact or manifest command."""
    try:
        result = subprocess.run([str(v) for v in argv], cwd=str(cwd), input=input,
                                env=env, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ManualRelease('suite_timed_out') from None
    if result.returncode != 0:
        raise CommandError(argv[0], result.returncode, result.stderr)
    need(len(result.stdout) <= MAX_EVIDENCE_BYTES, 'rehearsal output too large')
    return result.stdout


@dataclass(frozen=True)
class Row:
    release: object
    execution_deployment: dict
    layout_source_sha: str
    dependencies: tuple
    platform: str = SUPPORTED_PLATFORM
    deployment_directory: object = None

    def binding(self):
        old = self.release.manifest
        layout = topology_profile('standalone', ())
        return {'predecessor': {'version': old['version'], 'manifest_hash': manifest_hash(old),
            'raw_manifest_sha256': sha(self.release.raw_manifest), 'source_sha': old['source_sha'],
            'mode': 'standalone', 'platform': self.platform, 'profiles': [],
            'active_components': list(layout.active_components), 'restore_certified': True,
            'execution_deployment_hash': manifest_hash(self.execution_deployment)},
            'execution_deployment': copy.deepcopy(self.execution_deployment),
            'dependencies': list(self.dependencies), 'persistence': dict(layout.persistence)}


def select_predecessors(target, discovery, *, platform=SUPPORTED_PLATFORM):
    """Authenticate exact raw B and recursively find B's retained A layout."""
    need(platform in SUPPORTED_PLATFORMS, 'unreviewed platform')
    deadline = discovery._begin()
    records = discovery._records(target['channel'], deadline)
    if target['channel'] == 'beta':
        records += discovery._records('stable', deadline)
    records = sorted(records, key=lambda r: parse_version(r['tag_name']), reverse=True)
    floor, version = parse_version(target['minimum_version']), parse_version(target['version'])
    tags = list(dict.fromkeys(r['tag_name'] for r in records
        if floor <= parse_version(r['tag_name']) < version
        and parse_version(r['tag_name'])[0] == version[0]
        and has_release_assets(r, MANIFEST_ASSET, BUNDLE_ASSET)))[:PREDECESSOR_LIMIT]
    cache = {}

    def fetch(tag):
        if tag not in cache:
            need(len(cache) < ROW_LIMIT, 'supported layout ancestry exceeds bound')
            result = discovery.refresh(tag, channel='beta' if '-' in tag else 'stable')
            need(result.complete and result.candidate is not None, 'signed predecessor unavailable')
            release = result.candidate
            if release.manifest.get('compatibility', {}).get('validation') is not None:
                need(bool(release.release_proof) and not release.proof_reason_codes, 'predecessor proof unavailable')
                validate_release_proof(release.release_proof, release.manifest, repository=REPOSITORY)
            cache[tag] = release
        return cache[tag]

    def authenticated_row(item):
        ancestor = fetch(item['predecessor']['version'])
        need(sha(ancestor.raw_manifest) == item['predecessor']['raw_manifest_sha256']
             and manifest_hash(ancestor.manifest) == item['predecessor']['manifest_hash']
             and ancestor.manifest['source_sha'] == item['predecessor']['source_sha']
             and ancestor.manifest['deployment'] == item['predecessor_deployment']
             and ancestor.manifest['components'] == item['predecessor_components'], 'predecessor ancestry mismatch')
        return ancestor

    def layout_owner(release, deployment, seen):
        if release.manifest['deployment'] == deployment:
            return release.manifest['source_sha']
        need(release.version not in seen, 'layout ancestry cycle')
        need(bool(release.release_proof), 'retained layout has no authenticated owner')
        proof = validate_release_proof(release.release_proof, release.manifest, repository=REPOSITORY)
        for item in proof['predecessors']:
            if item.get('execution_deployment', item['predecessor_deployment']) == deployment:
                return layout_owner(authenticated_row(item), deployment, seen | {release.version})
        raise RehearsalError('retained layout has no authenticated owner')

    rows = []
    for tag in tags:
        release = fetch(tag)
        retained = {}
        if release.release_proof:
            proof = validate_release_proof(release.release_proof, release.manifest, repository=REPOSITORY)
            for item in proof['predecessors']:
                previous = item['predecessor']
                if (previous['mode'], previous['platform'], previous['profiles']) != ('standalone', platform, []):
                    continue
                authenticated_row(item)
                deployment = item.get('execution_deployment', item['predecessor_deployment'])
                key, value = manifest_hash(deployment), (deployment, tuple(item['dependencies']))
                need(key not in retained or retained[key] == value, 'ambiguous dependency lock')
                retained[key] = value
        deployment = release.manifest['deployment']
        retained.setdefault(manifest_hash(deployment), (deployment, ()))
        for deployment, dependencies in retained.values():
            rows.append(Row(release, deployment, layout_owner(release, deployment, set()), dependencies, platform))
            need(len(rows) <= ROW_LIMIT, 'supported matrix exceeds bound')
    if not rows:
        raise ManualRelease('no_supported_signed_predecessor')
    return rows


class ExecutedCases:
    """No verdict/count input: append only after an executable assertion returns."""
    def __init__(self):
        self.cases = {suite: {} for suite in SUITES}
        self.failed = None
        self.active_suite = None
        self.timings = []

    def check(self, suite, name, assertion):
        need(suite in self.cases and name not in self.cases[suite] and callable(assertion), 'duplicate/unrun assertion')
        start = time.monotonic()
        outcome = 'failed'
        try:
            need(assertion() is None, 'assertions must raise on failure, not return a verdict')
            outcome = 'passed'
        except BaseException:
            self.failed = (suite, name)
            raise
        finally:
            self.timings.append(dict(suite=suite, assertion=name, outcome=outcome,
                                     duration_seconds=time.monotonic() - start))
        self.cases[suite][name] = self.timings[-1]['duration_seconds']

    def complete(self):
        """A failed assertion cannot be erased by retrying it in the same run."""
        if self.failed is not None:
            raise ManualRelease('suite_failed')
        layout = topology_profile('standalone', ())
        if any(set(self.cases[s]) != required_cases(s, layout) for s in SUITES):
            raise ManualRelease('suite_unrun')

    def render(self, suite, layout, properties):
        need(self.failed is None, 'failed assertion in qualification run')
        need(set(self.cases[suite]) == required_cases(suite, layout), 'unrun required assertions')
        root = ET.Element('testsuite', name=suite, tests=str(len(self.cases[suite])), failures='0', errors='0', skipped='0')
        props = ET.SubElement(root, 'properties')
        for name, value in sorted(properties.items()):
            ET.SubElement(props, 'property', name=name, value=str(value))
        for name, elapsed in sorted(self.cases[suite].items()):
            ET.SubElement(root, 'testcase', classname=suite, name=name, assertions='1', time='%.6f' % elapsed)
        return ET.tostring(root, encoding='utf-8')


def _integer(value):
    return type(value) is int and 0 <= value <= 9007199254740991


def _positive(value):
    return type(value) in (int, float) and math.isfinite(value) and 0 < value <= 1e18


def load_high_volume_registry(path=None):
    """Read A8's reviewed registry as data. Never execute its source/path fields."""
    path = path or ROOT / 'deploy/update/high-volume-tables.json'
    try:
        doc = decode_document(read_bytes(Path(path), MAX_EVIDENCE_BYTES), MAX_EVIDENCE_BYTES)
    except FileNotFoundError:
        raise ManualRelease('high_volume_reference_unavailable') from None
    need(set(doc) == {'schema_version', 'table_identity', 'tables'} and doc['schema_version'] == 1
         and type(doc['schema_version']) is int and type(doc['table_identity']) is str
         and type(doc['tables']) is list and 1 <= len(doc['tables']) <= 4096, 'invalid high-volume registry')
    result, seen = [], set()
    for entry in doc['tables']:
        need(type(entry) is dict and {'database', 'table', 'source', 'reason'} <= set(entry)
             and set(entry) <= {'database', 'table', 'source', 'reason', 'app_label', 'model'}, 'invalid registry entry')
        engine, table = entry['database'], entry['table']
        need(engine in ('postgres', 'clickhouse') and type(table) is str
             and re.fullmatch(r'[a-z_][a-z0-9_]{0,127}', table) is not None, 'invalid registry table')
        need((engine, table) not in seen, 'duplicate registry table')
        seen.add((engine, table))
        result.append(copy.deepcopy(entry))
    return tuple(result)


def select_reference_registry(registry, pending_migration_tables, *, plan=None):
    """A9 intersection, from the producer's real-loader PRE-START plan analysis.

    Entries are complete/ordered {app_label,name,tables:[{engine,table}]}.
    Schema-qualified table identities are resolved by the live loader/state,
    not by path names or by the signed metric's own denominator. An empty plan
    positively selects zero tables. Missing analysis is not an empty plan.
    """
    need(type(pending_migration_tables) is list, 'missing pending migration table analysis')
    identities, touched = [], set()
    for item in pending_migration_tables:
        need(type(item) is dict and set(item) == {'app_label', 'name', 'tables'}
             and type(item['tables']) is list and len(item['tables']) <= 4096,
             'invalid pending migration table analysis')
        identities.append({k: item[k] for k in ('app_label', 'name')})
        seen = set()
        for table in item['tables']:
            need(type(table) is dict and set(table) == {'engine', 'table'} and
                 table['engine'] in ('postgres', 'clickhouse') and type(table['table']) is str and
                 re.fullmatch(r'[a-z_][a-z0-9_]{0,63}\.[a-z_][a-z0-9_]{0,63}', table['table']),
                 'invalid resolved pending migration table')
            key = (table['engine'], table['table'])
            need(key not in seen, 'duplicate pending migration table')
            seen.add(key)
            touched.add((key[0], key[1].split('.')[1]))
    pending_migration_plan_hash(identities)
    if plan is not None:
        equal(identities, plan, 'pending migration table analysis plan differs')
    return tuple(copy.deepcopy(row) for row in registry if (row['database'], row['table']) in touched)


# Appended to TARGET_LOADER_PLAN inside the isolated target image, not executed
# on the host. a['database_schemas'] MUST come from a read-only source DB probe.
# Tables come from Django's real loader, the historical ProjectState and each
# real operation object, never from a migration filename, number or app name.
# Related effects are a conservative SUPERSET: an FK/M2M field adds its related
# and through tables; PK/unique, rename and delete changes add every inbound
# relation. RunPython/RunSQL use the gate's reviewed literal UPDATE_SAFETY
# tables. Unresolvable relations, custom operations and undeclared code refuse.
PENDING_TABLE_SCOPE = r'''
from django.db import migrations
from django.db.backends.utils import strip_quotes, truncate_name
try:
    from django.db.migrations.utils import resolve_relation
except ImportError:
    from django.db.migrations.operations.utils import resolve_relation
import ast, re
schemas=a['database_schemas']
assert type(schemas) is dict and set(schemas)=={'postgres','clickhouse'}
assert all(type(v) is str and re.fullmatch(r'[a-z_][a-z0-9_]{0,62}',v) for v in schemas.values())
IDENT=r'[a-z_][a-z0-9_]{0,62}'
def resolved(engine, table):
    assert type(table) is str
    quoted=re.fullmatch('"('+IDENT+')"\\."('+IDENT+')"',table)
    if quoted: table=quoted.group(1)+'.'+quoted.group(2)
    elif re.fullmatch(IDENT,table): table=schemas[engine]+'.'+table
    assert re.fullmatch(IDENT+'\\.'+IDENT,table), 'unresolved_table_identity'
    return (engine,table)
def raw_table(state, app, name):
    key=(app,name.lower())
    assert key in state.models, 'unresolved_model_table_scope_unavailable'
    return state.models[key].options.get('db_table') or truncate_name(app+'_'+name.lower(),63)
def model_table(state, app, name):
    return resolved('postgres',raw_table(state,app,name))
# ModelState fields are not contributed to a class: field.name is None, so
# the state's own field key names an auto-created M2M table, as Django does.
def relation_tables(state, app, model, field, field_name):
    remote=getattr(field,'remote_field',None)
    if field is None or remote is None:
        return set()
    target=resolve_relation(remote.model,app,model)
    tables={model_table(state,*target)}
    if getattr(field,'many_to_many',False):
        through=getattr(remote,'through',None)
        if through is not None:
            tables.add(model_table(state,*resolve_relation(through,app,model)))
        else:
            name=getattr(field,'db_table',None) or truncate_name(
                '%s_%s'%(strip_quotes(raw_table(state,app,model)),field_name),63)
            tables.add(resolved('postgres',name))
    return tables
def inbound(state, app, model):
    wanted=(app,model.lower()); tables=set()
    for (other_app,other),state_model in state.models.items():
        for name,field in state_model.fields.items():
            remote=getattr(field,'remote_field',None)
            if remote is None: continue
            if resolve_relation(remote.model,other_app,other)==wanted:
                tables.add(model_table(state,other_app,other))
                tables|=relation_tables(state,other_app,other,field,name)
    return tables
def declared(migration):
    source=Path(inspect.getsourcefile(migration.__class__)).read_text()
    tree=ast.parse(source)
    values=[node.value for node in tree.body if isinstance(node,(ast.Assign,ast.AnnAssign)) and
        any(isinstance(t,ast.Name) and t.id=='UPDATE_SAFETY' for t in
            (node.targets if isinstance(node,ast.Assign) else [node.target]))]
    assert len(values)==1, 'opaque_operation_table_scope_unavailable'
    declaration=ast.literal_eval(values[0])
    assert type(declaration) is dict and declaration.get('schema_version')==1
    assert type(declaration.get('tables')) is list
    result=set()
    for table in declaration['tables']:
        assert type(table) is str and re.fullmatch(r'(postgres|clickhouse):[A-Za-z_][A-Za-z_0-9]*',table)
        engine,table=table.split(':',1)
        result.add(resolved(engine,table))
    return result
NO_TABLE={'AlterModelOptions','AlterModelManagers','CreateExtension','BloomExtension','BtreeGinExtension',
    'BtreeGistExtension','CITextExtension','CryptoExtension','HStoreExtension','TrigramExtension',
    'UnaccentExtension','CreateCollation','RemoveCollation'}
MODEL_OPS={'AddField','RemoveField','AlterField','RenameField','AddIndex','RemoveIndex','RenameIndex',
    'AddIndexConcurrently','RemoveIndexConcurrently','AddConstraint','RemoveConstraint',
    'AddConstraintNotValid','ValidateConstraint','AlterConstraint','AlterModelTable',
    'AlterModelTableComment','AlterUniqueTogether','AlterIndexTogether','AlterOrderWithRespectTo'}
def touched(operation, migration, state):
    app=migration.app_label
    if type(operation) is migrations.SeparateDatabaseAndState:
        physical=state.clone(); tables=set()
        for child in operation.database_operations:
            tables|=touched(child,migration,physical)
            child.state_forwards(app,physical)
        return tables
    if type(operation) is migrations.RunPython:
        return set() if operation.code is migrations.RunPython.noop else declared(migration)
    if type(operation) is migrations.RunSQL:
        return set() if type(operation.sql) is str and operation.sql==migrations.RunSQL.noop else declared(migration)
    kind=type(operation).__name__
    assert type(operation).__module__.startswith(('django.db.migrations.operations.',
        'django.contrib.postgres.operations')), 'custom_operation_table_scope_unavailable'
    if kind in NO_TABLE:
        return set()
    after=state.clone(); operation.state_forwards(app,after)
    if kind=='CreateModel':
        return {model_table(after,app,operation.name)}
    if kind=='DeleteModel':
        return {model_table(state,app,operation.name)}|inbound(state,app,operation.name)
    if kind=='RenameModel':
        return ({model_table(state,app,operation.old_name),model_table(after,app,operation.new_name)}|
                inbound(state,app,operation.old_name)|inbound(after,app,operation.new_name))
    assert kind in MODEL_OPS, 'unclassified_operation_table_scope_unavailable'
    model=getattr(operation,'model_name',None) or getattr(operation,'name',None)
    assert type(model) is str and model, 'unclassified_operation_table_scope_unavailable'
    tables={model_table(state,app,model),model_table(after,app,model)}
    if kind in ('AddField','AlterField','RemoveField','RenameField'):
        names=(operation.name,) if kind!='RenameField' else (operation.old_name,operation.new_name)
        for current in (state,after):
            fields=current.models[(app,model.lower())].fields
            for name in names:
                field=fields.get(name)
                tables|=relation_tables(current,app,model,field,name)
                if field is not None and (getattr(field,'primary_key',False) or getattr(field,'unique',False)):
                    tables|=inbound(current,app,model)
    return tables
state=loader.project_state(nodes=sorted(loader.applied_migrations),at_end=True)
scopes=[]
for migration, backwards in plan:
    assert not backwards
    tables=set()
    for operation in migration.operations:
        tables|=touched(operation,migration,state)
        operation.state_forwards(migration.app_label,state)
    scopes.append(dict(app_label=migration.app_label,name=migration.name,
        tables=[dict(engine=engine,table=table) for engine,table in sorted(tables)]))
# ONE final line (decode_product_output reads only the last line): the same
# real-loader plan as TARGET_LOADER_PLAN plus its pre-start table scope.
print(json.dumps(dict(planned=sorted([list(node) for node in loader.graph.nodes]),
    inventory=inventory,inventory_sha256=hashlib.sha256(json.dumps(inventory,sort_keys=True,
    separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest(),
    pending=[dict(app_label=migration.app_label,name=migration.name) for migration,backwards in plan],
    database_schemas=schemas,pending_migration_tables=scopes)))
'''


def target_table_scope_probe():
    """Fixed read-only target-image program; never import target Django on host."""
    from scripts.update_rehearsal_shipping import TARGET_LOADER_PLAN
    return TARGET_LOADER_PLAN + PENDING_TABLE_SCOPE


def pending_table_scope_record(result, *, target_image_digest, source_applied_sha256):
    """Corpus record from ONE run of target_table_scope_probe() in exact B.

    The stage driver runs the fixed program (not TARGET_LOADER_PLAN alone) in
    its networkless read-only B loader with stdin {'applied': A's observed
    applied set, 'database_schemas': {'postgres','clickhouse'} read from cold A},
    decodes the final line, and stores this as private evidence
    'pending_table_scope_probe' plus 'pending_migration_tables' = its scope.
    """
    _keys(result, ('planned', 'inventory', 'inventory_sha256', 'pending', 'database_schemas',
                   'pending_migration_tables'), 'incomplete target migration table probe output')
    equal(result['inventory_sha256'], sha(json.dumps(result['inventory'], sort_keys=True, separators=(',', ':'),
          ensure_ascii=False, allow_nan=False).encode()), 'table scope inventory hash differs')
    equal([{k: item[k] for k in ('app_label', 'name')} for item in result['pending_migration_tables']],
          result['pending'], 'table scope does not cover the exact pending plan')
    return dict(program_sha256=sha(target_table_scope_probe().encode()), target_image_digest=target_image_digest,
                target_inventory_sha256=result['inventory_sha256'], source_applied_sha256=source_applied_sha256,
                database_schemas=copy.deepcopy(result['database_schemas']),
                pending_migration_tables=copy.deepcopy(result['pending_migration_tables']))


def validate_scope_probe(fields, corpus, target):
    """Bind pre-start scope to the fixed program, actual B inventory and cold A."""
    probe = corpus.get('pending_table_scope_probe')
    if type(probe) is not dict:
        raise ManualRelease('migration_measurement_unavailable')
    _keys(probe, ('program_sha256', 'target_image_digest', 'target_inventory_sha256',
                  'source_applied_sha256', 'database_schemas', 'pending_migration_tables'),
          'incomplete target migration table probe')
    equal(probe['program_sha256'], sha(target_table_scope_probe().encode()), 'table scope probe implementation differs')
    equal(probe['target_image_digest'], target['components']['standalone']['digest'], 'table scope target digest differs')
    equal(probe['target_inventory_sha256'], fields['migration_evidence']['target_inventory_sha256'],
          'table scope target inventory differs')
    equal(probe['source_applied_sha256'], fields['migration_evidence']['source_applied_set_sha256'],
          'table scope source applied set differs')
    _keys(probe['database_schemas'], ('postgres', 'clickhouse'), 'missing independently observed database schemas')
    need(all(type(v) is str and re.fullmatch(r'[a-z_][a-z0-9_]{0,62}', v)
             for v in probe['database_schemas'].values()), 'invalid independently observed database schemas')
    equal(probe['pending_migration_tables'], corpus['pending_migration_tables'], 'table probe scope differs')
    select_reference_registry(load_high_volume_registry(), probe['pending_migration_tables'],
                              plan=corpus['target_pending_plan'])


def seed_reference_tables(registry, seed_one, measure_one, *, reference_rows=REFERENCE_ROWS):
    """Publisher-only bulk workload, then independent actual row/byte measurement.

    The shipping disposable adapter supplies checked-in per-table seeders. No
    constraints/triggers may be disabled, no table may be silently skipped, and
    missing real product seeding operations keep the release notify-only. The
    table identity must be resolved against the actual configured database (the
    registry's `database` field is an ENGINE, not a schema).
    """
    need(type(reference_rows) is int and REFERENCE_ROWS <= reference_rows <= 100000000, 'invalid reference scale')
    results = []
    for entry in registry:
        seed_one(copy.deepcopy(entry), reference_rows)
        actual = measure_one(copy.deepcopy(entry))
        need(type(actual) is dict and set(actual) == {'engine', 'table', 'rows', 'bytes'}, 'missing table measurement')
        if (actual['engine'] != entry['database'] or not _integer(actual['rows'])
                or actual['rows'] < reference_rows or not _integer(actual['bytes']) or actual['bytes'] <= 0):
            raise ManualRelease('high_volume_reference_unavailable')
        need(type(actual['table']) is str and re.fullmatch(r'[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*', actual['table'])
             and actual['table'].split('.')[1] == entry['table'], 'resolved registry table mismatch')
        results.append(copy.deepcopy(actual))
    # A9: an independently observed zero/untouched plan has no at-scale work.
    # The caller must supply select_reference_registry's exact intersection.
    return results


def witness_inventory(rows):
    """Exactly the controlled namespace: identities and normalized payload hashes.

    Origins explicitly exclude arbitrary files named spool and uncompleted
    Temporal signals. Missing naturally produced spool is a gap, not a fixture.
    """
    origins = {'postgres_app': {'product_api'}, 'clickhouse': {'collector_ingestion'},
        'object_store': {'product_api', 'bundled_s3_api'},
        'temporal': {'completed_product_workflow'}, 'shared_files': {'product_spool'}}
    need(type(rows) is list and 1 <= len(rows) <= 100000, 'unrun witness namespace')
    seen, result = set(), []
    for row in rows:
        need(type(row) is dict and set(row) == {'domain', 'identity', 'payload_sha256', 'origin'}, 'invalid witness')
        domain = row['domain']
        need(domain in origins and row['origin'] in origins[domain], 'unsupported witness origin')
        need(type(row['identity']) is str and 0 < len(row['identity']) <= 4096
             and type(row['payload_sha256']) is str and re.fullmatch(r'[a-f0-9]{64}', row['payload_sha256']), 'invalid witness')
        key = (domain, row['identity'])
        need(key not in seen, 'duplicate witness')
        seen.add(key)
        result.append(copy.deepcopy(row))
    if {r['domain'] for r in result} != set(origins):
        raise ManualRelease('witness_operation_unavailable')
    return sorted(result, key=lambda r: (r['domain'], r['identity']))


MAX_RESOURCE_SAMPLE_GAP_SECONDS = 2.0


def validate_sampling_interval(samples, duration_seconds, *, started_monotonic, completed_monotonic):
    """A baseline alone is not peak evidence. Bracket the actual operation.

    Monotonic timestamps are recorded at observation completion. The producer
    takes a synchronous baseline before releasing migration execution and a
    synchronous final observation after it completes, with a continuous sampler
    in between. A delayed/failed sampler makes qualification incomplete.
    """
    need(_positive(duration_seconds) and type(samples) is list and len(samples) >= 2,
         'incomplete migration sampling interval')
    keys = {'disk_bytes', 'wal_bytes', 'temp_bytes', 'rss_bytes', 'processed_bytes',
            'monotonic', 'boundary'}
    for index, sample in enumerate(samples):
        need(type(sample) is dict and set(sample) == keys, 'invalid resource sample')
        need(all(_integer(sample[k]) for k in keys - {'monotonic', 'boundary'})
             and sample['rss_bytes'] > 0, 'invalid resource counters')
        stamp = sample['monotonic']
        need(type(stamp) in (int, float) and math.isfinite(stamp) and stamp >= 0,
             'invalid sample timestamp')
        expected = 'before_start' if index == 0 else 'after_end' if index == len(samples) - 1 else 'during'
        equal(sample['boundary'], expected, 'missing migration sampling boundary')
        if index:
            previous = samples[index - 1]
            need(0 < stamp - previous['monotonic'] <= MAX_RESOURCE_SAMPLE_GAP_SECONDS,
                 'migration sampling coverage gap')
            need(sample['wal_bytes'] >= previous['wal_bytes'] and
                 sample['processed_bytes'] >= previous['processed_bytes'],
                 'cumulative migration counter reset')
    # Boundary timestamps include the small synchronous observation overhead;
    # they must cover, not understate, the measured execution duration.
    need(type(started_monotonic) in (int, float) and math.isfinite(started_monotonic) and
         type(completed_monotonic) in (int, float) and math.isfinite(completed_monotonic) and
         started_monotonic >= 0 and completed_monotonic > started_monotonic,
         'missing migration execution boundaries')
    need(math.isclose(completed_monotonic - started_monotonic, duration_seconds,
                      rel_tol=1e-12, abs_tol=1e-9), 'migration execution duration differs')
    need(samples[0]['monotonic'] <= started_monotonic < completed_monotonic <= samples[-1]['monotonic']
         and started_monotonic - samples[0]['monotonic'] <= MAX_RESOURCE_SAMPLE_GAP_SECONDS
         and samples[-1]['monotonic'] - completed_monotonic <= MAX_RESOURCE_SAMPLE_GAP_SECONDS,
         'samples do not cover migration execution')


class ContinuousResourceSampler:
    """Bracket a real operation with synchronous reads and bounded ongoing reads.

    `read` performs fixed producer-owned resource observations, never a command
    or counter from a release report. Sampling errors, slow/hung reads and gaps
    are failures, not missing values replaced with zero. The migration itself
    must be the shipping normal bootstrap, not a manual migrate invocation.
    """
    def __init__(self, read, *, interval_seconds=0.25, clock=time.monotonic):
        need(callable(read) and _positive(interval_seconds) and interval_seconds <= 1,
             'invalid resource sampler')
        self.read, self.interval, self.clock = read, interval_seconds, clock
        self.samples, self.error = [], None
        self.stop = threading.Event()
        self.lock = threading.Lock()

    def _sample(self, boundary):
        raw = self.read()
        need(type(raw) is dict and set(raw) == {
            'disk_bytes', 'wal_bytes', 'temp_bytes', 'rss_bytes', 'processed_bytes'},
            'invalid live resource observation')
        need(all(_integer(v) for v in raw.values()) and raw['rss_bytes'] > 0,
             'invalid live resource counters')
        sample = dict(raw, monotonic=self.clock(), boundary=boundary)
        with self.lock:
            need(len(self.samples) < 1000000, 'resource evidence bound exceeded')
            self.samples.append(sample)

    def _run(self):
        try:
            while not self.stop.wait(self.interval):
                self._sample('during')
        except BaseException as exc:
            self.error = exc
            self.stop.set()

    def measure(self, operation):
        need(callable(operation) and not self.samples, 'sampler is single use')
        self._sample('before_start')
        worker = threading.Thread(target=self._run, name='release-resource-sampler', daemon=True)
        worker.start()
        started = self.clock()
        try:
            result = operation()
            completed = self.clock()
        finally:
            self.stop.set()
            worker.join(timeout=MAX_RESOURCE_SAMPLE_GAP_SECONDS)
        need(not worker.is_alive() and self.error is None, 'continuous resource sampling failed')
        self._sample('after_end')
        duration = completed - started
        validate_sampling_interval(self.samples, duration, started_monotonic=started,
                                   completed_monotonic=completed)
        return result, {'duration_seconds': duration, 'started_monotonic': started,
                        'completed_monotonic': completed, 'samples': copy.deepcopy(self.samples)}


def migration_measurement(identity, tables, samples, *, engine, risk_class, duration_seconds,
                          started_monotonic=None, completed_monotonic=None):
    """Reduce executed per-migration samples; never invent a rate from a label.

    Samples are from the actual Docker data filesystem: total allocated bytes
    (including WAL/temp), WAL bytes generated, temporary bytes, process RSS and
    observed bytes processed. Raw samples, including peak RSS, stay private.
    Public amplification already includes WAL/temp; recording those separately
    intentionally makes host extrapolation conservative, never subtractive.
    """
    validate_sampling_interval(samples, duration_seconds, started_monotonic=started_monotonic,
                               completed_monotonic=completed_monotonic)
    need(type(identity) is dict and set(identity) == {'app_label', 'name'}, 'invalid migration identity')
    need(type(tables) is list and all(type(t) is dict and
         set(t) == {'table', 'reference_rows', 'reference_bytes'} and
         _integer(t['reference_rows']) and _integer(t['reference_bytes']) for t in tables),
         'unmeasured migration tables')
    reference_bytes = sum(t['reference_bytes'] for t in tables)
    work_rows = sum(t['reference_rows'] for t in tables)
    growth = max(0, max(s['disk_bytes'] for s in samples) - samples[0]['disk_bytes'])
    if growth and reference_bytes == 0:
        raise ManualRelease('migration_measurement_unavailable')
    processed = max(s['processed_bytes'] for s in samples) - samples[0]['processed_bytes']
    if work_rows:
        rate = {'unit': 'rows_per_second', 'value': work_rows / duration_seconds}
    else:
        if processed <= 0:
            raise ManualRelease('migration_measurement_unavailable')
        rate = {'unit': 'bytes_per_second', 'value': processed / duration_seconds}
    return dict(identity, engine=engine, tables=copy.deepcopy(tables), risk_class=risk_class,
        duration_seconds=duration_seconds, throughput=rate,
        peak_disk_amplification_factor=1 + growth / reference_bytes if reference_bytes else 1,
        wal_bytes=max(s['wal_bytes'] for s in samples) - samples[0]['wal_bytes'],
        temp_bytes=max(s['temp_bytes'] for s in samples))


def validate_reference_inventory(reference, *, registry=None, pending_migration_tables=None, plan=None):
    """A9: exactly the registered tables touched by the measured pending plan.

    Other migrations still require execution measurements in migration_forward,
    but do not force an unrelated high-volume table to be seeded at scale.
    No metric can omit its independent pre-start table analysis to claim zero.
    """
    registry = load_high_volume_registry() if registry is None else registry
    registry = select_reference_registry(registry, pending_migration_tables, plan=plan)
    need(type(reference) is dict and set(reference) == {
        'snapshot_generation', 'registry_sha256', 'reference_rows', 'tables'},
        'missing independently measured reference inventory')
    need(type(reference['snapshot_generation']) is str and
         re.fullmatch(r'[a-f0-9]{64}', reference['snapshot_generation']), 'invalid reference generation')
    equal(reference['registry_sha256'], sha(read_bytes(
        ROOT / 'deploy/update/high-volume-tables.json', MAX_EVIDENCE_BYTES)), 'reference registry differs')
    scale = reference['reference_rows']
    need(type(scale) is int and REFERENCE_ROWS <= scale <= 100000000, 'invalid reference scale')
    need(type(reference['tables']) is list and len(reference['tables']) <= 100000,
         'invalid reference tables')
    actual, registered = {}, {(r['database'], r['table']) for r in registry}
    expected_resolved = {(t['engine'], t['table']) for m in pending_migration_tables for t in m['tables']
                         if (t['engine'], t['table'].split('.')[1]) in registered}
    need(len(expected_resolved) == len(registered), 'ambiguous resolved registered-table scope')
    resolved = set()
    for table in reference['tables']:
        need(type(table) is dict and set(table) == {'engine', 'table', 'rows', 'bytes'} and
             table['engine'] in ('postgres', 'clickhouse') and type(table['table']) is str and
             re.fullmatch(r'[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*', table['table']) and
             _integer(table['rows']) and _integer(table['bytes']), 'invalid independent table measurement')
        key = (table['engine'], table['table'])
        need(key not in actual, 'duplicate independent table measurement')
        actual[key] = table
        registry_key = (table['engine'], table['table'].split('.')[1])
        need(registry_key in registered and key in expected_resolved, 'reference table outside pending registered-table scope')
        need(registry_key not in resolved and table['rows'] >= scale and table['bytes'] > 0,
             'ambiguous or underseeded reference table')
        resolved.add(registry_key)
    equal(resolved, registered, 'incomplete touched registered-table reference inventory')
    return actual


TRANSFER_BASIS = 'snapshot-logical-payload-bytes-v1'


def _keys(value, names, reason):
    need(type(value) is dict and set(value) == set(names), reason)
    return value


def _hex(value, length=64):
    return type(value) is str and re.fullmatch(r'[a-f0-9]{%d}' % length, value) is not None


def _instant(value):
    need(type(value) is str, 'invalid retained timestamp')
    try:
        instant = datetime.fromisoformat(value.replace('Z', '+00:00'))
        need(instant.tzinfo is not None and instant.utcoffset() is not None, 'invalid retained timestamp')
        return instant.astimezone(timezone.utc)
    except ValueError:
        raise RehearsalError('invalid retained timestamp') from None


def _snapshot_volume_bytes(snapshot, members):
    """Re-derive the transfer numerator from the cold verifier's complete trees.

    The normalized member inventories are produced independently by the shipping
    archive verifier AND compared with its read-only filesystem scan. A hardlink
    group has one archived payload, not one per directory entry. This basis is
    deliberately logical payload, not tar padding, allocated blocks or an
    adapter-supplied `du` total. The host's larger restore-work basis therefore
    remains conservative. Sparse payloads count apparent bytes, as GNU tar and
    the shipping verifier do. The complete trees remain private and bounded by
    the evidence envelope; an oversized inventory refuses, never truncates.
    """
    from deploy.update.probes.restore_fs import canonical as canonical_tree
    _keys(members, VOLUME_ROLES, 'missing independently captured snapshot members')
    result = {}
    for role, entry in snapshot['archives'].items():
        rows = members[role]
        need(type(rows) is list and 1 <= len(rows) <= 1000000, 'incomplete snapshot member inventory')
        by_name, logical, digest, size = {}, 0, hashlib.sha256(), 0
        previous = None
        for row in rows:
            _keys(row, ('name', 'type', 'size', 'uid', 'gid', 'mode', 'mtime_ns', 'link',
                        'sha256', 'hardlinks', 'xattrs', 'acls'), 'invalid snapshot member')
            name, kind = row['name'], row['type']
            need(type(name) is str and 0 < len(name) <= 4096 and '\0' not in name and
                 not name.startswith('/') and posixpath.normpath(name) == name and
                 name != '..' and not name.startswith('../') and (previous is None or previous < name),
                 'ambiguous snapshot member path')
            previous = name
            need(kind in ('directory', 'file', 'symlink') and _integer(row['size']) and
                 all(type(row[k]) is int and 0 <= row[k] < 2**32 for k in ('uid', 'gid')) and
                 type(row['mode']) is int and 0 <= row['mode'] <= 0o7777 and
                 type(row['mtime_ns']) is int and type(row['xattrs']) is dict and type(row['acls']) is dict,
                 'invalid snapshot member metadata')
            for key, value in row['xattrs'].items():
                need(type(key) is str and key.startswith('user.') and '\0' not in key and type(value) is str,
                     'unsupported snapshot extended attribute')
                try:
                    equal(base64.b64encode(base64.b64decode(value, validate=True)).decode('ascii'), value,
                          'noncanonical snapshot extended attribute')
                except (ValueError, binascii.Error):
                    raise RehearsalError('invalid snapshot extended attribute') from None
            need(set(row['acls']) <= {'access', 'default'}, 'unsupported snapshot ACL')
            for acl in row['acls'].values():
                need(type(acl) is list and all(type(part) is list and len(part) == 3 and
                     part[0] in ('user', 'group', 'mask', 'other') and
                     (part[1] is None or type(part[1]) is int and 0 <= part[1] < 2**32 and part[0] in ('user', 'group')) and
                     type(part[2]) is int and 0 <= part[2] <= 7 for part in acl), 'invalid snapshot ACL')
                equal(acl, sorted(acl, key=lambda part: (part[0], -1 if part[1] is None else part[1])), 'noncanonical snapshot ACL')
                need(len({(part[0], part[1]) for part in acl}) == len(acl), 'duplicate snapshot ACL entry')
            links = row['hardlinks']
            need(type(links) is list and all(type(n) is str for n in links) and links == sorted(set(links)),
                 'invalid snapshot hardlinks')
            if kind == 'file':
                need(_hex(row['sha256']) and row['link'] is None and (not links or len(links) > 1 and name in links),
                     'invalid snapshot file')
                if not links or name == links[0]:
                    logical += row['size']
            else:
                need(row['size'] == 0 and row['sha256'] is None and not links, 'invalid snapshot non-file')
                if kind == 'directory':
                    need(row['link'] is None, 'invalid snapshot directory')
                else:
                    link = row['link']
                    need(type(link) is str and 0 < len(link) <= 4096 and '\0' not in link, 'invalid snapshot symlink')
                    target = posixpath.normpath(link if link.startswith('/') else posixpath.join(posixpath.dirname(name), link))
                    need((role == 'clickhouse-data' and (target == '/var/lib/clickhouse' or
                         target.startswith('/var/lib/clickhouse/'))) if link.startswith('/') else
                         target != '..' and not target.startswith('../'), 'snapshot link escapes volume')
            by_name[name] = row
            raw = canonical_tree(row) + b'\n'
            digest.update(raw); size += len(raw)
        need('.' in by_name and by_name['.']['type'] == 'directory', 'missing snapshot root')
        for name, row in by_name.items():
            parent = posixpath.dirname(name)
            while parent:
                need(parent in by_name and by_name[parent]['type'] == 'directory', 'incomplete snapshot tree')
                parent = posixpath.dirname(parent)
            for linked in row['hardlinks']:
                other = by_name.get(linked)
                need(other is not None and all(other[k] == row[k] for k in row if k != 'name'),
                     'incomplete snapshot hardlink group')
        equal((len(rows), size, digest.hexdigest(), logical),
              (entry['members'], entry['members_bytes'], entry['members_sha256'], entry['logical_bytes']),
              'snapshot archive counters differ from independently captured members')
        result[role] = logical
    need(sum(result.values()) > 0, 'empty snapshot transfer workload')
    return result


def validate_source_preimages(corpus, source, binding, containers):
    """Recompute the retained observation, then compare the sealed physical input.

    Hashing a self-consistent replacement backup is not association. These are
    the preimages of the independent healthy-A observation, captured BEFORE
    quiescence, and checked against that observation's immutable hashes. Neither
    the archive binding nor a later source re-inspection supplies expected data.
    """
    preimages = corpus.get('source_observation_preimages')
    _keys(preimages, ('configuration', 'containers', 'volume_storage'),
          'missing source observation preimages')
    configuration, identities, storage = (preimages[k] for k in
        ('configuration', 'containers', 'volume_storage'))
    _keys(configuration, ('config', 'env', 'files', 'environment', 'updater_config'),
          'incomplete source configuration preimage')
    equal(manifest_hash(configuration), source.config_sha256, 'source configuration preimage differs')
    equal(manifest_hash(configuration['config']), binding['compose_sha256'], 'snapshot Compose model differs from source')
    _keys(identities, ('app', 'postgres', 'clickhouse'), 'incomplete source container preimages')
    _keys(storage, VOLUME_ROLES, 'incomplete source storage preimages')
    equal(manifest_hash(identities), source.container_roster_sha256, 'source container preimages differ')
    from deploy.update.standalone_backup import VOLUMES
    volumes = {}
    for role, (service, destination) in VOLUMES.items():
        observed, identity, sealed = storage[role], identities[service], containers[service]
        _keys(observed, ('name', 'root_identity', 'device', 'filesystem', 'backing_sha256'),
              'incomplete physical source storage')
        equal((observed['name'], observed['root_identity']),
              (binding['volumes'][role]['Name'], binding['volume_root_ids'][role]),
              'snapshot physical storage differs from source')
        need(type(observed['device']) is str and re.fullmatch(r'[0-9]+:[0-9]+', observed['device'])
             and observed['filesystem'] in ('ext4', 'xfs', 'btrfs') and _hex(observed['backing_sha256']),
             'invalid physical source storage')
        _keys(identity, ('id', 'image', 'started', 'mounts', 'config', 'restart_count', 'restart_policy',
                         'bind_hashes', 'volume', 'published_ports', 'network'), 'incomplete source container preimage')
        # Preserve every Mount field and Config field, not a selected command or
        # an environment map that loses duplicate keys. Only mount order varies.
        from deploy.update.standalone_state import _canonical_mounts
        equal((sealed['Id'], sealed['Image'], sealed['Config'], _canonical_mounts(sealed['Mounts']),
               sealed['HostConfig'].get('RestartPolicy', {}).get('Name')),
              (identity['id'], identity['image'], identity['config'], _canonical_mounts(identity['mounts']),
               identity['restart_policy']), 'snapshot container configuration differs from source')
        equal(identity['volume'], binding['volumes'][role], 'snapshot volume object differs from source')
        equal(identity['restart_count'], source.restart_counts[identity['id']], 'source restart count differs')
        equal(identity['started'], corpus['source_snapshot_inventory']['quiescence']['start_times'][service],
              'snapshot start time differs from source')
        volumes[service] = manifest_hash({'daemon': binding['daemon'], 'name': identity['volume']['Name'],
            'mountpoint': identity['volume']['Mountpoint'], 'created_at': identity['volume']['CreatedAt'],
            'driver': identity['volume']['Driver'], 'readonly_config': identity['bind_hashes']})
    equal(manifest_hash({'volumes': volumes, 'roots': manifest_hash(storage)}), source.storage_sha256,
          'source physical storage preimage differs')


def validate_snapshot_evidence(fields, corpus, *, target, predecessor_raw):
    """Validate the shipping inventory and associate it with independent inputs.

    `target` is the frozen *uncertified candidate*, not the later policy/proof
    result (which would create a hash cycle). `predecessor_raw` is supplied by the
    authenticated row caller, never selected from a snapshot/report. No hash in
    the corpus is its own expected transition identity. Retain the source runtime,
    enrollment binding, image inspections, backup proof/seal and complete member
    inventories as checked data; never execute any paths/code found in them.
    """
    from deploy.update.backup_proof import BackupExpectation, validate_backup_proof
    from deploy.update.contracts import (BackupSeal, InstallationBinding, RuntimeImage, RuntimeObservationV2,
                                         installation_binding_hash, RESTORE_PROCEDURE_ID)
    from deploy.update.standalone_backup import BINDS, CONFIG_FILES, VOLUMES
    snapshot = corpus.get('source_snapshot_inventory')
    _keys(snapshot, ('schema_version', 'inventory_version', 'binding', 'binding_sha256', 'expectation',
        'automatic_layout_sha256', 'source_observation_sha256', 'quiescence_evidence_sha256', 'quiescence',
        'containers', 'archives', 'config_files', 'started_at', 'snapshot_at', 'completed_at', 'restore_procedure_id'),
        'incomplete shipping snapshot inventory')
    need(type(snapshot['schema_version']) is int and snapshot['schema_version'] == 2 and
         type(snapshot['inventory_version']) is int and snapshot['inventory_version'] == 2 and
         snapshot['restore_procedure_id'] == RESTORE_PROCEDURE_ID, 'unsupported shipping snapshot inventory')
    equal(snapshot['automatic_layout_sha256'], sha(read_bytes(ROOT / 'deploy/update/automatic-layout.json', MAX_EVIDENCE_BYTES)),
          'snapshot automatic layout differs')
    old = validate_manifest(decode_document(predecessor_raw))
    validate_manifest(target)
    previous = fields['predecessor']
    equal((previous['version'], previous['manifest_hash'], previous['raw_manifest_sha256'], previous['source_sha']),
          (old['version'], manifest_hash(old), sha(predecessor_raw), old['source_sha']), 'expected predecessor differs')
    layout = topology_profile('standalone', ())
    need(previous['mode'] == 'standalone' and previous['profiles'] == [] and
         previous['active_components'] == list(layout.active_components), 'unsupported snapshot transition')
    equal(manifest_hash(fields['execution_deployment']), previous['execution_deployment_hash'], 'expected retained layout differs')
    source_doc = corpus.get('source_runtime_observation')
    _keys(source_doc, (f.name for f in dataclass_fields(RuntimeObservationV2)), 'missing complete source observation')
    source_args = copy.deepcopy(source_doc)
    source_args['observed_at'] = _instant(source_args['observed_at'])
    for key in ('product_images', 'dependency_images'):
        need(type(source_args[key]) is dict, 'invalid source images')
        images = {}
        for service, replicas in source_args[key].items():
            need(type(replicas) is list and len(replicas) == 1, 'ambiguous source replicas')
            _keys(replicas[0], ('image', 'digest', 'image_id'), 'incomplete source image')
            images[service] = (RuntimeImage(**replicas[0]),)
        source_args[key] = images
    source = RuntimeObservationV2(**source_args)
    need(source.ready and source.stable and source.release_version == old['version'] and
         source.manifest_sha256 == manifest_hash(old) and source.platform == previous['platform'] and
         source.deployment_sha256 == manifest_hash(fields['execution_deployment']), 'source observation belongs to another transition')
    migration = fields['migration_evidence']
    equal((source.migration_inventory_sha256, source.applied_migrations_sha256, source.active_app_mapping_sha256),
          (migration['source_inventory_sha256'], migration['source_applied_set_sha256'], migration['active_app_mapping_sha256']),
          'source migration observation differs')
    registration = copy.deepcopy(corpus.get('source_installation_binding'))
    _keys(registration, (f.name for f in dataclass_fields(InstallationBinding)), 'missing complete source installation binding')
    for key in ('checkout', 'env_alias', 'env_target'):
        need(type(registration[key]) is str and Path(registration[key]).is_absolute(), 'invalid retained installation path')
        registration[key] = Path(registration[key])
    need(type(registration['compose_files']) is list and all(type(p) is str for p in registration['compose_files']),
         'invalid retained compose paths')
    registration['compose_files'] = tuple(Path(p) for p in registration['compose_files'])
    registered = InstallationBinding(**registration)
    need(registered.topology == 'standalone' and not registered.production and not registered.profiles and
         registered.compose_files == (registered.checkout / 'docker-compose.yml',), 'unsupported retained installation')
    equal(source.binding_sha256, installation_binding_hash(registered), 'source enrollment binding differs')
    equal(snapshot['binding_sha256'], source.binding_sha256, 'snapshot enrollment binding differs')
    equal(snapshot['source_observation_sha256'], source.sha256, 'snapshot source observation differs')
    binding = snapshot['binding']
    _keys(binding, ('schema_version', 'installation_fingerprint', 'root', 'context', 'daemon', 'project', 'profiles',
                    'images', 'volumes', 'volume_root_ids', 'files', 'compose_sha256'), 'incomplete snapshot storage binding')
    need(type(binding['schema_version']) is int and binding['schema_version'] == 1, 'unsupported snapshot storage binding')
    equal((binding['installation_fingerprint'], binding['root'], binding['context'], binding['daemon'], binding['project'], binding['profiles']),
          (registered.installation_fingerprint, str(registered.checkout), registered.docker_context,
           registered.daemon_identity, registered.compose_project, []), 'snapshot storage belongs to another installation')
    _keys(binding['files'], CONFIG_FILES, 'incomplete snapshot configuration binding')
    need(all(_hex(v) for v in binding['files'].values()) and _hex(binding['compose_sha256']), 'invalid snapshot configuration hashes')
    equal(binding['files']['.env'], source.env_sha256, 'snapshot environment differs')
    for name in CONFIG_FILES[1:]:
        equal(binding['files'][name], fields['execution_deployment']['files'].get(name), 'snapshot retained deployment differs')
    expected_images = {}
    equal(set(source.product_images), {s for s, _ in layout.products}, 'source product roster differs')
    equal(set(source.dependency_images), set(layout.dependencies), 'source dependency roster differs')
    for service, component in layout.products:
        image = old['components'][component]
        expected_images[service] = (image['image'], image['digest'])
    dependencies = fields['dependencies']
    need(type(dependencies) is list and len(dependencies) == len(layout.dependencies) and
         {d['service'] for d in dependencies} == set(layout.dependencies), 'incomplete expected dependencies')
    expected_images.update({d['service']: (d['image'], d['digest']) for d in dependencies})
    _keys(binding['images'], expected_images, 'incomplete snapshot images')
    inspections = corpus.get('source_image_inspections')
    _keys(inspections, expected_images, 'missing independent source image inspections')
    from deploy.update.manifest import parse_image_ref
    for service, (image, digest) in expected_images.items():
        runtime_image = (source.product_images if service in source.product_images else source.dependency_images)[service][0]
        # Tags and immutable pins can name the same authenticated repository.
        equal((parse_image_ref(runtime_image.image).name, runtime_image.digest),
              (parse_image_ref(image).name, digest), 'snapshot source image digest differs')
        inspection = inspections[service]
        _keys(inspection, ('Id', 'Os', 'Architecture', 'RepoDigests'), 'incomplete source image inspection')
        need(type(inspection['RepoDigests']) is list and all(type(v) is str for v in inspection['RepoDigests']) and
             type(runtime_image.image_id) is str and re.fullmatch(r'sha256:[a-f0-9]{64}', runtime_image.image_id) and
             inspection['Id'] == runtime_image.image_id == binding['images'][service] and
             inspection['Os'] + '/' + inspection['Architecture'] == previous['platform'] and
             any(parse_image_ref(ref).name == parse_image_ref(image).name and
                 parse_image_ref(ref).digest == digest for ref in inspection['RepoDigests']),
             'snapshot image inspection belongs to another transition')
    expectation = snapshot['expectation']
    _keys(expectation, (f.name for f in dataclass_fields(BackupExpectation)), 'incomplete snapshot expectation')
    expected = BackupExpectation(expectation['attempt_id'], registered.installation_fingerprint, manifest_hash(target),
                                 old['version'], _instant(expectation['reserved_at']), layout.backup_domains)
    equal({k: expectation[k] for k in ('installation_fingerprint', 'target_manifest_hash', 'source_version', 'covered_domains')},
          dict(installation_fingerprint=expected.installation_fingerprint, target_manifest_hash=expected.target_manifest_hash,
               source_version=expected.source_version, covered_domains=list(expected.covered_domains)), 'snapshot belongs to another qualified transition')
    started, at, completed = (_instant(snapshot[k]) for k in ('started_at', 'snapshot_at', 'completed_at'))
    need(source.observed_at <= expected.reserved_at <= started <= at <= completed, 'snapshot source ordering differs')
    quiescence = snapshot['quiescence']
    _keys(quiescence, ('containers', 'start_times', 'restart_counts', 'restart_policies', 'daemon', 'volumes', 'roots', 'snapshot_at'),
          'incomplete snapshot quiescence')
    equal(sha(canonical_json(quiescence)), snapshot['quiescence_evidence_sha256'], 'snapshot quiescence hash differs')
    equal(quiescence, dict(containers=snapshot['containers'], start_times=quiescence['start_times'],
        restart_counts=dict(source.restart_counts), restart_policies=dict(source.restart_policies), daemon=binding['daemon'],
        volumes=binding['volumes'], roots=binding['volume_root_ids'], snapshot_at=snapshot['snapshot_at']), 'snapshot quiescence differs')
    _keys(snapshot['containers'], expected_images, 'incomplete snapshot container roster')
    _keys(quiescence['start_times'], expected_images, 'incomplete source start times')
    _keys(binding['volumes'], VOLUME_ROLES, 'incomplete snapshot volumes')
    _keys(binding['volume_root_ids'], VOLUME_ROLES, 'incomplete snapshot volume roots')
    _keys(snapshot['archives'], VOLUME_ROLES, 'incomplete snapshot archives')
    equal({c['Id'] for c in snapshot['containers'].values()}, set(source.restart_counts), 'snapshot source container IDs differ')
    for role, (service, destination) in VOLUMES.items():
        volume, archive = binding['volumes'][role], snapshot['archives'][role]
        _keys(volume, ('Name', 'Driver', 'Mountpoint', 'CreatedAt', 'Labels', 'Options', 'Scope'), 'incomplete snapshot volume identity')
        need(volume['Name'] == binding['project'] + '_' + role and volume['Driver'] == volume['Scope'] == 'local' and
             volume['Options'] in (None, {}) and type(volume['Mountpoint']) is str and volume['Mountpoint'].startswith('/') and
             type(volume['Labels']) is dict and volume['Labels'].get('com.docker.compose.project') == binding['project'] and
             volume['Labels'].get('com.docker.compose.volume') == role and bool(volume['CreatedAt']) and
             type(binding['volume_root_ids'][role]) is str and re.fullmatch(r'[0-9]+:[0-9]+', binding['volume_root_ids'][role]),
             'invalid snapshot volume identity')
        _keys(archive, ('size', 'sha256', 'members', 'logical_bytes', 'members_sha256', 'members_bytes', 'inventory_version', 'volume'),
              'incomplete snapshot archive')
        need(all(_integer(archive[k]) for k in ('size', 'members', 'logical_bytes', 'members_bytes')) and
             archive['size'] >= 1024 and archive['size'] % 512 == 0 and archive['members'] > 0 and archive['members_bytes'] > 0 and
             _hex(archive['sha256']) and _hex(archive['members_sha256']) and type(archive['inventory_version']) is int and
             archive['inventory_version'] == 2 and archive['volume'] == volume, 'invalid snapshot archive')
        container = snapshot['containers'][service]
        _keys(container, ('Id', 'Image', 'Created', 'Config', 'HostConfig', 'Mounts'), 'incomplete snapshot container identity')
        need(container['Image'] == binding['images'][service] and bool(container['Created']) and
             type(container['Config']) is dict and type(container['HostConfig']) is dict and
             container['HostConfig'].get('RestartPolicy', {}).get('Name') == source.restart_policies[container['Id']] and
             bool(quiescence['start_times'][service]), 'snapshot container identity differs')
        labels = container['Config'].get('Labels', {})
        need(labels.get('com.docker.compose.project') == binding['project'] and labels.get('com.docker.compose.service') == service and
             labels.get('com.docker.compose.project.working_dir') == binding['root'] and
             labels.get('com.docker.compose.project.config_files') == binding['root'] + '/docker-compose.yml' and
             labels.get('com.docker.compose.oneoff', '').lower() == 'false', 'snapshot container ownership differs')
        mounts = container['Mounts']
        need(type(mounts) is list and all(type(m) is dict for m in mounts), 'invalid snapshot mounts')
        expected_mounts = {destination: ('volume', volume['Mountpoint'], True)}
        expected_mounts.update({d: ('bind', p if p == '/dev/null' else binding['root'] + '/' + p, False)
                                for d, p in BINDS[service].items()})
        need(len(mounts) == len(expected_mounts) and {m['Destination']: (m['Type'], m['Source'], m['RW']) for m in mounts} == expected_mounts and
             all(m.get('Name') == volume['Name'] for m in mounts if m['Type'] == 'volume'), 'snapshot mount mapping differs')
    validate_source_preimages(corpus, source, binding, snapshot['containers'])
    records = snapshot['config_files']
    need(type(records) is list and len(records) == len(CONFIG_FILES), 'incomplete snapshot configuration')
    for index, (name, record) in enumerate(zip(CONFIG_FILES, records)):
        _keys(record, ('name', 'file', 'sha256', 'size', 'metadata'), 'incomplete snapshot configuration record')
        equal((record['name'], record['file'], record['sha256']), (name, 'config-' + str(index), binding['files'][name]),
              'snapshot configuration record differs')
        _keys(record['metadata'], ('uid', 'gid', 'mode', 'identity'), 'incomplete snapshot configuration metadata')
        need(_integer(record['size']) and all(_integer(record['metadata'][k]) for k in ('uid', 'gid', 'mode')) and
             record['metadata']['mode'] <= 0o7777 and type(record['metadata']['identity']) is list and
             len(record['metadata']['identity']) == 3 and
             all(type(v) is int and 0 <= v < 2**64 for v in record['metadata']['identity'][:2]) and
             type(record['metadata']['identity'][2]) is int and -(2**63) <= record['metadata']['identity'][2] < 2**63,
             'invalid snapshot configuration metadata')
    seal_doc = copy.deepcopy(corpus.get('source_backup_seal'))
    _keys(seal_doc, (f.name for f in dataclass_fields(BackupSeal)), 'missing complete snapshot seal')
    seal_doc.update(snapshot_at=_instant(seal_doc['snapshot_at']), authorised_at=_instant(seal_doc['authorised_at']))
    seal = BackupSeal(**seal_doc)
    equal(seal.inventory_sha256, sha(canonical_json(snapshot)), 'retained snapshot seal differs')
    proof_doc = corpus.get('source_backup_proof')
    need(type(proof_doc) is dict, 'missing retained snapshot proof')
    proof = validate_backup_proof(canonical_json(proof_doc), expected, now=seal.authorised_at, seal=seal, for_recovery=True)
    need(proof.schema_version == 2 and proof.binding_sha256 == source.binding_sha256 and
         proof.source_observation_sha256 == source.sha256 and proof.quiescence_evidence_sha256 == snapshot['quiescence_evidence_sha256'],
         'retained snapshot proof differs')
    equal((proof.started_at, proof.snapshot_at, proof.completed_at), (started, at, completed), 'snapshot proof ordering differs')
    return _snapshot_volume_bytes(snapshot, corpus.get('source_snapshot_members'))


def validate_executed_measurements(fields, corpus, *, target, predecessor_raw):
    """Bind signed A6 metrics back to complete private executed samples.

    `resource_samples` is an ordered list of {app_label,name,engine,risk_class,
    tables,duration_seconds,samples}; every sample includes measured RSS. Transfer
    records contain reference_volume_bytes, started_monotonic, completed_monotonic,
    snapshot_generation and measurement_basis; byte counts are independently
    rederived from the complete sealed member trees, not these repeated claims.
    `target_pending_plan` is measured BEFORE normal target
    startup, not reconstructed from a successful post-start applied inventory.
    """
    required = {'target_pending_plan', 'pending_migration_tables', 'backup_transfer', 'restore_transfer',
                'large_volume_reference', 'source_snapshot_inventory'}
    need(type(corpus) is dict and required <= set(corpus), 'unrun resource/transfer/reference measurements')
    reference = corpus['large_volume_reference']
    actual_tables = validate_reference_inventory(reference, pending_migration_tables=corpus['pending_migration_tables'],
                                                plan=corpus['target_pending_plan'])
    snapshot = corpus['source_snapshot_inventory']
    try:
        volume_bytes = validate_snapshot_evidence(fields, corpus, target=target, predecessor_raw=predecessor_raw)
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        raise RehearsalError('invalid retained snapshot evidence') from None
    equal(reference['snapshot_generation'], sha(canonical_json(snapshot)),
          'reference inventory belongs to another cold snapshot generation')
    plan = corpus['target_pending_plan']
    need(type(plan) is list and len(plan) == fields['migration_evidence']['target_pending_plan_count']
         and pending_migration_plan_hash(plan) == fields['migration_evidence']['target_pending_plan_sha256'],
         'measured pre-start pending plan differs')
    if type(fields['migration_measurements']) is dict:
        from scripts.update_rehearsal_qualification import validate_bootstrap_envelope
        need('resource_samples' not in corpus, 'mixed migration measurement modes')
        validate_bootstrap_envelope(fields, corpus, target, actual_tables)
    else:
        need('bootstrap_envelope' not in corpus, 'mixed migration measurement modes')
        samples = corpus.get('resource_samples')
        need(type(samples) is list and len(samples) == len(plan), 'incomplete per-migration samples')
        reduced = []
        for identity, item, scope in zip(plan, samples, corpus['pending_migration_tables']):
            need(type(item) is dict and set(item) == {'app_label', 'name', 'engine', 'risk_class',
                 'tables', 'duration_seconds', 'samples', 'snapshot_generation', 'started_monotonic',
                 'completed_monotonic'}, 'incomplete migration resource samples')
            equal({k: item[k] for k in ('app_label', 'name')}, identity, 'resource sample plan order differs')
            equal(item['snapshot_generation'], reference['snapshot_generation'], 'migration reference generation differs')
            need(type(item['tables']) is list, 'missing measured migration tables')
            for table in item['tables']:
                need(type(table) is dict and set(table) == {'table', 'reference_rows', 'reference_bytes'},
                     'invalid measured migration table')
                observed = actual_tables.get((item['engine'], table['table']))
                need(observed is not None and table['reference_rows'] == observed['rows'] and
                     table['reference_bytes'] == observed['bytes'], 'migration work differs from independent reference scale')
            expected_tables = {(t['engine'], t['table']) for t in scope['tables'] if (t['engine'], t['table']) in actual_tables}
            equal({(item['engine'], t['table']) for t in item['tables']}, expected_tables,
                  'migration at-scale table coverage differs from pre-start analysis')
            reduced.append(migration_measurement(identity, item['tables'], item['samples'], engine=item['engine'],
                risk_class=item['risk_class'], duration_seconds=item['duration_seconds'],
                started_monotonic=item['started_monotonic'], completed_monotonic=item['completed_monotonic']))
        equal(reduced, fields['migration_measurements'], 'signed migration metrics differ from raw samples')
    for role in ('backup', 'restore'):
        raw = corpus[role + '_transfer']
        _keys(raw, ('reference_volume_bytes', 'started_monotonic', 'completed_monotonic', 'snapshot_generation', 'measurement_basis'),
              'incomplete transfer timing')
        equal(raw['snapshot_generation'], reference['snapshot_generation'], 'transfer belongs to another snapshot generation')
        equal(raw['measurement_basis'], TRANSFER_BASIS, 'transfer measurement basis differs')
        _keys(raw['reference_volume_bytes'], VOLUME_ROLES, 'incomplete transfer byte measurements')
        need(all(_integer(v) for v in raw['reference_volume_bytes'].values()), 'invalid transfer byte measurements')
        equal(raw['reference_volume_bytes'], volume_bytes, 'transfer bytes differ from independently captured snapshot members')
        start, end = raw['started_monotonic'], raw['completed_monotonic']
        need(type(start) in (int, float) and math.isfinite(start) and start >= 0
             and type(end) in (int, float) and math.isfinite(end) and end > start, 'invalid transfer timing')
        expected = {'reference_volume_bytes': raw['reference_volume_bytes'],
                    'throughput_bytes_per_second': sum(raw['reference_volume_bytes'].values()) / (end - start)}
        equal(expected, fields[role + '_measurement'], 'signed transfer rate differs from executed timing')
    transition_measurements(fields)


def measure_transfer(operation, capture_snapshot, *, clock=time.monotonic):
    """Time the shipping operation and capture its inventory + independent trees.

    capture_snapshot() runs AFTER the operation and returns (inventory, members)
    read from the sealed generation. In particular cold backup cannot be given a
    guessed pre-backup byte count. Return signed metrics AND retained raw timing;
    both consumers rederive the numerator from these trees before packaging.
    """
    need(callable(operation) and callable(capture_snapshot), 'unmeasured reference volumes')
    started = clock()
    result = operation()
    completed = clock()
    duration = completed - started
    need(_positive(duration), 'unmeasured transfer duration')
    snapshot, members = capture_snapshot()
    _keys(snapshot.get('archives'), VOLUME_ROLES, 'incomplete snapshot archives')
    volume_bytes = _snapshot_volume_bytes(snapshot, members)
    raw = dict(reference_volume_bytes=volume_bytes, started_monotonic=started, completed_monotonic=completed,
               snapshot_generation=sha(canonical_json(snapshot)), measurement_basis=TRANSFER_BASIS)
    return result, {'reference_volume_bytes': dict(volume_bytes),
                    'throughput_bytes_per_second': sum(volume_bytes.values()) / duration}, raw


class VolumeRoleTransfers:
    """Time actual per-role shipping transfers, not a divided aggregate duration.

    The backup/restore adapter wraps each archive/extraction operation with
    measure(). finish() obtains its byte numerator from the sealed complete tree.
    Failed, repeated or absent roles have no measurement. These private rows do
    not extend the public schema: the signed aggregate remains conservative and
    host preflight already scales by the largest per-role volume ratio.
    """
    def __init__(self, *, clock=time.monotonic):
        self.clock, self.timings, self.failed = clock, {}, False

    def measure(self, role, operation):
        need(role in VOLUME_ROLES and role not in self.timings and callable(operation),
             'duplicate or unknown volume transfer')
        self.timings[role] = None
        started = self.clock()
        try:
            result = operation()
            completed = self.clock()
            need(_positive(completed - started), 'unmeasured volume transfer duration')
            self.timings[role] = dict(started_monotonic=started, completed_monotonic=completed)
            return result
        except BaseException:
            self.failed = True
            raise

    def finish(self, snapshot, members):
        need(not self.failed and set(self.timings) == set(VOLUME_ROLES) and
             all(value is not None for value in self.timings.values()), 'unrun volume role transfers')
        sizes = _snapshot_volume_bytes(snapshot, members)
        return dict(snapshot_generation=sha(canonical_json(snapshot)), measurement_basis=TRANSFER_BASIS,
            roles={role: dict(self.timings[role], logical_bytes=sizes[role],
                throughput_bytes_per_second=sizes[role] / (self.timings[role]['completed_monotonic'] -
                                                         self.timings[role]['started_monotonic']))
                for role in VOLUME_ROLES})


def validate_volume_role_transfers(fields, corpus):
    """A9 requires independently timed backup AND restore for every volume role."""
    records = corpus.get('volume_role_transfers')
    if type(records) is not dict or set(records) != {'backup', 'restore'}:
        raise ManualRelease('transfer_measurement_unavailable')
    snapshot = corpus['source_snapshot_inventory']
    sizes = _snapshot_volume_bytes(snapshot, corpus['source_snapshot_members'])
    generation = sha(canonical_json(snapshot))
    for action in ('backup', 'restore'):
        record, aggregate = records[action], corpus[action + '_transfer']
        _keys(record, ('snapshot_generation', 'measurement_basis', 'roles'), 'invalid volume transfer evidence')
        equal((record['snapshot_generation'], record['measurement_basis']), (generation, TRANSFER_BASIS),
              'volume transfer generation or basis differs')
        _keys(record['roles'], VOLUME_ROLES, 'incomplete volume transfer roles')
        for role in VOLUME_ROLES:
            item = record['roles'][role]
            _keys(item, ('started_monotonic', 'completed_monotonic', 'logical_bytes',
                         'throughput_bytes_per_second'), 'invalid per-role transfer measurement')
            start, end = item['started_monotonic'], item['completed_monotonic']
            need(type(start) in (int, float) and math.isfinite(start) and start >= 0 and
                 type(end) in (int, float) and math.isfinite(end) and end > start,
                 'invalid per-role transfer interval')
            need(aggregate['started_monotonic'] <= start < end <= aggregate['completed_monotonic'],
                 'volume transfer outside actual shipping operation')
            equal(item['logical_bytes'], sizes[role], 'volume transfer bytes differ from sealed trees')
            rate = sizes[role] / (end - start)
            need(type(item['throughput_bytes_per_second']) in (int, float) and
                 math.isfinite(item['throughput_bytes_per_second']) and
                 item['throughput_bytes_per_second'] == rate, 'volume transfer rate differs from execution')
        equal(fields[action + '_measurement']['reference_volume_bytes'], sizes,
              'signed transfer volume roles differ')


def interrupt_updater(process, *, boundary, checkpoint, restart):
    """Actually SIGKILL the updater and require a fresh recovery process.

    checkpoint is the caller's authenticated durable ledger read, not a timeout
    or a NotReady exception. The runtime must prove remote children quiescent
    through its own ledger before destructive recovery. Never clear ambiguity.
    """
    need(process.pid > 1 and process.pid != os.getpid() and process.poll() is None, 'updater not running')
    need(checkpoint() == boundary, 'durable interruption boundary not reached')
    os.kill(process.pid, signal.SIGKILL)
    need(process.wait(timeout=30) == -signal.SIGKILL, 'updater process not terminated')
    recovered = restart()
    need(recovered.pid != process.pid and recovered.pid > 1, 'fresh recovery process required')
    return recovered


@dataclass(frozen=True)
class Budgets:
    startup_seconds: int = 4200
    total_seconds: int = 19800
    stability_seconds: int = 60

    def __post_init__(self):
        need(type(self.startup_seconds) is int and 60 <= self.startup_seconds <= 7200
             and type(self.total_seconds) is int and 60 <= self.total_seconds <= 21000
             and type(self.stability_seconds) is int and 1 <= self.stability_seconds <= 3600,
             'invalid rehearsal budget')


EXECUTOR_REQUIREMENTS = (
    'lock_held_updater_automatic_transaction',
    'real_loader_pending_plan_and_table_scope',
    'touched_registered_table_reference_workloads',
    'normal_bootstrap_boundaries_and_genuine_resource_measurements',
    'post_migration_failure_shipping_restore',
    'durable_boundary_process_termination_and_fresh_recovery',
    'extracted_tree_config_and_corruption_assertions',
)


def shipping_factory():
    """Private checked-in runtime API, never a customer CLI/admission bypass.

    Required factory signature:
      create_disposable_rehearsal(workspace, row, target, *, docker_context,
        resource_prefix, budgets, deadline, registry) -> context manager

    The runtime must exclusively create fresh owned resources, inspect their
    exact identities, reject pre-existing/external/foreign resources, use the
    SAME Updater transaction and StandaloneRestore, retain original volume
    objects for failure/interruption cases, and verify cleanup. Its context
    exposes seed_reference_table/measure_reference_table, suite_assertions,
    measurements, private_evidence, dependencies, cleanup_verified. Each
    suite_assertions(suite) returns {required_name: executable_assertion}; not
    precomputed booleans/JSON. measurements includes the seven MEASURED_FIELDS
    except executor/evidence digests, which this producer computes itself.
    """
    try:
        module = importlib.import_module('scripts.update_rehearsal_shipping')
    except ModuleNotFoundError as exc:
        if exc.name != 'scripts.update_rehearsal_shipping':
            raise
        raise ManualRelease('shipping_executor_unavailable') from None
    factory = getattr(module, 'create_disposable_rehearsal', None)
    if (not callable(factory) or getattr(module, 'REHEARSAL_CONTRACT', None) != SHIPPING_REHEARSAL_CONTRACT
            or getattr(module, 'SHIPPING_TRANSACTION_INTEGRATED', None) is not True):
        raise ManualRelease('shipping_executor_unavailable')
    return factory


def exercise(executor, *, deadline):
    """One target direct start; actual crash and shipping restore assertion sets."""
    layout, cases = topology_profile('standalone', ()), ExecutedCases()
    executor.executed_cases = cases
    for suite in SUITES:
        cases.active_suite = suite
        if time.monotonic() >= deadline:
            raise ManualRelease('suite_timed_out')
        assertions = executor.suite_assertions(suite)
        need(type(assertions) is dict and set(assertions) == required_cases(suite, layout), 'unrun required assertions')
        for name, assertion in assertions.items():
            if time.monotonic() >= deadline:
                raise ManualRelease('suite_timed_out')
            cases.check(suite, name, assertion)
            if time.monotonic() >= deadline:
                raise ManualRelease('suite_timed_out')
    cases.complete()
    return cases


def measured_policy(bindings, target):
    need(bindings and len(bindings) <= ROW_LIMIT, 'no complete measurements')
    compatibility = copy.deepcopy(target['compatibility'])
    need(compatibility['contract_version'] == 2, 'restore contract required')
    compatibility.update(automatic_apply=True, requires_operator_initialization=False,
        predecessors=[b['predecessor'] for b in bindings], automatic_rollback={'strategy': 'restore_backup'}, validation=None)
    return {'schema_version': 1, 'migration': copy.deepcopy(target['migration']), 'compatibility': compatibility}


def _validate_context(context, workspace):
    if sys.platform == 'darwin':
        need(context == 'colima-fagi-update', 'dedicated Docker context required')
        need(Path(workspace).resolve().is_relative_to(Path('/Users/nikhilpareek')), 'home-only workspace required')
    else:
        # Only an isolated native Actions daemon may use its default context.
        need(context == 'colima-fagi-update' or (context == 'default'
             and os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ARCH') == 'X64'),
             'isolated native Actions daemon required')


def failure_diagnostics(exc, executor=None, *, completed_rows=0):
    """Bounded closed diagnostic data; never stringify subprocess/product errors.

    Preserve typed runtime refusals and our fixed assertion messages, not SQL,
    env values, downloaded exception messages or arbitrary command output.
    Diagnostic phase/suite data is not affirmative qualification evidence.
    """
    executed = getattr(executor, 'executed_cases', None)
    counts = {s: len(executed.cases[s]) if isinstance(executed, ExecutedCases) else 0 for s in SUITES}
    failed = executed.failed if isinstance(executed, ExecutedCases) else None
    active = executed.active_suite if isinstance(executed, ExecutedCases) else None
    layout = topology_profile('standalone', ())
    states = {s: ('failed' if failed and failed[0] == s else
                  'passed' if counts[s] == len(required_cases(s, layout)) else
                  'incomplete' if counts[s] or s == active else 'unrun') for s in SUITES}
    reason = str(exc) if isinstance(exc, ManualRelease) else 'suite_failed'
    if reason not in MANUAL_REASONS:
        reason = 'suite_failed'
    from deploy.update.apply import Interrupted
    from deploy.update.recovery import RecoveryError
    from deploy.update.standalone_backup import BackupError
    from scripts.update_rehearsal_resources import ResourceObservationError
    from scripts.update_rehearsal_spool import ObservedSpoolWitnessError
    code = getattr(exc, 'reason_code', getattr(exc, 'refusal_code', None))
    if code is None and isinstance(exc, (BackupError, RecoveryError, ResourceObservationError)):
        code = exc.code
    code = getattr(code, 'value', code)
    if isinstance(exc, (Interrupted, KeyboardInterrupt)):
        code = 'operation_interrupted'
    if not isinstance(code, str) or re.fullmatch(r'[a-z_]{1,80}', code) is None:
        code = reason
    result = dict(schema_version=1, producer=PRODUCER, eligible=False, reason=reason,
        refusal_code=code, exception_type=type(exc).__name__[:80], completed_rows=completed_rows,
        suites=states, executed_assertions=counts, failed_assertion=list(failed) if failed else None,
        active_suite=active, assertion_timings=executed.timings if isinstance(executed, ExecutedCases) else [])
    if isinstance(exc, RehearsalError):
        # Only this producer's own fixed diagnostics, not third-party stderr.
        result['assertion_detail'] = str(exc)[:512]
    if isinstance(exc, ObservedSpoolWitnessError):
        result['assertion_detail'] = exc.safe_detail
    if isinstance(exc, ResourceObservationError):
        # Only a line in our own fixed reader, never frame locals or host paths.
        trace = exc.__traceback__
        while trace is not None:
            frame_code = trace.tb_frame.f_code
            if (frame_code.co_filename == ResourceObservationError.__init__.__code__.co_filename
                    and frame_code.co_name not in ('_need', '__init__')):
                result['assertion_detail'] = 'update_rehearsal_resources.py:'+str(trace.tb_lineno)
            trace = trace.tb_next
    if isinstance(exc, CommandError):
        result['command'] = dict(executable=exc.command_name, exit_code=exc.returncode)
    if reason == 'shipping_executor_unavailable':
        # A fixed integration checklist, not external success data or waivers.
        result['missing_executor_requirements'] = list(EXECUTOR_REQUIREMENTS)
    return result


def retain_diagnostics(output, executor=None, *, failure=None):
    """Separate CI-readable bounded diagnostics from private backup/witness data."""
    data = dict(schema_version=1, qualification_evidence=False)
    if executor is not None and callable(getattr(executor, 'diagnostics', None)):
        try:
            data['product'] = executor.diagnostics()
        except Exception as exc:
            data['collection'] = dict(status='unavailable', exception_type=type(exc).__name__)
    executed = getattr(executor, 'executed_cases', None)
    if isinstance(executed, ExecutedCases):
        data['assertion_timings'] = executed.timings
    if failure is not None:
        data['failure'] = failure
    raw = canonical_json(data)
    need(len(raw) <= MAX_EVIDENCE_BYTES, 'diagnostic size bound')
    write_pair(((output / 'diagnostics.json', raw),))


def collect_executed_row(row, target, executor, cases, output, *, index, registry, scope,
                         reference, executor_sha, require_product_measurements=True):
    """One private, complete row; never promote stage-status JSON to assertions."""
    cases.complete()
    from deploy.update.apply import Updater
    from deploy.update.standalone_restore import StandaloneRestore
    need(executor.shipping_executor_class is Updater and executor.shipping_restore_class is StandaloneRestore,
         'shipping executor/restore required')
    fields = copy.deepcopy(executor.measurements())
    need(set(fields) == set(MEASURED_FIELDS) - {'executor_sha256', 'evidence_inventory_sha256'},
         'incomplete measurements')
    transition_measurements(fields)
    binding = row.binding()
    binding.update(fields, executor_sha256=executor_sha, dependencies=list(executor.dependencies))
    corpus = executor.private_evidence()
    need(type(corpus) is dict and {'source_witnesses', 'restored_witnesses'} <= set(corpus),
         'incomplete private evidence')
    equal(corpus.get('pending_migration_tables'), scope, 'pre-start table analysis changed during rehearsal')
    measured_reference = validate_reference_inventory(corpus.get('large_volume_reference'), registry=registry,
        pending_migration_tables=scope, plan=corpus.get('target_pending_plan'))
    for table in reference:
        equal(measured_reference.get((table['engine'], table['table'])), table,
              'seed measurement differs from snapshot reference')
    validate_executed_measurements(binding, corpus, target=target, predecessor_raw=row.release.raw_manifest)
    if require_product_measurements:
        validate_scope_probe(fields, corpus, target)
        validate_volume_role_transfers(fields, corpus)
    source = witness_inventory(corpus['source_witnesses'])
    equal(witness_inventory(corpus['restored_witnesses']), source, 'restored witness namespace differs')
    inventory = {}
    total = 0
    for key, value in corpus.items():
        need(type(key) is str and re.fullmatch(r'[a-z][a-z0-9_]{0,60}', key), 'invalid private evidence key')
        name, raw = str(index) + '-' + key + '.json', canonical_json({'value': value})
        total += len(raw)
        need(total <= MAX_EVIDENCE_BYTES, 'private evidence too large')
        write_pair(((output / name, raw),))
        inventory[name] = sha(raw)
    binding.update(evidence_inventory=inventory, evidence_inventory_sha256=inventory_hash(inventory))
    return binding


def publish_executed_rows(target, rows, bindings, results, output, *, run_id, implementation,
                          purpose='product', executor=None):
    """The only affirmative writer. All cases, measurements and cleanup precede it."""
    need(len(rows) == len(bindings) == len(results) and bool(rows), 'unrun predecessor matrix')
    need(executor is not None and executor.cleanup_verified is True, 'disposable resource cleanup unverified')
    for result in results:
        result.complete()
    equal(implementation_inventory(), implementation, 'executor changed during rehearsal')
    policy = measured_policy(bindings, target)
    approved = copy.deepcopy(target)
    approved.update(compatibility=policy['compatibility'])
    common = {'inputs_hash': inputs_hash(approved), 'repository': REPOSITORY, 'run_id': str(run_id),
        'lane': 'product' if purpose == 'product' else 'pr-validation',
        'candidate_sha256': sha(canonical_json(target)), 'producer_sha256': sha(Path(__file__).read_bytes()),
        'executor_sha256': inventory_hash(implementation),
        'high_volume_registry_sha256': sha(read_bytes(ROOT / 'deploy/update/high-volume-tables.json', MAX_EVIDENCE_BYTES))}
    evidence_rows = []
    for index, (row, binding, result) in enumerate(zip(rows, bindings, results)):
        properties, reports = dict(common, binding_hash=manifest_hash(binding)), {}
        for suite in SUITES:
            raw = result.render(suite, topology_profile('standalone', ()), properties)
            name = str(index) + '-' + suite + '.xml'
            write_pair(((output / name, raw),))
            reports[suite] = {'path': name, 'sha256': sha(raw)}
        evidence_rows.append(dict(binding, reports=reports))
        key = binding['predecessor']['raw_manifest_sha256']
        write_pair(((output / (key + '.json'), row.release.raw_manifest),
                    (output / (key + '.sigstore.json'), row.release.raw_bundle)))
    envelope = dict(common, schema_version=2, run_id=run_id, rows=evidence_rows, executor_inventory=implementation)
    retain_diagnostics(output, executor)
    write_pair(((output / 'policy.json', canonical_json(policy)), (output / 'evidence.json', canonical_json(envelope))))
    return policy


def milestone_api():
    """The parallel lane's sole dispatch contract; never hardcode CLI choices."""
    module = importlib.import_module('scripts.update_rehearsal_milestone')
    names = getattr(module, 'MILESTONES', None)
    need(type(names) is tuple and names and len(set(names)) == len(names) and
         all(type(n) is str and re.fullmatch(r'[a-z][a-z0-9_]{0,63}', n) for n in names),
         'milestone dispatch contract differs')
    if not callable(getattr(module, 'run_milestone', None)):
        raise ManualRelease('shipping_executor_unavailable')
    return module


SIX_SUITES_PLANNED = {suite: 'planned' for suite in SUITES}


def milestone_plans(module, args, directory):
    """Daemon-free job plan of EVERY milestone, through run_milestone only.

    Each dry run writes its own new directory; nothing is provisioned. The
    plans are DATA about what a real dispatch would execute, never evidence.
    """
    directory.mkdir(mode=0o700)
    plans = {}
    for name in module.MILESTONES:
        planned = argparse.Namespace(**vars(args))
        planned.through, planned.dry_run, planned.out = name, True, directory / name
        code = module.run_milestone(name, planned)
        need(type(code) is int and code == 0, 'milestone plan unavailable')
        plan = decode_document(read_bytes(directory / name / 'job-plan.json', MAX_EVIDENCE_BYTES), MAX_EVIDENCE_BYTES)
        need(type(plan) is dict and plan.get('milestone') == name, 'milestone plan identity differs')
        plans[name] = plan
    return plans


def select_qualification_milestone(plans, *, source_ref, target_ref, platform):
    """(name, None, summary) only for ONE milestone planning all six suites on this pair.

    A milestone qualifies for real dispatch only when its own job plan pins
    exactly the requested source and target digests and platform AND plans all
    six suites in that one session (qualification_suites == 'planned' for each).
    Otherwise (None, reason, summary) refuses BEFORE any Docker work: no six-
    suite plan at all is shipping_executor_unavailable; a six-suite plan pinned
    to another pair is suite_unrun for the requested pair.
    """
    from deploy.update.manifest import parse_image_ref

    def pin(ref):
        try:
            parsed = parse_image_ref(ref)
        except Exception:
            return None
        return (parsed.repository, parsed.digest) if parsed.digest else None

    requested = (pin(source_ref), pin(target_ref), platform)
    need(None not in requested, 'requested pair must be digest pinned')
    summary, six, matching = {}, [], []
    for name, plan in plans.items():
        pinned = (pin(plan.get('source_image')), pin(plan.get('target_image')), plan.get('platform'))
        suites = plan.get('qualification_suites')
        summary[name] = dict(source_image=plan.get('source_image'), target_image=plan.get('target_image'),
            platform=plan.get('platform'), qualification_suites=copy.deepcopy(suites),
            pins_requested_pair=pinned == requested, plans_six_suites=suites == SIX_SUITES_PLANNED)
        if suites == SIX_SUITES_PLANNED:
            six.append(name)
            if pinned == requested:
                matching.append(name)
    if not six:
        return None, 'shipping_executor_unavailable', summary
    if len(matching) != 1:
        return None, 'suite_unrun', summary
    return matching[0], None, summary


class QualificationSession:
    """In-process six-suite sink for run_milestone(name, args) -> int.

    Full qualify dispatches ONE milestone for real: the one whose job plan pins
    the requested pair and plans all six suites (see select_qualification_
    milestone). It passes this sink at args.qualification, with args.row/target/
    budgets/registry/deadline/workspace. A milestone status file or zero exit is
    NOT evidence. In that one runner session the stage driver must:
      1. prepare_reference(executor) BEFORE its cold snapshot (A9 scope + seed);
      2. check(suite, case, callable) AT each actual assertion, in SUITES order,
         for every required_cases(suite, standalone) name, raising on failure;
      3. capture(executor) after all six suites, while its stack still exists;
      4. keep the executor until its verified cleanup (cleanup_verified=True).
    The executor exposes shipping_executor_class=Updater, shipping_restore_class=
    StandaloneRestore, dependencies, pending_migration_tables(), seed_reference_
    table(entry, rows), measure_reference_table(entry), measurements() and
    private_evidence() (including pending_table_scope_probe from
    pending_table_scope_record() and volume_role_transfers from
    VolumeRoleTransfers). finish() runs only after run_milestone returns 0;
    cleanup, every assertion and raw A9 measurements are revalidated before any
    proof row. Bounded --through runs never receive this sink.
    """
    def __init__(self, row, target, output, *, run_id, deadline, registry, index=0, output_exists=False):
        self.row, self.target, self.output = row, copy.deepcopy(target), output
        self.run_id, self.deadline, self.registry, self.index = run_id, deadline, registry, index
        self.executed_cases = ExecutedCases()
        self.implementation = implementation_inventory()
        self.milestone_sha256 = sha(read_bytes(ROOT / 'scripts/update_rehearsal_milestone.py', MAX_EVIDENCE_BYTES))
        self.harness_sha256 = sha(read_bytes(ROOT / 'deploy/tests/update_e2e/real_automatic.py', MAX_EVIDENCE_BYTES))
        self.executor = self.binding = self.scope = self.reference = None
        self.shipping_executor_available = False
        if not output_exists:
            output.mkdir(mode=0o700)
        else:
            need(output.is_dir() and not output.is_symlink(), 'qualification output unavailable')

    def _budget(self):
        if time.monotonic() >= self.deadline:
            raise ManualRelease('suite_timed_out')

    def prepare_reference(self, executor):
        self._budget()
        need(self.executor is None, 'qualification executor is single use')
        self.executor = executor
        executor.executed_cases = self.executed_cases
        self.scope = copy.deepcopy(executor.pending_migration_tables())
        selected = select_reference_registry(self.registry, self.scope)
        self.reference = seed_reference_tables(selected, executor.seed_reference_table, executor.measure_reference_table)
        self._budget()

    def check(self, suite, name, assertion):
        self._budget()
        need(self.executor is not None and self.binding is None, 'qualification assertion outside execution')
        layout = topology_profile('standalone', ())
        need(suite in SUITES and name in required_cases(suite, layout), 'unknown qualification assertion')
        index = SUITES.index(suite)
        need(all(set(self.executed_cases.cases[s]) == required_cases(s, layout) for s in SUITES[:index]) and
             not any(self.executed_cases.cases[s] for s in SUITES[index + 1:]), 'qualification suite order differs')
        self.executed_cases.active_suite = suite
        self.executed_cases.check(suite, name, assertion)
        self._budget()

    def capture(self, executor):
        self._budget()
        need(executor is self.executor and self.binding is None, 'qualification executor changed')
        self.executed_cases.complete()
        self.binding = collect_executed_row(self.row, self.target, executor, self.executed_cases,
            self.output, index=self.index, registry=self.registry, scope=self.scope, reference=self.reference,
            executor_sha=inventory_hash(self.implementation))
        # Bind the exact stage adapter as private hash-bound provenance in
        # addition to the installed shipping executor's independently known hash.
        raw = canonical_json({'value': dict(run_id=self.run_id, candidate_sha256=sha(canonical_json(self.target)),
            implementation_sha256=self.milestone_sha256, harness_sha256=self.harness_sha256)})
        name = str(self.index) + '-qualification_driver.json'
        need(name not in self.binding['evidence_inventory'], 'duplicate qualification driver evidence')
        write_pair(((self.output / name, raw),))
        self.binding['evidence_inventory'][name] = sha(raw)
        self.binding['evidence_inventory_sha256'] = inventory_hash(self.binding['evidence_inventory'])

    def diagnostics(self):
        result = dict(shipping_executor_available=self.shipping_executor_available,
                      stage_sink_connected=self.executor is not None,
                      measurements_captured=self.binding is not None)
        if self.executor is not None and callable(getattr(self.executor, 'diagnostics', None)):
            result['product'] = self.executor.diagnostics()
        return result

    def validate_finished(self):
        self._budget()
        self.executed_cases.complete()
        if self.binding is None or self.executor is None:
            raise ManualRelease('migration_measurement_unavailable')
        need(self.executor.cleanup_verified is True, 'disposable resource cleanup unverified')
        equal(sha(read_bytes(ROOT / 'scripts/update_rehearsal_milestone.py', MAX_EVIDENCE_BYTES)),
              self.milestone_sha256, 'milestone implementation changed during qualification')
        equal(sha(read_bytes(ROOT / 'deploy/tests/update_e2e/real_automatic.py', MAX_EVIDENCE_BYTES)),
              self.harness_sha256, 'qualification harness changed during qualification')
        equal(implementation_inventory(), self.implementation, 'executor changed during rehearsal')

    def finish(self):
        self.validate_finished()
        policy = publish_executed_rows(self.target, [self.row], [self.binding], [self.executed_cases], self.output,
            run_id=self.run_id, implementation=self.implementation, executor=self.executor)
        self.shipping_executor_available = True
        return policy


def produce_product(target, rows, workspace, output, *, run_id, docker_context, budgets, deadline, registry, progress):
    """Dispatch each authenticated row through the same six-suite sink as qualify.

    All plans are checked before provisioning. No policy or envelope is written
    until every row, assertion, measurement, cleanup and implementation seal passes.
    """
    from deploy.update.manifest import parse_image_ref

    def image_pin(manifest):
        component = manifest['components']['standalone']
        image = component['image']
        return image if parse_image_ref(image).digest else image + '@' + component['digest']

    need(0 < len(rows) <= ROW_LIMIT, 'unrun predecessor matrix')
    module = milestone_api()
    plans_root = workspace / 'product-plans'
    plans_root.mkdir(mode=0o700)
    dispatches = []
    for index, row in enumerate(rows):
        args = argparse.Namespace(source_ref=image_pin(row.release.manifest), target_ref=image_pin(target),
            platform=row.platform, deployment_dir=row.deployment_directory, reference_scale='registered-100k',
            boot_timeout=budgets.startup_seconds, docker_context=docker_context, run_id=run_id)
        plans = milestone_plans(module, args, plans_root / str(index))
        selected, reason, _ = select_qualification_milestone(plans, source_ref=args.source_ref,
            target_ref=args.target_ref, platform=args.platform)
        if selected is None:
            raise ManualRelease(reason)
        dispatches.append((row, args, selected))
    sessions = []
    for index, (row, args, selected) in enumerate(dispatches):
        session = QualificationSession(row, target, output, run_id=run_id, deadline=deadline,
            registry=registry, index=index, output_exists=True)
        progress['session'] = session
        args.through, args.dry_run = selected, False
        args.out = workspace / ('product-milestone-' + str(index))
        args.qualification, args.row, args.target = session, row, copy.deepcopy(target)
        args.workspace = workspace / ('product-row-' + str(index))
        args.workspace.mkdir(mode=0o700)
        args.registry, args.budgets, args.deadline = registry, budgets, deadline
        code = module.run_milestone(selected, args)
        need(type(code) is int, 'invalid milestone exit status')
        if code != 0:
            raise ManualRelease('suite_failed')
        session.validate_finished()
        sessions.append(session)
        progress['completed_rows'] = len(sessions)
    for session in sessions:
        session.validate_finished()
        equal(session.implementation, sessions[0].implementation, 'executor changed between rows')
    return publish_executed_rows(target, rows, [s.binding for s in sessions],
        [s.executed_cases for s in sessions], output, run_id=run_id,
        implementation=sessions[0].implementation, executor=sessions[-1].executor)


def produce(target, rows, workspace, output, *, run_id, docker_context='colima-fagi-update',
            cosign='cosign', budgets=None, purpose='product'):
    """Publish no affirmative bytes until ALL exact rows and cleanup pass."""
    need(type(run_id) is int and 0 < run_id <= 9007199254740991, 'invalid run identity')
    validate_manifest(target)
    need(target.get('compatibility', {}).get('automatic_apply') is False
         and target['compatibility']['contract_version'] == 2
         and target['compatibility']['validation'] is None, 'candidate must be frozen and uncertified')
    need(purpose in ('product', 'pr-validation'), 'invalid rehearsal purpose')
    _validate_context(docker_context, workspace)
    need(not output.exists(), 'evidence outputs are create-only')
    output.mkdir(mode=0o700, parents=True)
    budgets = budgets or Budgets()
    deadline = time.monotonic() + budgets.total_seconds
    bindings, results, qualified_rows = [], [], []
    executor, progress = None, {}
    try:
        registry = load_high_volume_registry()
        if purpose == 'product':
            return produce_product(target, rows, workspace, output, run_id=run_id,
                docker_context=docker_context, budgets=budgets, deadline=deadline, registry=registry, progress=progress)
        factory = shipping_factory()  # PR-only synthetic execution remains ineligible
        need(0 < len(rows) <= ROW_LIMIT, 'unrun predecessor matrix')
        implementation = implementation_inventory()
        executor_sha = inventory_hash(implementation)
        for index, row in enumerate(rows):
            if time.monotonic() >= deadline:
                raise ManualRelease('suite_timed_out')
            executor = factory(workspace, row, target, docker_context=docker_context,
                               resource_prefix=RESOURCE_PREFIX, budgets=budgets, deadline=deadline, registry=registry)
            # Keep the object when __enter__ fails, so first-boot diagnostics
            # survive its verified cleanup and can be read from CI artifacts.
            with executor:
                from deploy.update.apply import Updater
                from deploy.update.standalone_restore import StandaloneRestore
                need(executor.shipping_executor_class is Updater and executor.shipping_restore_class is StandaloneRestore,
                     'shipping executor/restore required')
                scope = copy.deepcopy(executor.pending_migration_tables())
                selected = select_reference_registry(registry, scope)
                reference = seed_reference_tables(selected, executor.seed_reference_table, executor.measure_reference_table)
                cases = exercise(executor, deadline=deadline)
                binding = collect_executed_row(row, target, executor, cases, output, index=index,
                    registry=registry, scope=scope, reference=reference, executor_sha=executor_sha,
                    require_product_measurements=(purpose == 'product'))
            need(executor.cleanup_verified is True, 'disposable resource cleanup unverified')
            if time.monotonic() >= deadline:
                raise ManualRelease('suite_timed_out')
            equal(implementation_inventory(), implementation, 'executor changed during rehearsal')
            bindings.append(binding); results.append(cases); qualified_rows.append(row)
        # PR evidence remains deliberately ineligible at the product boundary.
        return publish_executed_rows(target, qualified_rows, bindings, results, output, run_id=run_id,
            implementation=implementation, purpose=purpose, executor=executor)
    except (Exception, Interrupted, KeyboardInterrupt) as exc:
        diagnostics_source = progress.get('session', executor)
        failure = failure_diagnostics(exc, diagnostics_source,
            completed_rows=progress.get('completed_rows', len(results)))
        failure['candidate_sha256'] = sha(canonical_json(target))
        write_pair(((output / 'failure.json', canonical_json(failure)),))
        retain_diagnostics(output, diagnostics_source, failure=failure)
        if isinstance(exc, ManualRelease):
            raise
        raise ManualRelease('suite_failed') from None


def notify_decision(target, reason, run_id):
    need(reason in MANUAL_REASONS, 'invalid notify-only reason')
    return {'schema_version': 1, 'eligible': False, 'reason': reason,
            'candidate_sha256': sha(canonical_json(target)), 'run_id': run_id, 'repository': REPOSITORY}


# Removed the historical ad-hoc file/Redis/unfinished-workflow writer. The real
# acceptance harness must use the shipping disposable witness adapter, never
# arbitrary bytes named collector spool. A missing operation is an honest gap.
WITNESS = "raise RuntimeError('witness_operation_unavailable: use the shipping product witness adapter')"


def retained_deployment(row, destination):
    """Materialize the authenticated retained layout as bounded git blob DATA.

    Does not check out a tree, overwrite the worktree, extract an archive, or
    execute predecessor scripts. Exact signed path hashes are rechecked by the
    disposable driver before use. Directory entries use only regular git blobs.
    """
    need(not destination.exists() and re.fullmatch(r'[a-f0-9]{40}', row.layout_source_sha),
         'retained layout destination/owner invalid')
    destination.mkdir(mode=0o700, parents=True)
    paths = list(row.execution_deployment['files'])
    need(all(type(name) is str and not name.startswith('/') and '..' not in Path(name).parts
             and name != '.env' for name in paths), 'invalid retained deployment paths')
    raw = command(['git', '-C', ROOT, 'ls-tree', '-r', '-z', row.layout_source_sha, '--', *paths], timeout=60)
    entries = raw.split(b'\0')
    need(len(entries) <= 65537, 'retained deployment file bound')
    for entry in entries:
        if not entry:
            continue
        header, encoded = entry.split(b'\t', 1)
        mode, kind, blob = header.decode('ascii').split(' ')
        name = encoded.decode('utf-8')
        need(mode in ('100644', '100755') and kind == 'blob' and re.fullmatch(r'[a-f0-9]{40,64}', blob)
             and not name.startswith('/') and '..' not in Path(name).parts
             and any(name == p or name.startswith(p.rstrip('/') + '/') for p in paths),
             'unsafe retained deployment entry')
        path = destination / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_pair(((path, command(['git', '-C', ROOT, 'cat-file', 'blob', blob], timeout=60)),))
    from dataclasses import replace
    return replace(row, deployment_directory=destination)


def _local_verifier(public_key=None):
    overrides = {'FUTURE_AGI_UPDATE_COSIGN_PUBLIC_KEY': str(Path(public_key).resolve()),
                 'FUTURE_AGI_UPDATE_COSIGN_OFFLINE': 'true'} if public_key else {}
    result = Verifier(load_settings(ROOT, overrides=overrides, environ={}), host_only=True)
    result.require_host()
    return result


def select_retained_layout(source, proof_raw, directory, platform):
    """Select a source-authenticated execution map by the actual retained files.

    Source B's published deployment is not the execution layout of B installed
    on A's retained files. Only its manifest-bound proof may supply that extra
    map/dependency lock. A directory/report cannot invent another layout.
    """
    from deploy.update.manifest import hash_path
    candidates = {}
    if source.get('compatibility', {}).get('validation') is not None:
        need(type(proof_raw) is bytes and bool(proof_raw), 'source compatibility proof required')
        proof = validate_release_proof(proof_raw, source, repository=REPOSITORY)
        for row in proof['predecessors']:
            previous = row['predecessor']
            if (previous['mode'], previous['platform'], previous['profiles']) != ('standalone', platform, []):
                continue
            deployment = row.get('execution_deployment', row['predecessor_deployment'])
            key, value = manifest_hash(deployment), (deployment, tuple(row['dependencies']))
            need(key not in candidates or candidates[key] == value, 'ambiguous retained dependency lock')
            candidates[key] = value
    else:
        need(proof_raw is None, 'unbound source compatibility proof')
    candidates.setdefault(manifest_hash(source['deployment']), (source['deployment'], ()))
    matches = []
    for deployment, dependencies in candidates.values():
        try:
            observed = {name: hash_path(directory, name) for name in deployment['files']}
        except (OSError, ValueError):
            continue
        if observed == deployment['files']:
            matches.append((copy.deepcopy(deployment), dependencies))
    need(len(matches) == 1, 'retained deployment has no unique authenticated source layout')
    return matches[0]


def _milestone_outcome(directory):
    """Bounded stage/failure labels from the dispatched milestone, never evidence."""
    try:
        record = decode_document(read_bytes(directory / 'milestone.json', MAX_EVIDENCE_BYTES), MAX_EVIDENCE_BYTES)
    except Exception:
        return None
    failure = record.get('failure') if type(record.get('failure')) is dict else None
    return dict(status=record.get('status') if record.get('status') in ('passed', 'failed') else None,
        executed_stages=[s for s in record.get('executed_stages', []) if type(s) is str][:64]
            if type(record.get('executed_stages')) is list else [],
        cleanup_verified=record.get('cleanup_verified') is True,
        failure=None if failure is None else {k: str(failure[k])[:512] for k in
            ('stage', 'reason', 'refusal_code', 'exception_type', 'detail', 'assertion_detail') if k in failure})


def qualify(argv):
    """Single native pair, with a nonzero failure exit even for notify-only rows."""
    module = importlib.import_module('scripts.update_rehearsal_milestone')
    parser = argparse.ArgumentParser(prog='rehearse_update_compatibility.py qualify')
    parser.add_argument('--through', choices=tuple(name for name in module.MILESTONES if name != 'qualification'),
                        help='Bounded milestone only; never writes a qualification/proof row')
    parser.add_argument('--dry-run', action='store_true', help='Write the milestone job plan without Docker')
    parser.add_argument('--source-ref')
    parser.add_argument('--target-ref')
    parser.add_argument('--platform', choices=SUPPORTED_PLATFORMS)
    parser.add_argument('--deployment-dir', type=Path)
    parser.add_argument('--reference-scale', choices=('registered-100k',))
    parser.add_argument('--boot-timeout', type=int, default=4200)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--public-key', type=Path)
    parser.add_argument('--docker-context')
    parser.add_argument('--run-id', type=int, default=int(os.environ.get('GITHUB_RUN_ID', '1')))
    args = parser.parse_args(argv)
    args.docker_context = args.docker_context or 'colima-fagi-update'
    if args.through:
        if any((args.source_ref, args.target_ref, args.deployment_dir, args.reference_scale, args.public_key)):
            parser.error('milestones use fixed release tags/pins, not qualification or trust inputs')
        if args.platform not in (None, SUPPORTED_PLATFORM):
            parser.error('Milestones run only on native linux/amd64 Actions')
        if not 1 <= args.boot_timeout <= 4200:
            parser.error('source boot ceiling must be between 1 and 4200 seconds')
        module = milestone_api()
        code = module.run_milestone(args.through, args)
        need(type(code) is int and code in (0, 2), 'invalid milestone exit status')
        return code
    if args.dry_run:
        parser.error('--dry-run requires --through')
    for name in ('source_ref', 'target_ref', 'platform', 'deployment_dir', 'reference_scale'):
        if getattr(args, name) is None:
            parser.error('--'+name.replace('_', '-')+' is required for full qualification')
    target = None
    need(not args.out.exists() and not args.out.is_symlink(), 'qualification output must be new')
    args.out.mkdir(mode=0o700, parents=True)
    output, workspace = args.out / 'rows', args.out / 'workspace'
    phase, session, dispatch, selected, milestone = 'context_validation', None, None, None, None
    try:
        need(type(args.run_id) is int and 0 < args.run_id <= 9007199254740991, 'invalid run identity')
        _validate_context(args.docker_context, args.out)
        from deploy.update.manifest import parse_image_ref, hash_path
        from deploy.update.trust import FetchedManifest
        from deploy.update.discovery import DiscoveredRelease
        from deploy.update.contracts import VerificationSnapshot
        phase = 'read_manifest_inputs'
        source_path = args.deployment_dir / 'source-manifest.json'
        raw = read_bytes(source_path)
        old = validate_manifest(decode_document(raw))
        target = validate_manifest(decode_document(read_bytes(args.deployment_dir / 'target-manifest.json')))
        need(target['compatibility']['contract_version'] == 2 and target['compatibility']['automatic_apply'] is False
             and target['compatibility']['validation'] is None, 'candidate must be uncertified')
        phase = 'verify_explicit_image_pins'
        for ref, manifest in ((args.source_ref, old), (args.target_ref, target)):
            parsed, component = parse_image_ref(ref), manifest['components']['standalone']
            need(parsed.digest is not None and parsed.digest == component['digest'] and
                 parsed.repository == parse_image_ref(component['image']).repository and
                 args.platform in component['platforms'], 'explicit image pin differs from manifest')
        phase = 'select_authenticated_retained_layout'
        proof_raw = (read_bytes(args.deployment_dir / 'source-compatibility.json')
                     if old.get('compatibility', {}).get('validation') is not None else None)
        execution, dependencies = select_retained_layout(old, proof_raw, args.deployment_dir, args.platform)
        phase = 'verify_source_signature'
        verifier = _local_verifier(args.public_key)
        bundle = args.deployment_dir / 'source-manifest.sigstore.json'
        bundle_raw = read_bytes(bundle, 1024 * 1024)
        checked = verifier.verify(FetchedManifest('retained-source', args.deployment_dir, source_path, raw, old, bundle))
        need(checked.verified is True, 'source signature rejected')
        snapshot = VerificationSnapshot(checked.verified, checked.method, checked.signer, checked.issuer, checked.tool,
            datetime.now(timezone.utc), checked.public_key_sha256)
        release = DiscoveredRelease(raw, bundle_raw, manifest_hash(old), sha(canonical_json(
            dict(method=checked.method, signer=checked.signer, public_key_sha256=checked.public_key_sha256))), snapshot,
            release_proof=proof_raw or b'')
        row = Row(release, execution, old['source_sha'], dependencies, args.platform, args.deployment_dir.resolve())
        # Plan every milestone through the stated dispatch contract, daemon-free,
        # and refuse BEFORE provisioning unless ONE plans all six suites for
        # exactly this pair. A bounded milestone of another pair is not evidence.
        phase = 'plan_milestones'
        module = milestone_api()
        plans = milestone_plans(module, args, args.out / 'milestone-plans')
        selected, reason, dispatch = select_qualification_milestone(plans, source_ref=args.source_ref,
            target_ref=args.target_ref, platform=args.platform)
        if selected is None:
            raise ManualRelease(reason)
        phase = 'shipping_product_rehearsal'
        workspace.mkdir(mode=0o700)
        budgets = Budgets(startup_seconds=args.boot_timeout)
        deadline = time.monotonic() + budgets.total_seconds
        registry = load_high_volume_registry()
        session = QualificationSession(row, target, output, run_id=args.run_id, deadline=deadline, registry=registry)
        milestone_args = argparse.Namespace(**vars(args))
        milestone_args.through, milestone_args.dry_run = selected, False
        milestone_args.out = args.out / 'milestones' / selected
        milestone_args.qualification = session
        milestone_args.row, milestone_args.target = row, copy.deepcopy(target)
        milestone_args.workspace, milestone_args.registry = workspace, registry
        milestone_args.budgets, milestone_args.deadline = budgets, deadline
        # One synchronous call owns the one stack that runs all six suites.
        code = module.run_milestone(selected, milestone_args)
        need(type(code) is int, 'invalid milestone exit status')
        milestone = _milestone_outcome(milestone_args.out)
        if code != 0:
            raise ManualRelease('suite_failed')
        session.finish()
        envelope = decode_document(read_bytes(output / 'evidence.json', MAX_EVIDENCE_BYTES), MAX_EVIDENCE_BYTES)
        equal(len(envelope['rows']), 1, 'single-pair row count differs')
        result = dict(schema_version=1, eligible=True, rows_directory='rows', row=envelope['rows'][0],
                      shipping_executor_available=session.shipping_executor_available,
                      milestone_dispatched=selected, milestone_outcome=milestone,
                      candidate_sha256=sha(canonical_json(target)), run_id=args.run_id)
        write_pair(((args.out / 'proof-row.json', canonical_json(result)),))
        return 0
    except (Exception, Interrupted, KeyboardInterrupt) as exc:
        diagnostics = failure_diagnostics(exc, session)
        reason = diagnostics['reason']
        counts = diagnostics['executed_assertions']
        failed = diagnostics['failed_assertion']
        if (output / 'failure.json').is_file():
            diagnostics = decode_document(read_bytes(output / 'failure.json'))
            reason = diagnostics['reason']
            counts = diagnostics['executed_assertions']
            failed = diagnostics['failed_assertion']
        layout = topology_profile('standalone', ())
        suites = {s: ('failed' if failed and failed[0] == s else
                      'passed' if counts[s] == len(required_cases(s, layout)) else
                      'incomplete' if counts[s] else 'unrun') for s in SUITES}
        result = dict(diagnostics, schema_version=1, eligible=False, reason=reason, completed_rows=0,
            shipping_executor_available=False,
            phase=phase, suites=suites, executed_assertions=counts, failed_assertion=failed,
            candidate_sha256=sha(canonical_json(target)) if target else None,
            milestone_dispatch=dispatch, milestone_dispatched=selected, milestone_outcome=milestone or
            (_milestone_outcome(args.out / 'milestones' / selected) if selected else None))
        write_pair(((args.out / 'proof-row.json', canonical_json(result)),))
        if not (args.out / 'failure.json').exists():
            write_pair(((args.out / 'failure.json', canonical_json(result)),))
        if (output / 'diagnostics.json').is_file():
            write_pair(((args.out / 'diagnostics.json', read_bytes(output / 'diagnostics.json', MAX_EVIDENCE_BYTES)),))
        else:
            # CI uploads only diagnostics.json: keep reason, phase and milestone pins.
            retain_diagnostics(args.out, session, failure=result)
        print('qualification refused: ' + reason, file=sys.stderr)
        return 2


def package(argv):
    """Strict executed product evidence -> local test-key signed package only."""
    parser = argparse.ArgumentParser(prog='rehearse_update_compatibility.py package')
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--rows', type=Path, help='single-pair qualify output')
    source.add_argument('--evidence', type=Path, help='same-job protected producer evidence, not an external verdict')
    parser.add_argument('--target', type=Path, required=True)
    signing = parser.add_mutually_exclusive_group(required=True)
    signing.add_argument('--key', type=Path, help='explicit LOCAL TEST private key only')
    signing.add_argument('--ephemeral-key', action='store_true', help='generate a fresh private local-test key in the new output')
    parser.add_argument('--public-key', type=Path, help='test predecessor public key; also the --key verification key')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--run-id', type=int, required=True)
    args = parser.parse_args(argv)
    phase = 'read_qualification_row'
    created = False
    try:
        target = validate_manifest(decode_document(read_bytes(args.target)))
        if args.rows is not None:
            result = decode_document(read_bytes(args.rows / 'proof-row.json', MAX_EVIDENCE_BYTES), MAX_EVIDENCE_BYTES)
            need(result.get('eligible') is True and result.get('rows_directory') == 'rows'
                 and result.get('run_id') == args.run_id, 'unqualified rows cannot be packaged')
            equal(result['candidate_sha256'], sha(canonical_json(target)), 'package candidate differs')
            evidence = args.rows / 'rows'
        else:
            # This is not a bypass: build() independently verifies the lane,
            # frozen candidate/run, every report, raw measurement and source trust.
            evidence = args.evidence
        need(not args.out.exists() and not args.out.is_symlink(), 'package output must be new')
        need(not (evidence / 'failure.json').exists() and not (evidence / 'manual.json').exists(),
             'failed qualification cannot be packaged')
        need(args.ephemeral_key or args.public_key is not None, 'explicit test-key pair required')
        from scripts.build_update_compatibility_proof import build
        phase = 'validate_executed_evidence'
        proof, manifest = build(target, decode_document(read_bytes(evidence / 'policy.json')), evidence,
            run_id=args.run_id, verifier=_local_verifier(args.public_key))
        # Key generation is AFTER all strict evidence checks. It never reads a
        # production secret and never invokes keyless signing or a transparency log.
        args.out.mkdir(mode=0o700, parents=True)
        created = True
        phase = 'read_local_test_key'
        key, signing_public_key = args.key, args.public_key
        if args.ephemeral_key:
            phase = 'generate_ephemeral_test_key'
            prefix = args.out.resolve() / 'local-test-key'
            command(['cosign', 'generate-key-pair', '--output-key-prefix', prefix], timeout=60,
                    env=dict(os.environ, COSIGN_PASSWORD=''))
            key, signing_public_key = Path(str(prefix) + '.key'), Path(str(prefix) + '.pub')
            key.chmod(0o600)
        key_bytes = read_bytes(key, 65536)
        need(b'PRIVATE KEY' in key_bytes, 'explicit local test private key required')
        read_bytes(signing_public_key, 65536)
        from deploy.update.compatibility import PROOF_ASSET
        write_pair(((args.out / PROOF_ASSET, proof), (args.out / MANIFEST_ASSET, manifest)))
        phase = 'sign_local_test_manifest'
        command(['cosign', 'sign-blob', '--yes', '--key', key.resolve(), '--tlog-upload=false',
                 '--use-signing-config=false', '--bundle', args.out / BUNDLE_ASSET, args.out / MANIFEST_ASSET],
                timeout=60, env=dict(os.environ, COSIGN_PASSWORD='' if args.ephemeral_key else os.environ.get('COSIGN_PASSWORD', '')))
        phase = 'verify_local_test_package'
        from deploy.update.trust import FetchedManifest
        verified = _local_verifier(signing_public_key).verify(FetchedManifest(
            'local-test-package', args.out, args.out / MANIFEST_ASSET,
            manifest, validate_manifest(decode_document(manifest)), args.out / BUNDLE_ASSET))
        need(verified.verified is True, 'local test package signature rejected')
        write_pair(((args.out / 'local-test.json', canonical_json(dict(schema_version=1,
            purpose='LOCAL TEST ONLY; not release trust or product qualification by itself',
            ephemeral_key=bool(args.ephemeral_key), run_id=args.run_id,
            candidate_sha256=sha(canonical_json(target)), manifest_sha256=sha(manifest), proof_sha256=sha(proof),
            public_key_sha256=sha(read_bytes(signing_public_key, 65536))))),))
        return 0
    except (Exception, Interrupted, KeyboardInterrupt) as exc:
        failure = dict(failure_diagnostics(exc), phase=phase)
        if created:
            # A partially signed directory is never a usable package. Keep a
            # bounded refusal instead of presenting stale/invalid bundle bytes
            # as a successful package. No pre-existing output is overwritten.
            write_pair(((args.out / 'failure.json', canonical_json(failure)),))
        print('local-test package refused: ' + failure['refusal_code'] + ' (' + phase + ')', file=sys.stderr)
        return 2


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ('qualify', 'package'):
        return {'qualify': qualify, 'package': package}[argv[0]](argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--run-id', type=int, required=True)
    parser.add_argument('--docker-context', default='colima-fagi-update')
    parser.add_argument('--platform', choices=SUPPORTED_PLATFORMS, default=SUPPORTED_PLATFORM)
    parser.add_argument('--purpose', choices=('product', 'pr-validation'), default='product')
    parser.add_argument('--startup-timeout-seconds', type=int, default=4200)
    parser.add_argument('--rehearsal-timeout-seconds', type=int, default=19800)
    args = parser.parse_args(argv)
    target = None
    try:
        target = validate_manifest(decode_document(read_bytes(args.target)))
        need(0 < args.run_id <= 9007199254740991 and os.environ.get('GITHUB_REPOSITORY') == REPOSITORY
             and str(args.run_id) == os.environ.get('GITHUB_RUN_ID'),
             'same canonical workflow run required')
        if args.purpose == 'product':
            need(os.environ.get('GITHUB_REF') == 'refs/tags/' + target['version']
                 and os.environ.get('GITHUB_SHA') == target['source_sha']
                 and os.environ.get('GITHUB_EVENT_NAME') not in ('pull_request', 'pull_request_target'), 'protected tag caller required')
        else:
            need(os.environ.get('GITHUB_EVENT_NAME') == 'pull_request', 'PR validation caller required')
        budgets = Budgets(args.startup_timeout_seconds, args.rehearsal_timeout_seconds)
        _validate_context(args.docker_context, args.workspace)
        need(not args.workspace.exists() and not args.output.exists(), 'workspace/output must be new')
        args.workspace.mkdir(mode=0o700, parents=True)
        if args.purpose == 'pr-validation':
            shipping_factory()  # no discovery/network/Docker if PR integration is absent
        verifier = Verifier(load_settings(ROOT, environ={}), host_only=True)
        verifier.require_host()
        discovery = ReleaseDiscovery(HttpCache(args.workspace / 'discovery'), verifier)
        rows = select_predecessors(target, discovery, platform=args.platform)
        produce(target, rows, args.workspace, args.output, run_id=args.run_id,
                docker_context=args.docker_context, budgets=budgets, purpose=args.purpose)
    except ManualRelease as exc:
        args.output.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not (args.output / 'failure.json').exists():
            failure = failure_diagnostics(exc)
            failure['candidate_sha256'] = sha(canonical_json(target)) if target else None
            write_pair(((args.output / 'failure.json', canonical_json(failure)),))
            retain_diagnostics(args.output, failure=failure)
        write_pair(((args.output / 'manual.json', canonical_json(notify_decision(target, str(exc), args.run_id))),))
        # A notify-only manifest is still readable/manual-installable, but a
        # protected qualification job did NOT pass. In particular absent
        # integration and failed, timed-out or unrun suites must turn CI red.
        print('release rehearsal refused: ' + str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        failure = failure_diagnostics(exc)
        # Never overwrite a pre-existing output directory after a create-only
        # refusal. Unexpected discovery/import failures still get diagnostics
        # when this invocation has not produced any directory/evidence yet.
        if not args.output.exists() and not args.output.is_symlink():
            args.output.mkdir(mode=0o700, parents=True)
            write_pair(((args.output / 'failure.json', canonical_json(failure)),))
            retain_diagnostics(args.output, failure=failure)
        print('release rehearsal refused: ' + failure['refusal_code'] + ' (' + type(exc).__name__ + ')', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    # Keep exception/dataclass identities shared with the fixed driver import.
    sys.modules['scripts.rehearse_update_compatibility'] = sys.modules[__name__]
    raise SystemExit(main())
