"""Source-only PostgreSQL Omega investigation fixture for tracer 0118.

tracer/0118_backfill_feed_issue_statuses declares the registered
tracer_trace_investigation_finding/_job/_report tables. It copies each Omega
job's root_end_time into Error Feed memberships whose occurrence time is NULL,
following membership -> finding -> report -> job. The fixture gives that
backfill real work and real negative controls, in one dedicated project and a
separate UUID namespace, so product witnesses and the Error Feed/trace fixtures
are left alone. Because it also adds issues and memberships to registered Error
Feed tables, the shipping driver completes it before those tables are seeded and
measured, whenever the investigation tables are in the selected scope.

Per 100k-job block, every job has one completed attempt, one current Omega
report (which the job's current_report points at) and one finding. Issues ("QI"
clusters) hold 100 jobs each; their counters and first/last seen times follow
the released grouping recount over live, current Omega memberships.
  backfill          69,000  root end time set, live chain, one membership
  deleted_backfill   1,000  as above, but job/attempt/report/finding/membership
                            soft-deleted (0118's historical managers do not
                            filter deleted rows, so these are pending too)
  no_root_end       25,000  job has no root end time; membership stays NULL
  no_membership      5,000  root end time set, finding has no membership
One legacy_scan report per 100 jobs (no job/attempt) adds a finding and a
membership that 0118 cannot fill. Totals: 100k jobs and attempts, 101k reports
and findings, 1k issues and 96k memberships, of which 70k satisfy 0118's join.

0118 also creates one FeedEscalationPolicy per non-deleted workspace. The
fixture adds no workspaces for that step on purpose: accounts_workspace is not a
registered high-volume table, so it has no row floor; the step is a plain
get_or_create that already runs over every existing source workspace; and extra
workspaces in the witness organisation could change what other witnesses read.

The source schema has no occurrence-time columns (0117 adds them NULL), so every
source membership is pending and an "already filled" cohort cannot exist before
the upgrade. The fixture writes no target-only field, and the source check
refuses a membership table that already has one. v1.56.1 through v1.59.1 have
the same investigation, issue and membership models; columns, named constraints
and the unconditional unique index on Report.attempt_id are checked before any
write. Plans are expectations, never measurements: the shipping driver's
independent count(*) / pg_total_relation_size measurement remains authoritative.

Every app_python call has a fixed 180s limit, so the payload, baseline and
cohort reads are bounded per call at any accepted scale. Writes go in 2,500-job chunks.
Payload verification reads 10,000 jobs per call. PREPARE checks the project;
separate calls count physical and fixture rows before the first chunk, including
on resume. These counts and the final physical, fixture and cohort counts use
one UUID-range bucket per scale block. The host sums the disjoint ranges, so
extra rows are counted too. At scale one the single bucket covers the table.
Each bucket first probes the whole table's rows in its key range with a LIMIT
of BUCKET_ROWS + 1 and refuses a range over BUCKET_ROWS (1,000,000), so a large
pre-existing table or a skewed range cannot make one call unbounded.

After the target migrates, qualification's large_volume_migrations assertion
runs the same bounded slices and buckets on the target. It checks that every
eligible membership, including the soft-deleted ones, carries its own job's root
end time with source omega_root_end, and that every negative control is still
NULL. Runtime evidence
repair skips soft-deleted rows, so only 0118 can fill those 1,000 per block.
Host code is stdlib; the fixed programs below run in the app container.
"""
from __future__ import annotations

import time


INVESTIGATION_REFERENCE_MODELS = {
    ('tracer', 'TraceInvestigationFinding'): 'tracer_trace_investigation_finding',
    ('tracer', 'TraceInvestigationJob'): 'tracer_trace_investigation_job',
    ('tracer', 'TraceInvestigationReport'): 'tracer_trace_investigation_report',
}
# Registered Error Feed tables this fixture also writes. Registry order measures
# them before the investigation tables, and each seed-time measurement must equal
# the pre-backup one, so the driver completes this family before seeding them.
INVESTIGATION_REFERENCE_SHARED_TABLES = ('tracer_error_cluster_traces', 'tracer_trace_error_group')
CHUNK_JOBS = 2500
# Jobs whose payloads one read-only call re-derives and checks (four chunks).
VERIFY_JOBS = 10000
# Columns 0117 adds to the membership table. They must be absent on the source
# and present on the target.
TARGET_ONLY_MEMBER_COLUMNS = ('occurred_at', 'occurrence_time_source')
# Per 100k-job block, in ordinal order.
COHORTS = {'backfill': 69000, 'deleted_backfill': 1000, 'no_root_end': 25000, 'no_membership': 5000}
LEGACY_EVERY = 100
GROUP_SIZE = 100
# Reviewed source field inventories (BaseModel fields included). Identical in
# v1.56.1, v1.58.0 and v1.59.1; the target only adds membership columns.
SOURCE_FIELDS = {
    'Job': 'id created_at updated_at deleted deleted_at organization workspace project workload_type '
        'test_execution call_execution trace_id root_span_id root_end_time generation state not_before current_report',
    'Attempt': 'id created_at updated_at deleted deleted_at job generation worker_id engine_version status '
        'lease_token_digest lease_expires_at read_cutoff memory_snapshot_id memory_digest memory limits '
        'completed_at cancellation_reason',
    'Report': 'id created_at updated_at deleted deleted_at organization workspace project source test_execution '
        'workload_type trace_id source_record_id source_version recorded_at is_current error_message turn_count '
        'has_issues grouping_gate job attempt idempotency_key result_digest contract_version evidence_digest '
        'execution_status outcome coverage_scope observed_span_count observed_call_count read_complete '
        'future_arrivals_known model_calls input_tokens output_tokens cost_usd cost_status grouping_status',
    'Finding': 'id created_at updated_at deleted deleted_at report finding_id ordinal kind statement recovery '
        'source_finding_id category group_label fix_layer confidence requirement cluster',
    'Group': 'id created_at updated_at deleted deleted_at redirect_to project source eval_target_type '
        'target_type test_execution issue_group issue_category fix_layer title status eval_config '
        'success_trace cluster_id error_type total_events unique_traces unique_users first_seen last_seen '
        'error_ids combined_impact combined_description error_count assignee priority '
        'severity_assessment_status severity_source severity_reason external_issue_url external_issue_id '
        'rca_synthesis rca_fix rca_confidence rca_evidence_trace_ids rca_at rca_failures_at_run rca_trace trace_impact',
    'Member': 'id created_at updated_at deleted deleted_at trace span trace_session cluster scan_issue finding eval_logger',
}
# Named model constraints and their physical kind, from tracer 0099/0109.
SOURCE_CONSTRAINTS = {
    'Job': {'unique_trace_investigation_job': 'unique', 'unique_simulation_call_investigation_job': 'unique',
        'valid_trace_investigation_workload': 'check'},
    'Attempt': {'unique_trace_investigation_attempt': 'unique'},
    'Report': {'unique_inv_source_record': 'unique', 'unique_current_trace_investigation': 'unique',
        'unique_current_simulation_investigation': 'unique', 'valid_inv_report_source_fields': 'check',
        'unique_trace_investigation_report_key': 'unique'},
    'Finding': {'unique_inv_finding_id': 'unique'},
    'Group': {'unique_project_cluster_if_not_deleted': 'unique', 'valid_error_group_target_scope': 'check'},
    'Member': {},
}


