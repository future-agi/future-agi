"""Disposable driver of the unmodified shipping cold-backup/runtime/restore APIs.

This module is producer code, never a customer plugin or a runtime admission
bypass. Image code runs only inside digest-verified product containers. Host
code is Python 3.9+ stdlib. Missing witness operations, registry seed workloads,
or per-migration execution boundaries are qualification failures, not skips.
No external command/report is accepted as an assertion.
"""
from __future__ import annotations

import base64
import copy
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
import uuid

REHEARSAL_CONTRACT = 'standalone-disposable-rehearsal-v1'
# Explicit incompleteness, not an admission flag. Do not flip this constant to
# qualify: the complete Updater transaction, registry workloads and all six
# executable assertion sets must replace the incomplete methods below first.
SHIPPING_TRANSACTION_INTEGRATED = False
PREFIX = 'fagi-upd-e2e-release-'


def producer():
    # The CLI installs its __main__ module at this canonical name before calling
    # us. Imports from tests/library callers already have that identity.
    from scripts import rehearse_update_compatibility
    return rehearse_update_compatibility


def wire(value):
    from collections.abc import Mapping
    if is_dataclass(value):
        return {f.name: wire(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {k: wire(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [wire(v) for v in value]
    if isinstance(value, (datetime, Path)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    return value


def configuration_preimage(inst):
    from deploy.update.manifest import hash_path
    from deploy.update.settings import CONFIG_FILE
    p = inst.root / CONFIG_FILE
    return dict(config=inst.config(refresh=True), env=dict(inst.env),
        files={str(f): hash_path(f.parent, f.name) for f in inst._ownership_files()},
        environment=dict(inst.environ), updater_config=hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None)


def retain_source_preimages(inst, registration, release, observation):
    """Independently capture the actual preimages while the source is healthy."""
    from deploy.update.standalone_state import _runtime
    from deploy.update.compatibility import topology_profile, plain
    from deploy.update.docker_host import inspect_volume_storage
    p = producer()
    _, identities, _ = _runtime(inst, registration.binding, release, topology_profile('standalone', ()))
    storage = inspect_volume_storage(inst, registration, helper_image_id=identities['app']['image'],
                                     timeout=90, measure_used_bytes=False)
    p.need(storage.status == 'available', 'source storage preimage unavailable')
    result = dict(configuration=configuration_preimage(inst), containers=plain(identities),
        volume_storage={v.role: dict(name=v.name, root_identity=v.root_identity, device=v.device,
            filesystem=v.filesystem, backing_sha256=v.backing_sha256) for v in storage.volumes})
    runtime = observation.runtime_observation
    p.equal(p.manifest_hash(result['configuration']), runtime.config_sha256, 'source changed during preimage capture')
    p.equal(p.manifest_hash(result['containers']), runtime.container_roster_sha256, 'source changed during preimage capture')
    p.equal(storage.storage_sha256, observation.volume_storage_sha256, 'source storage changed during preimage capture')
    return result


# Fixed, checked-in product operations. Arguments are JSON data on stdin. These
# run through the app's own installed libraries, never through a host dependency.
DJANGO_PREFIX = """
import sys, os, json
sys.path.insert(0, '/app/backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE','tfc.settings.settings')
os.environ['NO_STARTUP_DB_MUTATIONS']='true'
os.environ['FI_SKIP_CH25_MIGRATION']='1'
import django
django.setup()
a=json.load(sys.stdin)
"""

S3_OPERATION = """
import sys, os, json, base64, hashlib, boto3
a=json.load(sys.stdin)
s=boto3.client('s3', endpoint_url='http://127.0.0.1:9005',
    aws_access_key_id=os.environ['MINIO_ROOT_USER'], aws_secret_access_key=os.environ['MINIO_ROOT_PASSWORD'],
    region_name='us-east-1')
if a['action']=='create':
    s.create_bucket(Bucket=a['bucket'])
    s.put_object(Bucket=a['bucket'],Key=a['key'],Body=base64.b64decode(a['body'],validate=True))
r=s.get_object(Bucket=a['bucket'],Key=a['key'])['Body'].read(1048577)
assert len(r)<=1048576
print(json.dumps({'identity':a['bucket']+'/'+a['key'],'payload_sha256':hashlib.sha256(r).hexdigest()}))
"""

TEMPORAL_OPERATION = DJANGO_PREFIX + """
import asyncio, hashlib
from tfc.temporal.common.client import get_client
from tfc.temporal.drop_in.workflow import TaskRunnerWorkflow, TaskRunnerInput
async def main():
    c=await get_client()
    if a['action']=='create':
        h=await c.start_workflow(TaskRunnerWorkflow.run,TaskRunnerInput(
            activity_name='drain_outbox_cdc',args=[],kwargs={},queue='tasks_s',time_limit=60,max_retries=0),
            id=a['id'],task_queue='tasks_s')
        await asyncio.wait_for(h.result(),120)
        rid=h.first_execution_run_id
    else:
        rid=a['run_id']
    h=c.get_workflow_handle(a['id'],run_id=rid)
    d=await h.describe()
    assert d.status.name=='COMPLETED'
    history=await h.fetch_history()
    raw=history.to_json().encode()
    assert len(raw)<=4*1024*1024
    print(json.dumps({'identity':a['id']+'/'+d.run_id,'run_id':d.run_id,
                     'payload_sha256':hashlib.sha256(raw).hexdigest()}))
asyncio.run(main())
"""

REDIS_OPERATION = """
import sys,os,json,redis
a=json.load(sys.stdin)
r=redis.Redis.from_url(os.environ['REDIS_URL'],socket_connect_timeout=5,socket_timeout=5)
assert r.config_get('appendonly').get('appendonly')=='no'
assert r.config_get('save').get('save')==''
if a['action']=='seed':
    assert r.set(a['key'],a['value'],nx=True)
    assert r.get(a['key'])==a['value'].encode()
    result={'seeded':True}
else:
    assert a['action']=='verify_reset'
    result={'absent':r.get(a['key']) is None}
print(json.dumps(result))
"""

SPOOL_OPERATION = """
import sys,json,hashlib,os,stat
from pathlib import Path
a=json.load(sys.stdin)
rows=[]
for root in (Path('/data/collector'),Path('/data/telemetry')):
    if not root.exists(): continue
    for path in sorted(root.rglob('*')):
        info=path.lstat()
        if not stat.S_ISREG(info.st_mode): continue
        if a['paths'] is not None and str(path) not in a['paths']: continue
        assert info.st_size<=4*1024*1024
        fd=os.open(str(path),os.O_RDONLY|os.O_NOFOLLOW)
        with os.fdopen(fd,'rb') as f: raw=f.read(4*1024*1024+1)
        after=path.lstat()
        assert (info.st_ino,info.st_size,info.st_mtime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns)
        # A background spool changing during seeding is not attribution. Only
        # claim files containing this controlled namespace/trace, when supplied.
        if a.get('contains') and not any(value.encode() in raw for value in a['contains']): continue
        rows.append({'identity':str(path),'payload_sha256':hashlib.sha256(raw).hexdigest()})
        assert len(rows)<=4096
print(json.dumps(rows))
"""

# Reference workloads are separate from A4 witnesses. This deliberately does
# not fabricate model records, guess required FK values, disable constraints,
# copy another database, or claim an empty/unavailable table reached the scale.
# Existing real records are cloned only for reviewed append-only ORM log models.
APPEND_ONLY_MODELS = frozenset({('prism', 'AgentccRequestLog'), ('prism', 'AgentccShadowResult'),
    ('prism', 'AgentccWebhookEvent'), ('usage', 'APICallLog')})
REFERENCE_SEED = DJANGO_PREFIX + """
from django.apps import apps
from django.db import transaction
from copy import deepcopy
import uuid
m=apps.get_model(a['app_label'],a['model'])
assert m._meta.db_table==a['table'] and m._meta.managed
sample=m.objects.order_by(m._meta.pk.name).first()
assert sample is not None, 'reference_seed_operation_unavailable'
count=m.objects.count()
# Never copy unique values/relationships or synthesize semantically invalid
# required fields. A table that needs a different workload needs a reviewed
# seeder, rather than silently removing its constraints or rows.
for field in m._meta.concrete_fields:
    assert not field.unique or field.primary_key
assert not m._meta.unique_together and not m._meta.constraints
while count<a['rows']:
    batch=[]
    for _ in range(min(500,a['rows']-count)):
        obj=deepcopy(sample)
        obj._state.adding=True
        pk=m._meta.pk
        if pk.get_internal_type()=='UUIDField': setattr(obj,pk.attname,uuid.uuid4())
        elif pk.get_internal_type() in ('AutoField','BigAutoField','SmallAutoField'): setattr(obj,pk.attname,None)
        else: raise RuntimeError('reference_seed_operation_unavailable')
        batch.append(obj)
    with transaction.atomic(): m.objects.bulk_create(batch,batch_size=500)
    count+=len(batch)
print(json.dumps({'seeded_rows':count}))
"""


def decode_product_output(raw):
    """Read a fixed operation's final JSON value, after product startup logging.

    The manifest decoder deliberately accepts only objects; spool enumeration is
    a list. Wrap the final line so duplicate keys, nonfinite numbers, depth and
    size still use the same strict decoder, rather than relaxing manifest input.
    """
    p = producer()
    raw = raw.encode('utf-8') if isinstance(raw, str) else raw
    p.need(type(raw) is bytes and len(raw) <= p.MAX_EVIDENCE_BYTES, 'product operation output bound')
    lines = raw.splitlines()
    p.need(bool(lines) and bool(lines[-1].strip()), 'missing product operation result')
    return p.decode_document(b'{"value":' + lines[-1] + b'}', p.MAX_EVIDENCE_BYTES)['value']


class ProductWitnesses:
    """Own exact API/S3/collector/workflow identities, not a whole-database dump."""
    def __init__(self, driver):
        self.d = driver
        self.namespaces = {}

    def _http(self, path, *, payload=None, headers=None, port='BACKEND_PORT'):
        from urllib.request import Request, ProxyHandler, build_opener
        p = producer()
        raw = None if payload is None else p.canonical_json(payload)
        url = 'http://127.0.0.1:' + str(self.d.ports[port]) + path
        request = Request(url, data=raw, headers=dict({'Content-Type':'application/json'}, **(headers or {})))
        from urllib.error import HTTPError
        try:
            with build_opener(ProxyHandler({})).open(request, timeout=30) as response:
                result = response.read(p.MAX_EVIDENCE_BYTES + 1)
                p.need(len(result) <= p.MAX_EVIDENCE_BYTES, 'witness API response bound')
                document = p.decode_document(result) if result else {}
                # GeneralMethods.success_response wraps signup/login in `result`;
                # user-info and keys use their own documented response shapes.
                return document['result'] if 'result' in document else document
        except HTTPError as error:
            # These paths are fixed product operations. Keep the HTTP reason,
            # never credentials, request payloads or arbitrary server errors.
            raise p.RehearsalError('product witness HTTP '+str(error.code)+' on '+path) from None

    def create(self, label):
        p = producer()
        p.need(label not in self.namespaces, 'duplicate witness namespace')
        namespace = PREFIX + label + '-' + uuid.uuid4().hex
        initial_spool = {r['identity']:r['payload_sha256'] for r in self.d.app_python(SPOOL_OPERATION,{'paths':None})}
        account = dict(email=namespace+'@example.test',full_name=namespace,company_name=namespace,
                       password=secrets.token_urlsafe(32))
        self.d._secret_values.add(account['password'])
        # The supported local OSS API creates the relational user/org/workspace;
        # collector ingestion creates the project through the product path.
        self._http('/accounts/signup/',payload=account)
        tokens = self._http('/accounts/token/',payload=dict(email=account['email'],password=account['password'],remember_me=True))
        self.d._secret_values.update(str(v) for v in tokens.values() if isinstance(v, str))
        headers = {'Authorization':'Bearer '+tokens['access']}
        info = self._http('/accounts/user-info/',headers=headers)
        p.need(info.get('default_workspace_id') and info.get('organization',{}).get('id'), 'relational witness missing')
        headers.update({'X-Organization-Id':str(info['organization']['id']), 'X-Workspace-Id':str(info['default_workspace_id'])})
        keys = self._http('/accounts/keys/',headers=headers)['data']
        self.d._secret_values.update(str(v) for v in keys.values() if isinstance(v, str))
        trace = uuid.uuid4()
        span = secrets.token_hex(8)
        start = int(time.time()*1000000)*1000
        attrs = lambda doc:[dict(key=k,value={'stringValue':v}) for k,v in doc.items()]
        payload = dict(resourceSpans=[dict(resource=dict(attributes=attrs({'project_name':namespace,'service.name':namespace,
            'project_type':'observe'})),scopeSpans=[dict(scope={'name':'update-qualification'},spans=[dict(
                traceId=trace.hex,spanId=span,name=namespace,kind=1,startTimeUnixNano=str(start),
                endTimeUnixNano=str(start+50000000),attributes=attrs({'qualification.namespace':namespace}),status={'code':1})])])])
        self._http('/v1/traces',payload=payload,headers={'X-Api-Key':keys['api_key'],'X-Secret-Key':keys['secret_key']},port='FI_COLLECTOR_OTLP_HTTP_PORT')
        obj = dict(action='create',bucket='qualification-'+uuid.uuid4().hex,key=namespace,
                   body=base64.b64encode(secrets.token_bytes(4096)).decode())
        s3 = self.d.app_python(S3_OPERATION,obj)
        p.equal(s3,dict(identity=obj['bucket']+'/'+obj['key'],
                       payload_sha256=p.sha(base64.b64decode(obj['body'],validate=True))),
                'S3 witness differs from seeded payload')
        workflow = self.d.app_python(TEMPORAL_OPERATION,dict(action='create',id=namespace))
        final_spool = self.d.app_python(SPOOL_OPERATION,{'paths':None,'contains':[namespace,trace.hex,str(trace)]})
        produced = [r['identity'] for r in final_spool if initial_spool.get(r['identity'])!=r['payload_sha256']]
        self.namespaces[label] = dict(namespace=namespace,account=account,headers=headers,trace_id=str(trace),
            span_id=span,object=obj,workflow=workflow,spool_paths=produced)
        for family in ('collector','telemetry'):
            if not any(path.startswith('/data/'+family+'/') for path in produced):
                self.d.corpus.setdefault('witness_gaps',[]).append({'domain':'shared_files','family':family,
                    'reason':'no_attributable_naturally_produced_spool'})
        if not produced:
            raise p.ManualRelease('witness_operation_unavailable')
        return self.read(label)

    def read(self, label):
        p, n = producer(), self.namespaces[label]
        info = self._http('/accounts/user-info/',headers=n['headers'])
        p.equal(info.get('email'),n['account']['email'],'witness API returned another user')
        relational = {
            'user/'+str(info['id']): {k:info.get(k) for k in ('id','email','name')},
            'organization/'+str(info['organization']['id']): info['organization'],
            'workspace/'+str(info['default_workspace_id']): {k:info.get(k) for k in
                ('default_workspace_id','default_workspace_name','default_workspace_display_name')},
        }
        result = [dict(domain='postgres_app',identity=identity,origin='product_api',
                       payload_sha256=p.sha(p.canonical_json(payload))) for identity,payload in relational.items()]
        # Read only the exact collector-created span. timestamps/usage counters
        # from unrelated background traffic never enter this namespace.
        sql = ("SELECT id, trace_id, name, project_id, service_name, start_time, end_time, "
               "status, attrs_string FROM spans FINAL WHERE trace_id = {trace:String} "
               "AND id = {span:String} FORMAT JSONEachRow")
        deadline = time.monotonic()+120
        while True:
            rows = self.d.clickhouse(sql,params={'trace':n['trace_id'],'span':n['span_id']})
            if rows: break
            p.need(time.monotonic()<deadline, 'collector witness not visible')
            time.sleep(.5)
        p.need(len(rows)==1, 'ambiguous collector witness')
        result.append(dict(domain='clickhouse',identity=n['trace_id']+'/'+n['span_id'],origin='collector_ingestion',payload_sha256=p.sha(p.canonical_json(rows[0]))))
        obj = self.d.app_python(S3_OPERATION,dict(n['object'],action='read'))
        result.append(dict(domain='object_store',origin='bundled_s3_api',**obj))
        workflow = self.d.app_python(TEMPORAL_OPERATION,dict(action='read',id=n['namespace'],run_id=n['workflow']['run_id']))
        result.append(dict(domain='temporal',origin='completed_product_workflow',**{k:workflow[k] for k in ('identity','payload_sha256')}))
        spool=self.d.app_python(SPOOL_OPERATION,{'paths':n['spool_paths']})
        p.equal(sorted(r['identity'] for r in spool),sorted(n['spool_paths']),'product spool disappeared')
        result.extend(dict(domain='shared_files',origin='product_spool',**r) for r in spool)
        return sorted(result,key=lambda r:(r['domain'],r['identity']))


def copy_retained_files(source, destination, execution):
    """Copy data, not a checkout or an executable; recheck the expected hashes."""
    from deploy.update.manifest import hash_path
    p = producer()
    p.need(source.is_dir() and not source.is_symlink(), 'retained deployment unavailable')
    for name,digest in execution['files'].items():
        relative = Path(name)
        p.need(not relative.is_absolute() and '..' not in relative.parts and name != '.env', 'invalid retained deployment path')
        path = source / relative
        p.need(not any(parent.is_symlink() for parent in (path,*path.parents)), 'symlink in retained deployment')
        p.equal(hash_path(source,name),digest,'retained deployment hash differs')
        target = destination / relative
        target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        if path.is_dir():
            for current,dirs,files in os.walk(path,followlinks=False):
                p.need(not any((Path(current)/entry).is_symlink() for entry in dirs+files),'retained deployment symlink')
            shutil.copytree(path,target,dirs_exist_ok=True)
        else:
            shutil.copyfile(path,target)
    for name,digest in execution['files'].items():
        p.equal(hash_path(destination,name),digest,'copied deployment hash differs')


class ShippingRehearsal:
    """One owned stack. Mutation methods delegate to the actual shipping APIs."""
    def __init__(self,workspace,row,target,*,docker_context,resource_prefix,budgets,deadline,registry):
        p = producer()
        p.need(resource_prefix==PREFIX,'release resource prefix required')
        p._validate_context(docker_context,workspace)
        self.row,self.target,self.context,self.budgets,self.deadline,self.registry = row,target,docker_context,budgets,deadline,registry
        self.project = PREFIX+uuid.uuid4().hex[:12]
        self.directory = Path(workspace)/self.project
        self.root = self.directory/'installation'
        self.cleanup_verified = False
        self._secret_values = set()
        self._diagnostic_data = dict(schema_version=1, qualification_evidence=False,
            first_boot_budget_seconds=budgets.startup_seconds, phase_timings=[],
            resources=[], product_logs={}, product_operations=[], collection_errors=[])
        self.corpus,self.executed,self.tables = {},{},[]
        self.owned_containers,self.owned_volumes,self.owned_networks = {},{},{}
        self.dependencies = tuple(row.dependencies)
        from deploy.update.standalone_restore import StandaloneRestore
        # Calling shipping primitives is not a complete Updater transaction.
        # Do not attest merely by importing the class (the old protocol accepted
        # that assertion without establishing an engine invocation).
        self.shipping_executor_class,self.shipping_restore_class = None,StandaloneRestore

    def _redact(self, value, limit=8192):
        """Diagnostics only. Never include generated passwords/keys or API tokens."""
        text = value.decode('utf-8', errors='replace') if isinstance(value, bytes) else str(value)
        for secret in sorted(getattr(self, '_secret_values', ()), key=len, reverse=True):
            if len(secret) >= 6:
                text = text.replace(secret, '[REDACTED]')
        text = re.sub(r'(?i)(bearer\s+)[A-Za-z0-9_.~+/=-]+', r'\1[REDACTED]', text)
        text = re.sub(r'(?i)((?:password|secret|token|api[_-]?key)["\s:=]+)[^\s,;]+', r'\1[REDACTED]', text)
        text = re.sub(r'(?i)(https?://)[^/\s:@]+:[^/\s@]+@', r'\1[REDACTED]@', text)
        return ''.join(c for c in text if c in '\n\r\t' or ord(c) >= 32)[-limit:]

    def _phase(self, name, operation, *args, **kwargs):
        started = time.monotonic()
        entry = dict(phase=name, outcome='failed')
        self._diagnostic_data['active_phase'] = name
        try:
            result = operation(*args, **kwargs)
            entry['outcome'] = 'passed'
            return result
        except BaseException as exc:
            entry['refusal'] = producer().failure_diagnostics(exc)['refusal_code']
            raise
        finally:
            entry['duration_seconds'] = time.monotonic() - started
            self._diagnostic_data['phase_timings'].append(entry)

    def _collect_diagnostics(self):
        """Bounded read-only exact-project snapshots, before disposable cleanup.

        This uses an independent READ supervisor. It cannot reset or extend the
        mutation supervisor, consume its protected restore reserve, or bless an
        ambiguous child. Samples/logs are for CI diagnosis, never proof metrics.
        """
        if not hasattr(self, 'installation'):
            return
        from deploy.update.docker_host import bounded_installation
        inst = bounded_installation(self.installation, 15)
        inst.runner.max_output = 65536
        sample = dict(monotonic=time.monotonic(), containers={})
        try:
            rows = inst.containers()
            ids = []
            for item in rows[:3]:
                record = json.loads(inst.docker_run('inspect', item['id']).stdout)[0]
                if record['Config']['Labels'].get('com.docker.compose.project') != self.project:
                    raise RuntimeError('foreign diagnostic container')
                service = record['Config']['Labels'].get('com.docker.compose.service')
                if service not in ('app', 'postgres', 'clickhouse'):
                    raise RuntimeError('foreign diagnostic service')
                ids.append(record['Id'])
                state = record['State']
                sample['containers'][service] = dict(status=state.get('Status'),
                    health=state.get('Health', {}).get('Status'), oom_killed=state.get('OOMKilled'),
                    exit_code=state.get('ExitCode'), restart_count=record['RestartCount'])
                logs = inst.docker_run('logs', '--timestamps', '--tail', '60', record['Id'], check=False)
                self._diagnostic_data['product_logs'][service] = dict(
                    stdout=self._redact(logs.stdout), stderr=self._redact(logs.stderr))
                if service == 'app' and state.get('Running'):
                    result = inst.docker_run('exec', record['Id'], 'cat', '/run/futureagi/status.json', check=False)
                    if result.returncode == 0:
                        status = producer().decode_document(result.stdout.encode(), 16384)
                        sample['bootstrap'] = {k: v for k, v in status.items()
                            if k in ('phase', 'migration', 'current_migration') and isinstance(v, str)
                            and re.fullmatch(r'[A-Za-z0-9_.-]{0,257}', v)}
            if ids:
                stats = inst.docker_run('stats', '--no-stream', '--format', '{{json .}}', *ids)
                sample['stats'] = [{k: str(v)[:80] for k, v in json.loads(line).items()
                    if k in ('ID', 'CPUPerc', 'MemUsage', 'MemPerc', 'BlockIO', 'NetIO', 'PIDs')}
                    for line in stats.stdout.splitlines() if line.strip()]
        except Exception as exc:
            self._diagnostic_data['collection_errors'].append(dict(exception_type=type(exc).__name__,
                refusal=producer().failure_diagnostics(exc)['refusal_code']))
            self._diagnostic_data['collection_errors'] = self._diagnostic_data['collection_errors'][-16:]
        self._diagnostic_data['resources'].append(sample)
        self._diagnostic_data['resources'] = self._diagnostic_data['resources'][-128:]

    def diagnostics(self):
        result = copy.deepcopy(self._diagnostic_data)
        result['cleanup_verified'] = self.cleanup_verified
        return result

    def _docker(self,*args,check=True):
        from deploy.update.compatibility import decode_document
        result=self.installation.docker_run(*args,check=check)
        return result.stdout

    def _json(self,*args):
        return json.loads(self._docker(*args))

    def __enter__(self):
        p = producer()
        self.directory.mkdir(mode=0o700,parents=True)
        self.root.mkdir(mode=0o700)
        try:
            self._phase('provision_and_first_boot', self._prepare)
            return self
        except BaseException:
            self.__exit__(*sys.exc_info())
            raise

    def _prepare(self):
        p=producer()
        from deploy.update.install import Installation,resolve_compose_command
        from deploy.update.processes import ProcessSupervisor
        from deploy.update.contracts import AutomationPolicy,InstallationBinding
        from deploy.update.envfile import read_env
        from deploy.update.standalone_backup import CONFIG_FILES,create_destination
        source=getattr(self.row,'deployment_directory',None)
        if source is None:
            self.row=p.retained_deployment(self.row,self.directory/'retained-deployment')
            source=self.row.deployment_directory
        copy_retained_files(Path(source),self.root,self.row.execution_deployment)
        # Deliberately do not copy source .env, user state or provider credentials.
        values={}
        self.ports={}
        sockets=[]
        try:
            for key in ('FRONTEND_PORT','BACKEND_PORT','FI_COLLECTOR_OTLP_PORT','FI_COLLECTOR_OTLP_HTTP_PORT','AGENTCC_GATEWAY_PORT','MINIO_API_PORT'):
                sock=socket.socket(); sock.bind(('127.0.0.1',0)); sockets.append(sock)
                self.ports[key]=sock.getsockname()[1]
        finally:
            for sock in sockets: sock.close()
        values.update(self.ports,COMPOSE_PROJECT_NAME=self.project,COMPOSE_PROFILES='',GOOGLE_APPLICATION_CREDENTIALS='/dev/null',
            FUTURE_AGI_TELEMETRY_DISABLED='true',FUTURE_AGI_VERSION=self.row.release.manifest['version'])
        values['INTEGRATION_ENCRYPTION_KEY']=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
        for key in ('SECRET_KEY','REDIS_PASSWORD','PG_PASSWORD','CH_PASSWORD','MINIO_ROOT_PASSWORD','AGENTCC_INTERNAL_API_KEY','AGENTCC_ADMIN_TOKEN'):
            values[key]=secrets.token_hex(32)
        self._secret_values.update(str(values[k]) for k in values if any(part in k for part in ('KEY', 'PASSWORD', 'TOKEN')))
        path=self.root/'.env'
        path.write_text(''.join(str(k)+'='+str(v)+'\n' for k,v in values.items()))
        path.chmod(0o600)
        self.policy=AutomationPolicy(probe_timeout_seconds=90,wait_timeout_seconds=self.budgets.startup_seconds,
            recovery_startup_timeout_seconds=self.budgets.startup_seconds,stability_period_seconds=self.budgets.stability_seconds,
            qualification_policy='standalone-cold-restore-v1',migration_timeout_seconds=self.budgets.startup_seconds,
            migration_stall_timeout_seconds=300)
        env={k:os.environ[k] for k in ('PATH','HOME','TMPDIR') if k in os.environ}
        env['DOCKER_CONTEXT']=self.context
        self.runner=ProcessSupervisor(self.policy,docker_context=self.context,environment=env)
        self.runner.deadline=self.deadline
        self.installation=Installation(self.root,environ=env,runner=self.runner,
            docker=[shutil.which('docker') or 'docker','--context',self.context],compose=resolve_compose_command())
        for kind,args in (('container',('ps','-aq','--no-trunc')),('volume',('volume','ls','-q')),('network',('network','ls','-q'))):
            p.need(not self._docker(*args,'--filter','label=com.docker.compose.project='+self.project).strip(),'pre-existing rehearsal resource')
        daemon_info=self._json('info','--format','{{json .}}')
        arch={'aarch64':'arm64','x86_64':'amd64'}.get(daemon_info['Architecture'],daemon_info['Architecture'])
        p.equal(daemon_info['OSType']+'/'+arch,self.row.platform,'native product rehearsal required')
        model=self.installation.config(refresh=True)
        p.need(set(model['services'])=={'app','postgres','clickhouse'} and set(model['volumes'])==set(p.VOLUME_ROLES),'unsupported rehearsal model')
        for service in model['services'].values():
            p.need(not service.get('build') and service.get('restart','no') in ('no','unless-stopped') and not service.get('privileged'),'unsupported rehearsal model')
            for mount in service.get('volumes',[]):
                if mount['type']=='bind' and sys.platform=='darwin':
                    # The shipped /dev/null bind is intentionally NOT rewritten.
                    p.need(Path(mount['source']).is_relative_to(Path('/Users/nikhilpareek')),'home-only Docker bind required')
        from deploy.update.manifest import parse_image_ref
        dependencies=[]
        for name,service in model['services'].items():
            self.installation.docker_run('pull','--platform',self.row.platform,service['image'])
            inspection=self._json('image','inspect',service['image'])[0]
            p.equal(inspection['Os']+'/'+inspection['Architecture'],self.row.platform,'non-native image')
            if name=='app':
                expected=self.row.release.manifest['components']['standalone']
                p.need(any(parse_image_ref(ref).repository==parse_image_ref(expected['image']).repository and
                    parse_image_ref(ref).digest==expected['digest'] for ref in inspection.get('RepoDigests',[])),
                    'source image tag differs from explicit pin')
            if name!='app':
                repository=parse_image_ref(service['image']).repository
                digests={parse_image_ref(ref).digest for ref in inspection.get('RepoDigests',[])
                         if parse_image_ref(ref).repository==repository}
                p.need(len(digests)==1,'dependency digest ambiguous')
                dependencies.append(dict(service=name,image=service['image'],digest=digests.pop()))
        if self.dependencies:
            p.equal(sorted(dependencies,key=lambda r:r['service']),sorted(self.dependencies,key=lambda r:r['service']),
                    'retained dependency lock differs')
        self.dependencies=tuple(sorted(dependencies,key=lambda r:r['service']))
        target=self.target['components']['standalone']
        pin=target['image']+'@'+target['digest']
        self.installation.docker_run('pull','--platform',self.row.platform,pin)
        pinned=self._json('image','inspect',pin)[0]
        p.equal(pinned['Architecture'],self.row.platform.split('/')[1],'non-native target image')
        self.installation.docker_run('pull','--platform',self.row.platform,target['image'])
        p.equal(self._json('image','inspect',target['image'])[0]['Id'],pinned['Id'],'target tag differs from explicit pin')
        self._phase('source_compose_up', self.installation.compose_run, 'up','-d','--no-build','--pull','never')
        self._phase('source_first_boot', self._wait_healthy)
        self._remember_resources()
        daemon=self._docker('info','--format','{{.ID}}').strip()
        identity=self.installation.identity()
        binding=InstallationBinding(uuid.uuid4().hex,p.sha(p.canonical_json(identity)),p.sha(p.canonical_json(identity)),
            self.root,self.root/'.env',self.root/'.env','standalone',False,self.project,(self.root/'docker-compose.yml',),(),self.context,daemon,os.geteuid())
        self.registration=SimpleNamespace(binding=binding)
        self.destination=self.directory/'backups'; create_destination(self.destination)
        self.proofs=self.directory/'proofs'; create_destination(self.proofs)
        from deploy.update.standalone_state import StandaloneStateObserver
        self.observer=StandaloneStateObserver(self.registration,timeout=180,stability_period_seconds=self.budgets.stability_seconds)
        self.witnesses=ProductWitnesses(self)

    def _remember_resources(self):
        p=producer()
        for row in self.installation.containers():
            raw=self._json('inspect',row['id'])[0]
            p.need(raw['Config']['Labels']['com.docker.compose.project']==self.project,'foreign rehearsal container')
            self.owned_containers[raw['Id']]=raw['Created']
        for kind,destination in (('volume',self.owned_volumes),('network',self.owned_networks)):
            names=self._docker(kind,'ls','-q','--filter','label=com.docker.compose.project='+self.project).split()
            for name in names:
                raw=self._json(kind,'inspect',name)[0]
                p.need(raw['Name'].startswith(self.project+'_') and raw['Labels']['com.docker.compose.project']==self.project,'foreign rehearsal storage')
                destination[name]=raw.get('CreatedAt') or raw.get('Created')

    def _wait_healthy(self):
        p=producer(); deadline=min(self.deadline,time.monotonic()+self.budgets.startup_seconds)
        next_sample = 0
        try:
            while time.monotonic()<deadline:
                rows=self.installation.containers()
                if len(rows)==3:
                    records=[self._json('inspect',r['id'])[0] for r in rows]
                    if all(r['State']['Running'] and r['State'].get('Health',{}).get('Status')=='healthy' for r in records): return
                    p.need(not any(r['State'].get('OOMKilled') or r['RestartCount'] for r in records),'source boot failed')
                if time.monotonic() >= next_sample:
                    self._collect_diagnostics()
                    next_sample = time.monotonic() + 30
                time.sleep(min(2, max(0, deadline-time.monotonic())))
            raise p.ManualRelease('suite_timed_out')
        finally:
            self._collect_diagnostics()

    def app_python(self,source,argument):
        p=producer()
        app=next(r['id'] for r in self.installation.containers() if r['service']=='app')
        # Attach Python's command like the shipping helpers: a standalone -c
        # is a Docker routing flag to the supervisor. JSON stays on stdin.
        result=self.runner([*self.installation.docker,'exec','-i',app,'python','-I','-B','-c'+source],
            input=p.canonical_json(argument),cwd=str(self.root),env=self.installation.subprocess_env(),
            capture_output=True,timeout=180)
        if result.returncode != 0:
            self._diagnostic_data['product_operations'].append(dict(
                script_sha256=p.sha(source.encode()), exit_code=result.returncode,
                stderr=self._redact(result.stderr, 4096)))
            self._diagnostic_data['product_operations'] = self._diagnostic_data['product_operations'][-8:]
            self._collect_diagnostics()
        p.need(result.returncode==0,'product witness operation failed')
        return decode_product_output(result.stdout)

    def clickhouse(self,sql,*,params=None):
        cid=next(r['id'] for r in self.installation.containers() if r['service']=='clickhouse')
        argv=['exec',cid,'clickhouse-client','--user',self.installation.env.get('CH_USERNAME','default'),
              '--password',self.installation.env.get('CH_PASSWORD',''),'--query',sql]
        argv.extend('--param_'+k+'='+v for k,v in (params or {}).items())
        return [json.loads(line) for line in self._docker(*argv).splitlines() if line.strip()]

    def pending_migration_tables(self):
        # Must be captured from the target's real MigrationLoader/model state
        # against the source's applied set BEFORE any reference workload/start.
        # Never infer this from a successful target or the rate's own tables.
        if 'pending_migration_tables' not in self.corpus or 'target_pending_plan' not in self.corpus:
            raise producer().ManualRelease('migration_measurement_unavailable')
        scope = copy.deepcopy(self.corpus['pending_migration_tables'])
        self.reference_registry = producer().select_reference_registry(self.registry, scope,
            plan=self.corpus['target_pending_plan'])
        return scope

    def seed_reference_table(self,entry,rows):
        p=producer()
        key=(entry.get('app_label'),entry.get('model'))
        if entry['database']!='postgres' or key not in APPEND_ONLY_MODELS:
            # A6 needs product-specific distributions for each registered table.
            # Never create a generic all-default row or silently skip a table.
            raise p.ManualRelease('high_volume_reference_unavailable')
        self.app_python(REFERENCE_SEED,dict(entry,rows=rows))

    def measure_reference_table(self,entry):
        p=producer()
        # Registry identifiers are validated by the producer, then quoted by the
        # product DB adapter. Never interpolate a release-supplied SQL program.
        if entry['database']=='postgres':
            source=DJANGO_PREFIX+"""
from django.db import connection
with connection.cursor() as c:
    c.execute('SELECT n.nspname,c.relname,pg_total_relation_size(c.oid) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relname=%s AND n.nspname=ANY(current_schemas(false))',[a['table']])
    rows=c.fetchall(); assert len(rows)==1
    schema,table,size=rows[0]
    q=connection.ops.quote_name
    c.execute('SELECT count(*) FROM '+q(schema)+'.'+q(table))
    count=c.fetchone()[0]
print(json.dumps(dict(engine='postgres',table=schema+'.'+table,rows=count,bytes=size)))
"""
            return self.app_python(source,entry)
        result=self.clickhouse('SELECT database,table,sum(rows) AS rows,sum(bytes_on_disk) AS bytes FROM system.parts WHERE active AND table={table:String} GROUP BY database,table FORMAT JSONEachRow',params={'table':entry['table']})
        p.need(len(result)==1,'ambiguous/missing registered ClickHouse table')
        r=result[0]
        return dict(engine='clickhouse',table=r['database']+'.'+r['table'],rows=int(r['rows']),bytes=int(r['bytes']))

    def cold_backup(self):
        p=producer()
        from deploy.update.standalone_backup import capture_binding,StandaloneBackup,BackupPolicy
        from deploy.update.backup_proof import BackupExpectation
        from deploy.update.contracts import installation_binding_hash
        from deploy.update.recovery import RecoveryLedger
        self.source=self.observer.observe(self.installation,current_release=self.row.release,
            execution_deployment=self.row.execution_deployment,boundary='enrollment')
        self.corpus['source_observation_preimages']=retain_source_preimages(self.installation,self.registration,self.row.release,self.source)
        self.binding=capture_binding(self.installation,installation_fingerprint=self.registration.binding.installation_fingerprint,
            docker_context=self.context,daemon_id=self.registration.binding.daemon_identity,project=self.project,profiles=(),
            verified_images={s:r['image'] for s,r in self.source.containers.items()},deployment_hashes=self.row.execution_deployment['files'])
        self.expected=BackupExpectation(uuid.uuid4().hex,self.registration.binding.installation_fingerprint,
            p.manifest_hash(self.target),self.row.release.version,datetime.now(timezone.utc).replace(microsecond=0),
            p.topology_profile('standalone',()).backup_domains)
        self.ledger=RecoveryLedger.reserve(self.destination,self.expected,operation_token=uuid.uuid4().hex,run_id=uuid.uuid4().hex,
            binding_sha256=installation_binding_hash(self.registration.binding))
        self.backup=StandaloneBackup(self.installation,self.binding,self.destination,
            policy=BackupPolicy(timeout_seconds=3600,readiness_seconds=self.budgets.startup_seconds))
        fd=os.open(self.proofs,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            self.artifact,self.backup_measurement,self.backup_transfer=p.measure_transfer(
                lambda:self.backup.run_quiesced(self.expected,self.ledger,self.source.runtime_observation,proof_directory_fd=fd),
                self.capture_snapshot)
        finally: os.close(fd)
        self.backup.retire_helpers(self.ledger)
        self.corpus.update(source_runtime_observation=wire(self.source.runtime_observation),
            source_installation_binding=wire(self.registration.binding),source_backup_seal=wire(self.artifact.seal),
            source_backup_proof=json.loads((self.ledger.directory/'proof.json').read_bytes()),
            source_image_inspections={s:{k:self._json('image','inspect',image)[0][k] for k in ('Id','Os','Architecture','RepoDigests')}
                for s,image in json.loads(self.binding.raw)['images'].items()})
        self.corpus['source_snapshot_inventory'],self.corpus['source_snapshot_members']=self.capture_snapshot()
        self.corpus['backup_transfer']=self.backup_transfer

    def capture_snapshot(self):
        directory=self.ledger.directory
        return json.loads((directory/'inventory.json').read_bytes()),{role:[json.loads(line) for line in
            (directory/(role+'.members.jsonl')).read_bytes().splitlines()] for role in producer().VOLUME_ROLES}

    def start_target(self,estimate):
        """Use normal envcommit + AutomaticRuntime; never a manual migrate."""
        p=producer()
        from deploy.update import envfile
        from deploy.update.automatic_runtime import AutomaticRuntime,HostRuntimeObserver
        from deploy.update.manifest import parse_image_ref
        image=self.target['components']['standalone']
        tag=parse_image_ref(image['image']).tag
        self.inverse=envfile.recovery_record(self.root/'.env',{'FUTURE_AGI_VERSION':tag})
        op=self.ledger.begin('env_commit',identity={'run_id':self.ledger.snapshot['run_id']})
        envfile.write_changes(self.root/'.env',{'FUTURE_AGI_VERSION':tag},recovery=self.inverse)
        self.installation.reload_env()
        self.ledger.complete(op,{'env_sha256':p.sha((self.root/'.env').read_bytes())})
        b=json.loads(self.binding.raw)
        self.runtime=AutomaticRuntime(self.installation,self.ledger,b,observer=HostRuntimeObserver(self.installation,self.registration,b,timeout=30))
        ids=dict(b['images']); ids['app']=self._json('image','inspect',image['image']+'@'+image['digest'])[0]['Id']
        self.ledger.checkpoint(target_model=self.installation.config(refresh=True))
        roster=self.runtime.create(ids)
        self.runtime.start_and_wait(roster,self.policy,estimate)
        self._remember_resources()
        return roster

    def restore(self):
        """Storage is restored in place; original volume objects remain enrolled."""
        from deploy.update.standalone_restore import StandaloneRestore
        from deploy.update.automatic_runtime import AutomaticRuntime,HostRuntimeObserver
        p=producer()
        self.ledger.decide_restore()
        before=json.loads(self.binding.raw)['volumes']
        adapter=StandaloneRestore(self.installation,self.binding,self.destination,policy=self.backup.policy)
        try:
            _,self.restore_measurement,self.restore_transfer=p.measure_transfer(
                lambda:adapter.resume(self.expected,self.ledger,env_recovery=getattr(self,'inverse',None)),self.capture_snapshot)
        finally: adapter.close()
        self.installation.reload_env()
        b=json.loads(self.binding.raw)
        runtime=AutomaticRuntime(self.installation,self.ledger,b,observer=HostRuntimeObserver(self.installation,self.registration,b,timeout=30))
        roster=runtime.create(b['images'],previous=True)
        runtime.start_and_wait(roster,self.policy,previous=True)
        self._remember_resources()
        after={role:{k:self._json('volume','inspect',v['Name'])[0].get(k) for k in v} for role,v in before.items()}
        p.equal(after,before,'restore replaced physical source volumes')
        self.corpus['restore_transfer']=self.restore_transfer

    def _source_snapshot(self):
        p=producer()
        self.corpus['source_witnesses']=p.witness_inventory(self.witnesses.create('source'))
        self.redis_key='update-qualification:'+uuid.uuid4().hex
        p.equal(self.app_python(REDIS_OPERATION,dict(action='seed',key=self.redis_key,value=uuid.uuid4().hex)),
                {'seeded':True},'source Redis witness was not independently read')
        self.corpus['redis_source_seeded']=True
        self.corpus['source_witnesses_rechecked']=p.witness_inventory(self.witnesses.read('source'))
        p.equal(self.corpus['source_witnesses_rechecked'],self.corpus['source_witnesses'],'source witnesses changed before backup')
        p.need(hasattr(self, 'reference_registry'), 'pre-start reference scope not measured')
        self.corpus['reference_tables_before_backup']=[self.measure_reference_table(r) for r in self.reference_registry]
        self.cold_backup()
        snapshot=self.corpus['source_snapshot_inventory']
        self.corpus['large_volume_reference']=dict(snapshot_generation=p.sha(p.canonical_json(snapshot)),
            registry_sha256=p.sha(p.read_bytes(p.ROOT/'deploy/update/high-volume-tables.json',p.MAX_EVIDENCE_BYTES)),
            reference_rows=p.REFERENCE_ROWS,tables=self.corpus['reference_tables_before_backup'])
        p.equal(self.source.runtime_observation.release_version,self.row.release.version,'source version differs')
        p.need(self.backup.verify_hold(self.ledger,self.source.runtime_observation) is not None,'source cold hold unavailable')

    def _source_migrations(self):
        p=producer(); runtime=self.source.runtime_observation
        # The shipping full observer only returns after independently checking
        # exact applied migrations against the complete image inventory.
        p.need(runtime.ready and runtime.stable and runtime.migration_inventory_sha256 and
               runtime.applied_migrations_sha256 and runtime.active_app_mapping_sha256,'source migrations not observed')
        p.equal(self.corpus['source_runtime_observation']['migration_inventory_sha256'],runtime.migration_inventory_sha256,
                'source migration inventory changed')

    def _snapshot_sealed(self):
        p=producer()
        p.equal(self.artifact.seal.inventory_sha256,p.sha(p.canonical_json(self.corpus['source_snapshot_inventory'])),
                'shipping cold snapshot seal differs')
        p.need(self.ledger.backup_seal==self.artifact.seal and self.ledger.direction=='forward_pending','cold snapshot not authoritative')

    def _domain_mapping(self):
        p=producer()
        p.equal(set(self.corpus['source_snapshot_inventory']['archives']),set(p.VOLUME_ROLES),'incomplete persistent stores')
        p.equal(set(self.artifact.proof.covered_domains),set(p.topology_profile('standalone',()).backup_domains),'incomplete persistent domains')

    def _source_domain(self,domain):
        p=producer()
        observed=[r for r in self.corpus['source_witnesses_rechecked'] if r['domain']==domain]
        p.need(bool(observed),'unrun source domain witness')
        p.equal(observed,[r for r in self.corpus['source_witnesses'] if r['domain']==domain],'source domain witness differs')

    def _normal_model(self):
        p=producer()
        p.equal(self.installation.config(refresh=True),self.ledger.snapshot['target_model'],'runtime transformed Compose model')
        p.need(self.ledger.snapshot.get('target_roster') is not None,'target not started through shipping runtime')

    def _target_health(self):
        p=producer()
        rows=self.runtime.sample(self.ledger.snapshot['target_roster'],progress=False)
        p.need(all(v[0] and v[1] for v in rows.containers.values()),'target health failed')

    def _target_stability(self):
        p=producer()
        roster=self.ledger.snapshot['target_roster']
        baseline=self.runtime.sample(roster,progress=False)
        started=time.monotonic()
        while time.monotonic()-started<self.policy.stability_period_seconds:
            current=self.runtime.sample(roster,progress=False)
            p.equal(current.containers,baseline.containers,'target restart/start identity changed during stability')
            p.need(all(v[0] and v[1] and v[2]==0 for v in current.containers.values()),'target unstable')
            time.sleep(.5)

    def _target_identities(self):
        p=producer(); roster=self.ledger.snapshot['target_roster']
        for service,identity in roster.items():
            p.equal(self.runtime._exact(identity)['Image'],identity['Image'],'target image identity changed')
        self.runtime._stores()
        p.equal(self._json('image','inspect',self.target['components']['standalone']['image']+'@'+
            self.target['components']['standalone']['digest'])[0]['Id'],roster['app']['Image'],'target image differs from qualified pin')

    def _redis_ephemeral(self):
        p=producer()
        p.need(self.corpus.get('redis_source_seeded') is True and hasattr(self,'redis_key'),
               'source Redis witness was not seeded')
        result=self.app_python(REDIS_OPERATION,dict(action='verify_reset',key=self.redis_key))
        p.equal(result,{'absent':True},'Redis persisted source cache data into target')
        self.corpus['redis_target_reset']=result

    def _reference_scope_verified(self):
        producer().validate_reference_inventory(self.corpus['large_volume_reference'], registry=self.registry,
            pending_migration_tables=self.corpus['pending_migration_tables'], plan=self.corpus['target_pending_plan'])

    def suite_assertions(self,suite):
        p=producer()
        if suite=='source_snapshot':
            result={'source_identity':self._source_snapshot,'source_migrations_exact':self._source_migrations,
                'snapshot_sealed':self._snapshot_sealed,'persistent_domain_mapping':self._domain_mapping,
                'witness_namespace_complete':lambda:p.witness_inventory(self.corpus['source_witnesses']) and
                    p.equal(self.corpus['source_witnesses'],self.corpus['source_witnesses_rechecked'],'source namespace differs'),
                'large_volume_reference':self._reference_scope_verified}
            result.update({'source_witness:'+domain:lambda d=domain:self._source_domain(d)
                           for domain in p.topology_profile('standalone',()).backup_domains})
            return result
        if suite=='target_readiness':
            return {'normal_compose_direct_start':self._normal_model, 'health':self._target_health,
                'restart_count_stability':self._target_stability, 'final_identities':self._target_identities,
                'redis_ephemeral':self._redis_ephemeral}
        # Per-migration *execution* boundaries and the complete registry-specific
        # workloads are not yet available. Do not turn migration recorder times,
        # API health or a manual migrate into the missing A6 assertions.
        raise p.ManualRelease('migration_measurement_unavailable' if suite=='migration_forward' else 'suite_unrun')

    def measurements(self):
        raise producer().ManualRelease('migration_measurement_unavailable')

    def private_evidence(self):
        return copy.deepcopy(self.corpus)

    def __exit__(self,*exc):
        if not hasattr(self,'installation'): return
        p=producer()
        from deploy.update.processes import ProcessSupervisor
        # Cleanup has its own bounded supervisor; never clear uncertainty or
        # extend the original mutation authority after its deadline.
        cleanup=copy.copy(self.installation)
        cleanup.runner=ProcessSupervisor(self.policy,docker_context=self.context,
            environment=self.runner.environment,grace_seconds=1)
        cleanup.runner.deadline=time.monotonic()+180
        self._collect_diagnostics()
        self.installation=cleanup
        # Discover only our exact unique project after partially failed create.
        # Verify physical identity again before removing it; never prune by prefix.
        self._remember_resources()
        for cid,created in list(self.owned_containers.items()):
            rows=json.loads(self._docker('inspect',cid,check=False) or '[]')
            if not rows: continue
            row=rows[0]
            p.need(row['Id']==cid and row['Created']==created and row['Config']['Labels']['com.docker.compose.project']==self.project,'cleanup container identity changed')
            self._docker('rm','-f',cid)
        for kind,owned in (('volume',self.owned_volumes),('network',self.owned_networks)):
            for name,created in owned.items():
                row=self._json(kind,'inspect',name)[0]
                p.need((row.get('CreatedAt') or row.get('Created'))==created and row['Labels']['com.docker.compose.project']==self.project,'cleanup storage identity changed')
                self._docker(kind,'rm',name)
        for args in (('ps','-aq'),('volume','ls','-q'),('network','ls','-q')):
            p.need(not self._docker(*args,'--filter','label=com.docker.compose.project='+self.project).strip(),'rehearsal cleanup unverified')
        self.cleanup_verified=True


def create_disposable_rehearsal(*args,**kwargs):
    # Refuse BEFORE provisioning a product stack that cannot complete. This is
    # deliberately not a passing product executor: see the lane report's gaps.
    if not SHIPPING_TRANSACTION_INTEGRATED:
        raise producer().ManualRelease('shipping_executor_unavailable')
    return ShippingRehearsal(*args,**kwargs)