def investigation_reference_plan(scale):
    assert type(scale) is int and scale > 0
    assert sum(COHORTS.values()) == 100000 and 100000 % CHUNK_JOBS == 0
    assert CHUNK_JOBS % LEGACY_EVERY == 0 and CHUNK_JOBS % GROUP_SIZE == 0
    legacy = 100000 // LEGACY_EVERY
    members = 100000 - COHORTS['no_membership'] + legacy
    return dict(counts={
            'tracer_trace_investigation_finding': (100000 + legacy) * scale,
            'tracer_trace_investigation_job': 100000 * scale,
            'tracer_trace_investigation_report': (100000 + legacy) * scale},
        cohorts=dict(
            groups=100000 // GROUP_SIZE * scale, attempts=100000 * scale, legacy_reports=legacy * scale,
            no_root_end_jobs=COHORTS['no_root_end'] * scale, memberships=members * scale,
            backfill_memberships=(COHORTS['backfill'] + COHORTS['deleted_backfill']) * scale,
            deleted_backfill_memberships=COHORTS['deleted_backfill'] * scale,
            no_root_end_memberships=COHORTS['no_root_end'] * scale, legacy_memberships=legacy * scale,
            chain_consistent_memberships=members * scale,
            findings_without_membership=COHORTS['no_membership'] * scale))


def investigation_target_plan(scale):
    """Exact fixture membership states after 0118 has run on the target."""
    assert type(scale) is int and scale > 0
    legacy = 100000 // LEGACY_EVERY
    return dict(memberships=(100000 - COHORTS['no_membership'] + legacy) * scale,
        filled_memberships=(COHORTS['backfill'] + COHORTS['deleted_backfill']) * scale,
        filled_deleted_memberships=COHORTS['deleted_backfill'] * scale,
        null_memberships=(COHORTS['no_root_end'] + legacy) * scale,
        null_no_root_end_memberships=COHORTS['no_root_end'] * scale,
        null_legacy_memberships=legacy * scale)


# Pure payload logic is shared verbatim by all container operations and tests.
ROWS = ('\nCOHORTS = ' + repr(COHORTS) + '\nLEGACY_EVERY = ' + repr(LEGACY_EVERY)
    + '\nGROUP_SIZE = ' + repr(GROUP_SIZE) + '\n') + """
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
NAMESPACE=uuid.uuid5(uuid.NAMESPACE_URL,'futureagi-qualification-investigation-reference:'+a['namespace'])
def identity(kind,ordinal):
    return uuid.uuid5(NAMESPACE,f'{kind}:{ordinal}')
def cohort(ordinal):
    assert type(ordinal) is int and ordinal>=0, 'investigation_reference: invalid ordinal'
    local=ordinal%100000
    for name,count in COHORTS.items():
        if local<count: return name
        local-=count
    raise AssertionError('investigation_reference: ordinal outside cohort plan')
def code(group):
    digits='0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'
    value=''
    while group:
        group,remainder=divmod(group,36)
        value=digits[remainder]+value
    result='QI'+(value or '0').zfill(12)
    assert len(result)<=20, 'investigation_reference: cluster code too long'
    return result
def digest(kind,ordinal):
    return 'sha256:'+identity(kind,ordinal).hex+identity(kind+'-tail',ordinal).hex
def times(when,ordinal):
    # Distinct whole-second root end times, before the fixture history.
    ended=when-timedelta(seconds=ordinal+1)
    cutoff=ended+timedelta(seconds=30)
    return ended,cutoff,cutoff+timedelta(minutes=2)
def recount(when,group):
    # Released grouping _recount: distinct live findings on live, current,
    # completed Omega trace reports whose membership trace is the report trace.
    # Deleted chains, legacy_scan reports and findings without a membership do
    # not count, so some issues legitimately have zero counts and no times.
    counted=[ordinal for ordinal in range(group*GROUP_SIZE,(group+1)*GROUP_SIZE)
        if cohort(ordinal) in ('backfill','no_root_end')]
    recorded=[times(when,ordinal)[2] for ordinal in counted]
    return dict(error_count=len(counted),total_events=len(counted),unique_traces=len(counted),
        first_seen=min(recorded) if recorded else None,last_seen=max(recorded) if recorded else None)
def occurrences(rows):
    # What 0118 must leave on each membership, from the payload chain alone:
    # (occurred_at, occurrence_time_source, the membership's own job root end).
    jobs={row['id']:row for row in rows['Job']}
    reports={row['id']:row for row in rows['Report']}
    findings={row['id']:row for row in rows['Finding']}
    expected={}
    for row in rows['Member']:
        job=jobs.get(reports[findings[row['finding_id']]['report_id']]['job_id'])
        ended=None if job is None else job['root_end_time']
        expected[row['id']]=(None,'',None) if ended is None else (ended,'omega_root_end',ended)
    return expected
def project_payload(context):
    when=datetime.fromisoformat(context['historical_at'])
    return dict(id=identity('project',0),organization_id=uuid.UUID(a['organization_id']),
        workspace_id=uuid.UUID(a['workspace_id']),user_id=uuid.UUID(context['user_id']),
        name='qualification-investigation-'+NAMESPACE.hex[:12],model_type='GenerativeLLM',trace_type='observe',
        source='prototype',metadata={'qualification_investigation_reference':1,'namespace':a['namespace'],
            'requested_rows':a['rows']},
        config=[],session_config=[],tags=['qualification-reference'],simulation_agent_id=None,
        created_at=when,updated_at=when,deleted=False,deleted_at=None)
def payloads(context,start,stop):
    when=datetime.fromisoformat(context['historical_at'])
    organization,workspace=uuid.UUID(a['organization_id']),uuid.UUID(a['workspace_id'])
    project=identity('project',0)
    rows=dict(Group=[],Job=[],Attempt=[],Report=[],Finding=[],Member=[])
    for ordinal in range(start,stop):
        name=cohort(ordinal)
        group=identity('group',ordinal//GROUP_SIZE)
        live=dict(created_at=when,updated_at=when,deleted=False,deleted_at=None)
        history=dict(live,deleted=True,deleted_at=when) if name=='deleted_backfill' else live
        ended,cutoff,recorded=times(when,ordinal)
        if ordinal%GROUP_SIZE==0:
            label=code(ordinal//GROUP_SIZE)
            counters=recount(when,ordinal//GROUP_SIZE)
            rows['Group'].append(dict(live,id=group,redirect_to_id=None,project_id=project,source='scanner',
                eval_target_type=None,target_type='error_feed',test_execution_id=None,
                issue_group='Tool Failures',issue_category='Language-only',fix_layer='Tools',
                title='Historical investigation issue '+label,status='escalating',eval_config_id=None,
                success_trace_id=None,cluster_id=label,error_type='qualification-investigation-tool-failure',
                unique_users=0,error_ids=[],combined_impact='HIGH',
                combined_description='Synthetic qualification history: Omega findings for one failing tool.',
                trace_impact=None,assignee_id=None,priority='medium',
                severity_assessment_status='unassessed',severity_source='legacy',severity_reason='',
                external_issue_url=None,external_issue_id=None,rca_synthesis=None,rca_fix=None,
                rca_confidence=None,rca_evidence_trace_ids=[],rca_at=None,rca_failures_at_run=None,rca_trace=None,
                **counters))
        trace=identity('trace',ordinal)
        job,attempt,report,finding=(identity(kind,ordinal) for kind in ('job','attempt','report','finding'))
        rows['Job'].append(dict(history,id=job,organization_id=organization,workspace_id=workspace,
            project_id=project,workload_type='trace',test_execution_id=None,call_execution_id=None,
            trace_id=trace,root_span_id=identity('span',ordinal).hex[:16],
            root_end_time=None if name=='no_root_end' else ended,generation=1,state='completed',
            # The job points at its own current report. CHUNK inserts it NULL and
            # sets it after the report exists, in the same transaction.
            not_before=cutoff,current_report_id=report))
        rows['Attempt'].append(dict(history,id=attempt,job_id=job,generation=1,
            worker_id='qualification-investigation-worker-'+str(ordinal%8),engine_version='omega-v1',
            status='completed',lease_token_digest=identity('lease',ordinal).hex*2,
            lease_expires_at=cutoff+timedelta(minutes=5),read_cutoff=cutoff,
            memory_snapshot_id='memory-'+str(ordinal),memory_digest=digest('memory',ordinal),memory=[],
            limits={'model_calls':8,'input_tokens':200000},completed_at=recorded,cancellation_reason=''))
        rows['Report'].append(dict(history,id=report,organization_id=organization,workspace_id=workspace,
            project_id=project,source='omega',test_execution_id=None,workload_type='trace',trace_id=trace,
            source_record_id=None,source_version=None,recorded_at=recorded,is_current=True,error_message=None,
            turn_count=None,has_issues=True,grouping_gate=None,job_id=job,attempt_id=attempt,
            idempotency_key='qualification-investigation-'+report.hex,result_digest=digest('result',ordinal),
            contract_version='omega-investigation/v1',evidence_digest=digest('evidence',ordinal),
            execution_status='completed',outcome='failure',coverage_scope='available trace at read cutoff',
            observed_span_count=2+ordinal%7,observed_call_count=None,read_complete=ordinal%5!=0,
            future_arrivals_known=ordinal%3==0,model_calls=1+ordinal%4,input_tokens=800+ordinal%4000,
            output_tokens=40+ordinal%400,cost_usd=Decimal(1000+ordinal%9000).scaleb(-9),
            cost_status='complete',grouping_status='completed'))
        member=name!='no_membership'
        rows['Finding'].append(dict(history,id=finding,report_id=report,finding_id='f0',ordinal=0,
            kind='tool_failure',statement='The agent reported success after the order lookup tool failed for order '
                +str(10000+ordinal)+'.',
            recovery='none',source_finding_id=None,category='Tool Failures',group_label='Order lookup failures',
            fix_layer='Tools',confidence='H',requirement_id=None,cluster_id=group if member else None))
        if member:
            rows['Member'].append(dict(history,id=identity('member',ordinal),trace_id=trace,span_id=None,
                trace_session_id=None,cluster_id=group,scan_issue_id=None,finding_id=finding,eval_logger_id=None))
        if ordinal%LEGACY_EVERY==0:
            # Legacy scanner results have no job, so 0118 cannot date them.
            legacy_report,legacy_finding,legacy_trace=(identity(kind,ordinal)
                for kind in ('legacy-report','legacy-finding','legacy-trace'))
            rows['Report'].append(dict(live,id=legacy_report,organization_id=organization,workspace_id=workspace,
                project_id=project,source='legacy_scan',test_execution_id=None,workload_type='trace',
                trace_id=legacy_trace,source_record_id=identity('legacy-record',ordinal),source_version=None,
                recorded_at=recorded,is_current=False,error_message=None,turn_count=None,has_issues=True,
                grouping_gate=None,job_id=None,attempt_id=None,idempotency_key=None,result_digest=None,
                contract_version=None,evidence_digest=None,execution_status='completed',outcome=None,
                coverage_scope=None,observed_span_count=None,observed_call_count=None,read_complete=None,
                future_arrivals_known=None,model_calls=None,input_tokens=None,output_tokens=None,cost_usd=None,
                cost_status=None,grouping_status='completed'))
            rows['Finding'].append(dict(live,id=legacy_finding,report_id=legacy_report,finding_id='f0',ordinal=0,
                kind='tool_failure',statement='Legacy scan: order lookup tool failed for order '+str(10000+ordinal)+'.',
                recovery=None,source_finding_id=None,category='Tool Failures',group_label='Order lookup failures',
                fix_layer='Tools',confidence='M',requirement_id=None,cluster_id=group))
            rows['Member'].append(dict(live,id=identity('legacy-member',ordinal),trace_id=legacy_trace,span_id=None,
                trace_session_id=None,cluster_id=group,scan_issue_id=None,finding_id=legacy_finding,
                eval_logger_id=None))
    return rows
"""

COMMON = """
from django.apps import apps
from django.db import connection, transaction, models
from django.db.models import Q, F, Exists, OuterRef
Job=apps.get_model('tracer','TraceInvestigationJob')
Attempt=apps.get_model('tracer','TraceInvestigationAttempt')
Report=apps.get_model('tracer','TraceInvestigationReport')
Finding=apps.get_model('tracer','TraceInvestigationFinding')
Group=apps.get_model('tracer','TraceErrorGroup')
Member=apps.get_model('tracer','ErrorClusterTraces')
Project=apps.get_model('tracer','Project')
User=apps.get_model('accounts','User')
Workspace=apps.get_model('accounts','Workspace')
""" + ROWS + """
def models_in_order():
    # Insertion order follows the foreign keys.
    return (('Group',Group),('Job',Job),('Attempt',Attempt),('Report',Report),('Finding',Finding),('Member',Member))
def subsets():
    # The dedicated project is written only by this fixture.
    project=identity('project',0)
    return dict(groups=Group.all_objects.filter(project_id=project,cluster_id__startswith='QI'),
        jobs=Job.all_objects.filter(project_id=project),attempts=Attempt.all_objects.filter(job__project_id=project),
        reports=Report.all_objects.filter(project_id=project),findings=Finding.all_objects.filter(report__project_id=project),
        members=Member.all_objects.filter(cluster__project_id=project))
def matching(model,rows):
    # Every concrete source column, including FKs and soft-delete state. Never
    # accept a replay with edited payloads or substituted relations.
    if not rows: return
    fields=[f.attname for f in model._meta.concrete_fields]
    assert all(set(row)==set(fields) for row in rows), 'investigation_reference: payload inventory drift '+model.__name__
    expected={row['id']:tuple(row[key] for key in fields) for row in rows}
    position=fields.index('id')
    actual={row[position]:row for row in model.all_objects.filter(pk__in=list(expected)).values_list(*fields)}
    assert actual==expected, 'investigation_reference: incompatible existing '+model.__name__
def check_context(context):
    assert context['project_id']==str(identity('project',0)), 'investigation_reference: project identity changed'
    row=project_payload(context)
    fields=list(row)
    actual=list(Project.all_objects.filter(pk=row['id']).values_list(*fields))
    assert actual==[tuple(row[key] for key in fields)], 'investigation_reference: incompatible existing Project'
"""

# One UUID-range share of a table per call. Together the buckets read every row
# exactly once, including rows no payload predicts. Fixture IDs are uuid5, so one
# bucket holds about one scale block; BUCKET_ROWS is the enforced bound, which
# also covers rows that existed before the fixture or a skewed key range.
BUCKET_ROWS = 1000000
BUCKET = '\nVERIFY_JOBS = ' + repr(VERIFY_JOBS) + '\nBUCKET_ROWS = ' + repr(BUCKET_ROWS) + '\n' + """
def key_range(queryset,low,high):
    queryset=queryset.filter(pk__gte=uuid.UUID(int=low))
    return queryset if high==2**128 else queryset.filter(pk__lt=uuid.UUID(int=high))
def bucket(queryset):
    count,index=a['buckets'],a['bucket']
    assert type(count) is int and count==a['scale'] and type(index) is int and 0<=index<count, 'investigation_reference: invalid bucket'
    low,high=index*2**128//count,(index+1)*2**128//count
    # Probe the whole table's share of the range, not only the filtered rows: a
    # filtered count can still scan the full range. The probe stops at
    # BUCKET_ROWS+1 rows, so it and every count on this range are bounded.
    probe=key_range(queryset.model._base_manager.order_by(),low,high).values('pk')[:BUCKET_ROWS+1].count()
    assert probe<=BUCKET_ROWS, 'investigation_reference: bucket over BUCKET_ROWS rows'
    return key_range(queryset,low,high)
def verify_slice(start,stop):
    assert type(a['scale']) is int and a['scale']>0, 'investigation_reference: invalid scale'
    assert type(start) is int and 0<=start<stop<=100000*a['scale'], 'investigation_reference: invalid slice'
    assert start%VERIFY_JOBS==0 and stop==start+VERIFY_JOBS, 'investigation_reference: unbounded/misaligned slice'
    return range(start,stop,2500)
"""

SCHEMA_CHECK = ('\nSOURCE_FIELDS = ' + repr(SOURCE_FIELDS) + '\nSOURCE_CONSTRAINTS = ' + repr(SOURCE_CONSTRAINTS)
    + '\nTARGET_ONLY_MEMBER_COLUMNS = ' + repr(TARGET_ONLY_MEMBER_COLUMNS) + '\n') + """
def single_column_unique(table,column):
    # Physical, valid, unconditional and on exactly this one key column. Django
    # metadata and get_constraints() cannot show a partial-index predicate.
    with connection.cursor() as cursor:
        cursor.execute('SELECT i.indisunique, i.indisvalid, pg_get_expr(i.indpred, i.indrelid), '
            'pg_get_expr(i.indexprs, i.indrelid), ARRAY(SELECT a.attname::text FROM unnest(i.indkey::int2[]) '
            'WITH ORDINALITY AS k(attnum, n) JOIN pg_attribute a ON a.attrelid = i.indrelid '
            'AND a.attnum = k.attnum WHERE k.n <= i.indnkeyatts ORDER BY k.n) '
            'FROM pg_index i WHERE i.indrelid = to_regclass(%s)',[table])
        rows=cursor.fetchall()
    return any(unique is True and valid is True and predicate is None and expression is None and list(columns)==[column]
        for unique,valid,predicate,expression,columns in rows)
# Fail closed on source-model drift before inserting any supporting context.
assert connection.vendor=='postgresql', 'investigation_reference: PostgreSQL required'
expected_tables=dict(Job='tracer_trace_investigation_job',Attempt='tracer_trace_investigation_attempt',
    Report='tracer_trace_investigation_report',Finding='tracer_trace_investigation_finding',
    Group='tracer_trace_error_group',Member='tracer_error_cluster_traces')
for name,model in models_in_order():
    assert model._meta.managed and model._meta.db_table==expected_tables[name], 'investigation_reference: table/managed drift'
    assert {f.name for f in model._meta.concrete_fields}==set(SOURCE_FIELDS[name].split()), 'investigation_reference: field inventory drift '+name
    assert model._meta.pk.name=='id' and model._meta.pk.get_internal_type()=='UUIDField', 'investigation_reference: UUID pk required'
    assert {c.name for c in model._meta.constraints}==set(SOURCE_CONSTRAINTS[name]), 'investigation_reference: constraint drift '+name
    for field,kind in [('deleted','BooleanField'),('deleted_at','DateTimeField'),('created_at','DateTimeField'),('updated_at','DateTimeField')]:
        assert model._meta.get_field(field).get_internal_type()==kind, 'investigation_reference: history field drift'
    with connection.cursor() as cursor:
        columns={column.name for column in connection.introspection.get_table_description(cursor,model._meta.db_table)}
        actual=connection.introspection.get_constraints(cursor,model._meta.db_table)
    assert {f.column for f in model._meta.concrete_fields}<=columns, 'investigation_reference: missing physical column '+name
    # 0117 adds these; a source table that already has them is not the source schema.
    assert name!='Member' or not columns&set(TARGET_ONLY_MEMBER_COLUMNS), 'investigation_reference: target-only physical column '+name
    for constraint,kind in SOURCE_CONSTRAINTS[name].items():
        assert actual.get(constraint,{}).get(kind), 'investigation_reference: missing physical constraint '+constraint
assert {tuple(fields) for fields in Member._meta.unique_together}=={('cluster','trace','span'),('cluster','trace_session')}, 'investigation_reference: membership uniqueness drift'
# The relations 0118 joins, and the ones this fixture depends on, are real FKs.
for model,name,target in [(Member,'finding',Finding),(Member,'cluster',Group),(Finding,'report',Report),
        (Finding,'cluster',Group),(Report,'job',Job),(Report,'attempt',Attempt),(Attempt,'job',Job),
        (Job,'project',Project),(Report,'project',Project),(Group,'project',Project),(Job,'current_report',Report)]:
    field=model._meta.get_field(name)
    assert (field.many_to_one or field.one_to_one) and field.db_constraint and field.remote_field.model==target, 'investigation_reference: relation drift '+name
    with connection.cursor() as cursor:
        actual=connection.introspection.get_constraints(cursor,model._meta.db_table)
    assert any(c.get('foreign_key')==(target._meta.db_table,field.target_field.column) and c['columns']==[field.column]
        for c in actual.values()), 'investigation_reference: physical FK drift '+name
for model,name in [(Member,'trace'),(Member,'span'),(Member,'trace_session')]:
    field=model._meta.get_field(name)
    assert field.many_to_one and field.null and field.db_constraint is False, 'investigation_reference: logical telemetry FK drift'
assert Report._meta.get_field('attempt').one_to_one, 'investigation_reference: attempt relation drift'
# One report per attempt must hold in the database, not only in model metadata:
# the physical FK alone would let two reports share one attempt.
assert single_column_unique(Report._meta.db_table,Report._meta.get_field('attempt').column), 'investigation_reference: attempt uniqueness drift'
assert Group._meta.get_field('cluster_id').max_length==20, 'investigation_reference: cluster code drift'
for model,name,choices in [(Job,'state',{'completed'}),(Job,'workload_type',{'trace'}),(Attempt,'status',{'completed'}),
        (Report,'source',{'omega','legacy_scan'}),(Report,'workload_type',{'trace'}),(Report,'grouping_status',{'completed'}),
        (Group,'source',{'scanner'}),(Group,'status',{'escalating'}),(Group,'target_type',{'error_feed'}),
        (Group,'priority',{'medium'})]:
    assert choices <= {value for value,label in model._meta.get_field(name).flatchoices}, 'investigation_reference: choice drift '+name
"""

PREPARE_BODY = """
user=User.objects.get(email=a['email'],organization_id=a['organization_id'],is_active=True)
workspace=Workspace.all_objects.get(id=a['workspace_id'],organization_id=a['organization_id'],deleted=False,is_active=True)
assert workspace.created_by_id==user.id, 'investigation_reference: source account/workspace mismatch'
context=dict(user_id=str(user.id),project_id=str(identity('project',0)),
    historical_at=(workspace.created_at-timedelta(days=7)).isoformat())
# Before any write: every cohort's payload supplies each source column, is NULL
# only where the source column allows it, and fits the source lengths.
with connection.cursor() as cursor:
    nullable={model:{c.name:c.null_ok for c in connection.introspection.get_table_description(cursor,model._meta.db_table)}
        for name,model in models_in_order()}
for start in (0,69000,70000,95000):
    sample=payloads(context,start,start+GROUP_SIZE)
    for name,model in models_in_order():
        fields={f.attname:f for f in model._meta.concrete_fields}
        for row in sample[name]:
            assert set(row)==set(fields), 'investigation_reference: payload inventory drift '+name
            for key,value in row.items():
                field=fields[key]
                if value is None:
                    assert field.null and nullable[model][field.column], 'investigation_reference: non-null source column '+name+'.'+key
                elif isinstance(value,str) and field.max_length is not None:
                    assert len(value)<=field.max_length, 'investigation_reference: source length '+name+'.'+key
row=project_payload(context)
with transaction.atomic():
    existing=Project.all_objects.filter(pk=row['id']).count()
    if not existing:
        Project.all_objects.bulk_create([Project(**row)],batch_size=100)
        Project.all_objects.filter(pk=row['id']).update(created_at=row['created_at'],updated_at=row['updated_at'])
    check_context(context)
print(json.dumps(dict(context=context)))
"""

CHUNK_BODY = """
context=a['context']
start,stop,scale=a['start'],a['stop'],a['scale']
assert type(scale) is int and scale>0 and 0<=start<stop<=100000*scale, 'investigation_reference: invalid slice'
assert start%2500==0 and stop==start+2500, 'investigation_reference: unbounded/misaligned slice'
check_context(context)
when=datetime.fromisoformat(context['historical_at'])
rows=payloads(context,start,stop)
with transaction.atomic():
    counts=[model.all_objects.filter(pk__in=[row['id'] for row in rows[name]]).count() for name,model in models_in_order()]
    fresh=all(count==0 for count in counts)
    assert fresh or counts==[len(rows[name]) for name,model in models_in_order()], 'investigation_reference: partial atomic chunk'
    if fresh:
        for name,model in models_in_order():
            if not rows[name]: continue
            # Jobs precede their reports, so current_report starts NULL; it is
            # set below once the reports exist, without relying on deferred FKs.
            model.all_objects.bulk_create([model(**(dict(row,current_report_id=None) if name=='Job' else row))
                for row in rows[name]],batch_size=500)
            # auto_now(_add) applies during bulk_create; restore fixture history.
            model.all_objects.filter(pk__in=[row['id'] for row in rows[name]]).update(created_at=when,updated_at=when)
        Job.all_objects.bulk_update([Job(id=row['id'],current_report_id=row['current_report_id']) for row in rows['Job']],
            ['current_report_id'],batch_size=500)
    # Replays compare current_report_id too, like every other source column.
    for name,model in models_in_order():
        matching(model,rows[name])
print(json.dumps(dict(skipped=not fresh)))
"""

VERIFY_SLICE_BODY = """
context=a['context']
check_context(context)
# Re-read every expected value, 2,500 jobs at a time. Counts alone cannot
# detect replay with edited payloads, substituted IDs or a broken chain.
for start in verify_slice(a['start'],a['stop']):
    rows=payloads(context,start,start+2500)
    for name,model in models_in_order():
        matching(model,rows[name])
print(json.dumps(dict(verified=a['stop']-a['start'])))
"""

# The same PK range is used on both sides of the baseline subtraction,
# including when part of the fixture already exists after an interruption.
SOURCE_BUCKET = COMMON + BUCKET + """
check_context(a['context'])
scoped={key:bucket(queryset) for key,queryset in subsets().items()}
tables=((Finding,'findings'),(Job,'jobs'),(Report,'reports'))
counts={model._meta.db_table:bucket(model.all_objects).count() for model,key in tables}
seeded={model._meta.db_table:scoped[key].count() for model,key in tables}
"""

COHORTS_BODY = """
members=scoped['members']
# 0118's join predicate, scoped to the fixture. occurred_at does not exist on the
# source schema, so every membership matching it is pending for the backfill.
pending=members.filter(finding__report__job__root_end_time__isnull=False)
cohorts=dict(groups=scoped['groups'].count(),attempts=scoped['attempts'].count(),
    legacy_reports=scoped['reports'].filter(source='legacy_scan').count(),
    no_root_end_jobs=scoped['jobs'].filter(root_end_time__isnull=True).count(),
    memberships=members.count(),backfill_memberships=pending.count(),
    deleted_backfill_memberships=pending.filter(deleted=True).count(),
    no_root_end_memberships=members.filter(finding__report__job__isnull=False,
        finding__report__job__root_end_time__isnull=True).count(),
    legacy_memberships=members.filter(finding__isnull=False,finding__report__job__isnull=True).count(),
    chain_consistent_memberships=members.filter(trace_id=F('finding__report__trace_id')).count(),
    findings_without_membership=scoped['findings'].filter(
        ~Exists(Member.all_objects.filter(finding_id=OuterRef('pk')))).count())
print(json.dumps(dict(counts=counts,seeded_counts=seeded,cohorts=cohorts)))
"""

# Target programs read the migrated schema, whose models gain columns, so they
# never use the source inventory checks. They resolve only the membership model
# and follow 0118's own relation path from it.
TARGET_COMMON = """
from django.apps import apps
from django.db import connection
from django.db.models import F
Member=apps.get_model('tracer','ErrorClusterTraces')
""" + ROWS + BUCKET + '\nTARGET_ONLY_MEMBER_COLUMNS = ' + repr(TARGET_ONLY_MEMBER_COLUMNS) + '\n' + """
assert connection.vendor=='postgresql', 'investigation_reference: PostgreSQL required'
context=a['context']
assert context['project_id']==str(identity('project',0)), 'investigation_reference: project identity changed'
with connection.cursor() as cursor:
    columns={column.name for column in connection.introspection.get_table_description(cursor,Member._meta.db_table)}
assert {Member._meta.get_field(name).column for name in TARGET_ONLY_MEMBER_COLUMNS}<=columns, 'investigation_reference: target occurrence columns missing'
"""

TARGET_SLICE_BODY = """
checked=mismatched=0
for start in verify_slice(a['start'],a['stop']):
    expected=occurrences(payloads(context,start,start+2500))
    actual={row[0]:row[1:] for row in Member.all_objects.filter(
        cluster__project_id=identity('project',0),pk__in=list(expected)).values_list(
        'id','occurred_at','occurrence_time_source','finding__report__job__root_end_time')}
    checked+=len(expected)
    # A missing membership, a wrong time or source, or a changed job all count.
    mismatched+=sum(1 for key,value in expected.items() if actual.get(key)!=value)
print(json.dumps(dict(checked=checked,mismatched=mismatched)))
"""

TARGET_COHORTS_BODY = """
members=bucket(Member.all_objects.filter(cluster__project_id=identity('project',0)))
filled=members.filter(occurrence_time_source='omega_root_end',occurred_at__isnull=False,
    occurred_at=F('finding__report__job__root_end_time'))
empty=members.filter(occurred_at__isnull=True,occurrence_time_source='')
cohorts=dict(memberships=members.count(),filled_memberships=filled.count(),
    filled_deleted_memberships=filled.filter(deleted=True).count(),null_memberships=empty.count(),
    null_no_root_end_memberships=empty.filter(finding__report__job__isnull=False,
        finding__report__job__root_end_time__isnull=True).count(),
    null_legacy_memberships=empty.filter(finding__isnull=False,finding__report__job__isnull=True).count())
print(json.dumps(dict(cohorts=cohorts)))
"""

PREPARE = COMMON + SCHEMA_CHECK + PREPARE_BODY
CHUNK = COMMON + CHUNK_BODY
VERIFY_SLICE = COMMON + BUCKET + VERIFY_SLICE_BODY
BASELINE_PROGRAM = SOURCE_BUCKET + 'print(json.dumps(dict(counts=counts,seeded_counts=seeded)))\n'
COHORTS_PROGRAM = SOURCE_BUCKET + COHORTS_BODY
TARGET_SLICE = TARGET_COMMON + TARGET_SLICE_BODY
TARGET_COHORTS = TARGET_COMMON + TARGET_COHORTS_BODY


def _requirement(producer):
    def require(ok):
        if not ok:
            raise producer.ManualRelease('high_volume_reference_unavailable')
    return require


def _timed(driver, seconds, program, arguments):
    began = time.monotonic()
    try:
        return driver.app_python(program, arguments)
    finally:
        seconds.append(time.monotonic() - began)


def _bucket_totals(driver, seconds, program, arguments, groups, require):
    """Sum each named count across the disjoint UUID ranges on the host."""
    totals = {name: dict.fromkeys(keys, 0) for name, keys in groups.items()}
    for index in range(arguments['scale']):
        result = _timed(driver, seconds, program, dict(arguments, bucket=index, buckets=arguments['scale']))
        require(isinstance(result, dict) and set(result) == set(groups))
        for name, counts in totals.items():
            part = result[name]
            require(isinstance(part, dict) and set(part) == set(counts))
            for key, value in part.items():
                require(type(value) is int and value >= 0)
                counts[key] += value
    return totals


def _verify_source(driver, state, plan, scale, *, prefix, require):
    """Bounded read-only source verification; returns (counts, cohorts, seconds)."""
    arguments = dict(state['arguments'], context=state['context'], scale=scale)
    seconds = []
    for start in range(0, 100000 * scale, VERIFY_JOBS):
        result = _timed(driver, seconds, prefix + VERIFY_SLICE, dict(arguments, start=start, stop=start + VERIFY_JOBS))
        require(isinstance(result, dict) and set(result) == {'verified'} and type(result['verified']) is int
            and result['verified'] == VERIFY_JOBS)
    totals = _bucket_totals(driver, seconds, prefix + COHORTS_PROGRAM, arguments,
        dict(plan, seeded_counts=plan['counts']), require)
    counts = totals['counts']
    require(totals['seeded_counts'] == plan['counts'] and totals['cohorts'] == plan['cohorts'])
    # Fixture-scoped counts above are exact. Physical totals only need to include
    # the baseline plus the whole fixture; each table's reference is its
    # independent measurement, not this self-report.
    for table, added in plan['counts'].items():
        require(type(counts[table]) is int and counts[table] >= state['arguments']['rows']
            and counts[table] >= state['baseline'][table] + added)
    return counts, totals['cohorts'], seconds


def verify_investigation_backfill(driver, *, prefix, producer):
    """Read-only target check of what 0118 wrote to the fixture memberships.

    Qualification calls this in migration_forward, after the planned migrations
    ran on the target. Unavailable or malformed state refuses as a manual
    release; a wrong migrated value fails the assertion.
    """
    require = _requirement(producer)
    tables = set(INVESTIGATION_REFERENCE_MODELS.values())
    selected = any(isinstance(row, dict) and row.get('database') == 'postgres' and row.get('table') in tables
        for row in getattr(driver, 'reference_registry', None) or ())
    require(getattr(driver, '_investigation_reference_pending', None) is None)
    state = getattr(driver, '_investigation_reference', None)
    if state is None:
        # Nothing to check only when the family was never in scope.
        require(not selected)
        return
    rows = state['arguments']['rows']
    require(type(rows) is int and 100000 <= rows <= 100000000)
    scale = (rows + 99999) // 100000
    plan = investigation_target_plan(scale)
    arguments = dict(state['arguments'], context=state['context'], scale=scale)
    seconds, checked, mismatched = [], 0, 0
    for start in range(0, 100000 * scale, VERIFY_JOBS):
        result = _timed(driver, seconds, prefix + TARGET_SLICE, dict(arguments, start=start, stop=start + VERIFY_JOBS))
        require(isinstance(result, dict) and set(result) == {'checked', 'mismatched'}
            and all(type(result[key]) is int and result[key] >= 0 for key in result))
        checked += result['checked']
        mismatched += result['mismatched']
    require(checked == plan['memberships'])
    totals = _bucket_totals(driver, seconds, prefix + TARGET_COHORTS, arguments, dict(cohorts=plan), require)['cohorts']
    if getattr(driver, 'corpus', None) is not None:
        driver.corpus['investigation_reference_target_check'] = dict(scale=scale, slices=100000 * scale // VERIFY_JOBS,
            buckets=scale, checked=checked, mismatched=mismatched, cohorts=dict(totals), seconds=seconds)
    producer.need(mismatched == 0, 'investigation backfill differs from the payload on target')
    producer.equal(totals, plan, 'investigation backfill cohorts differ on target')


def seed_investigation_reference_family(driver, rows, *, prefix, producer):
    """Complete all three source tables before any independent per-table measurement."""
    require = _requirement(producer)
    require(type(rows) is int and 100000 <= rows <= 100000000)
    scale = (rows + 99999) // 100000
    plan = investigation_reference_plan(scale)
    namespaces = getattr(getattr(driver, 'witnesses', None), 'namespaces', None)
    require(isinstance(namespaces, dict))
    source = namespaces.get('source')
    require(isinstance(source, dict))
    headers, account = source.get('headers'), source.get('account')
    require(isinstance(headers, dict) and isinstance(account, dict))
    arguments = dict(namespace=source.get('namespace'), email=account.get('email'),
        organization_id=headers.get('X-Organization-Id'), workspace_id=headers.get('X-Workspace-Id'))
    require(all(isinstance(value, str) and bool(value) for value in arguments.values()))
    # No bearer token/password/API key leaves the witness corpus.
    arguments['rows'] = rows
    complete = getattr(driver, '_investigation_reference', None)
    state = complete or getattr(driver, '_investigation_reference_pending', None)
    if state is not None:
        require(state['arguments'] == arguments)
    else:
        prepared = driver.app_python(prefix + PREPARE, arguments)
        require(isinstance(prepared, dict) and isinstance(prepared.get('context'), dict))
        initial = _bucket_totals(driver, [], prefix + BASELINE_PROGRAM,
            dict(arguments, context=prepared['context'], scale=scale),
            dict(counts=plan['counts'], seeded_counts=plan['counts']), require)
        before, seeded = initial['counts'], initial['seeded_counts']
        for table, expected in plan['counts'].items():
            require(seeded[table] <= expected and before[table] >= seeded[table])
        state = dict(arguments=arguments, context=prepared['context'],
            baseline={table: before[table] - seeded[table] for table in plan['counts']})
        driver._investigation_reference_pending = state
    if complete is None:
        chunk_seconds = []
        for start in range(0, plan['counts']['tracer_trace_investigation_job'], CHUNK_JOBS):
            began = time.monotonic()
            result = driver.app_python(prefix + CHUNK, dict(arguments, context=state['context'],
                start=start, stop=start + CHUNK_JOBS, scale=scale))
            chunk_seconds.append(time.monotonic() - began)
            require(isinstance(result, dict) and type(result.get('skipped')) is bool)
    # Every later table callback repeats the same full bounded verification.
    counts, cohorts, verify_seconds = _verify_source(driver, state, plan, scale, prefix=prefix, require=require)
    if complete is not None:
        return
    if getattr(driver, 'corpus', None) is not None:
        driver.corpus.setdefault('reference_seed_operations', []).append(dict(
            table='tracer_trace_investigation_family', operation='investigation_reference_fixture', version=1,
            requested_rows=rows, scale=scale, chunks=len(chunk_seconds), chunk_seconds=chunk_seconds,
            verify_calls=len(verify_seconds), verify_seconds=verify_seconds, counts=dict(counts), cohorts=dict(cohorts)))
    driver._investigation_reference = dict(state, counts=dict(counts), cohorts=dict(cohorts))
    del driver._investigation_reference_pending
