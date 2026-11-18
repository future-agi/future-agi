"""Combined-source PostgreSQL seams; synthetic storage/remote IO, real ORM/services.

Not native-storage, complete sandbox admission, or release approval. Existing
assertions are untouched. Whole/single publication bypass only external source
recheck (and single decoder result), explicitly distinguishing transaction proof
from end-to-end decoder qualification, which is executed separately.
"""
import base64
from datetime import timedelta
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest
from django.db import connection, transaction, IntegrityError
from django.db.migrations.recorder import MigrationRecorder
from django.utils import timezone

from tracer.models.attachment import Attachment, AttachmentScope, CaptureSession, AttachmentOccurrence, AttachmentUpload, AttachmentAuditEvent
from tracer.models.attachment_recording import AttachmentImportJob, AttachmentRecording, AttachmentRecordingSegment, AttachmentSourceEnvelope, AttachmentRecordingDerivative, AttachmentProviderConnection, AttachmentProviderRecording
from tracer.models.attachment_storage import AttachmentStorageBinding, binding_identity_sha256
from tracer.services.attachments import capacity, capacity_db, imports, publication, retention, derivatives, recordings, envelopes, source_providers, accounting, validation
from tracer.services.attachments.contract import AttachmentError
from tracer.services.attachments.interfaces import ObjectIdentity, ResourceVector, StoredObject, VerifiedAssetFacts, ReservationOwner, EraseResult, VersionPage, SourceRef
from tracer.tests import test_attachment_simplify_review_imports_db as original_sql

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.integration]


@pytest.fixture
def values(project, user, settings, tmp_path, request):
    assert connection.vendor == 'postgresql'
    assert connection.settings_dict['NAME'].startswith('test_att_combined_review_')
    assert settings.CLICKHOUSE['CH_DATABASE'] == connection.settings_dict['NAME']
    settings.FI_ATTACHMENTS_ENABLED = True
    settings.FI_ATTACHMENTS_WORK_DIR = str(tmp_path)
    settings.FI_ATTACHMENTS_ENVELOPE_KEYS = 'fixture:' + base64.urlsafe_b64encode(b'X' * 32).decode().rstrip('=')
    settings.FI_ATTACHMENTS_IMPORT_HOSTS = ('storage.invalid',)
    now = timezone.now()
    identity = dict(provider='s3', endpoint='https://storage.invalid', account_identity='synthetic', region='fixture', quarantine_bucket='combined-quarantine', serving_bucket='combined-serving', quarantine_resource_identity='fixture:quarantine', serving_resource_identity='fixture:serving', quarantine_owner_identity='synthetic', serving_owner_identity='synthetic', quarantine_created_at=now, serving_created_at=now, quarantine_encryption_mode='sse-s3', serving_encryption_mode='sse-s3', quarantine_encryption_key_identity='', serving_encryption_key_identity='')
    binding = AttachmentStorageBinding.objects.create(**identity, identity_sha256=binding_identity_sha256(identity))
    scope = AttachmentScope.objects.create(project=project, workspace=project.workspace, organization=project.organization, enabled=True, allow_import=True, record_audio=True, require_media_redaction=getattr(request,'param',{}).get('redaction',False))
    session = CaptureSession.objects.create(scope=scope, client_id=uuid4(), writer_kind='user', writer_id=user.id, correlation_nonce=uuid4(), policy_version=scope.policy_version, deletion_generation=scope.deletion_generation, expires_at=now+timedelta(hours=1), request_fingerprint='0'*64, capture_policy={'enabled':True, 'allow_import':True, 'record_audio':True})
    trace, span = uuid4(), '1234567890abcdef'
    eid = envelopes.seal(scope=scope, url='https://storage.invalid/combined.wav', creator_kind='user', creator_id=user.id, direction='mixed', source_bytes=46, source_expires_at=now+timedelta(minutes=5), generation_kind='http_strong_etag', generation='"fixture-v1"', mime_type='audio/wav', duration_ms=1, trace_id=trace, span_id=span, capture_session_id=session.id)
    envelope = AttachmentSourceEnvelope.objects.get(pk=eid)
    return NS(scope=scope, session=session, binding=binding, user=user, envelope=envelope, trace=trace, span=span, tmp_path=tmp_path)


def make_job(v, *, upload=None, ttl=None):
    now=timezone.now()
    recording = None if upload else AttachmentRecording.objects.create(scope=v.scope, client_id=uuid4(), capture_session=v.session, trace_id=v.trace, span_id=v.span, capture_session_client_id=v.session.client_id, correlation_nonce=v.session.correlation_nonce, direction='mixed', state='importing', policy_version=v.scope.policy_version, deletion_generation=v.scope.deletion_generation)
    jid=uuid4(); deadline=min(v.envelope.expires_at,now+timedelta(seconds=ttl)) if ttl else v.envelope.expires_at; amount=ResourceVector(100,1,1,1,2,0)
    job=AttachmentImportJob.objects.create(id=jid, scope=v.scope, capture_session=v.session, recording=recording, upload=upload, storage_binding=v.binding, importer_kind='user', importer_id=v.user.id, request_fingerprint='1'*64, source_kind='RemoteUrlEnvelope', envelope_id=v.envelope.id, mime_type='audio/wav', declared_bytes=v.envelope.source_bytes, declared_duration_ms=v.envelope.duration_ms, window_bytes=100, max_segment_ms=30000, recording_bytes=100000, deadline=deadline, policy_version=v.scope.policy_version, deletion_generation=v.scope.deletion_generation, scratch_id=jid, cleanup_not_before=deadline+timedelta(seconds=15), source_generation_kind=v.envelope.generation_kind, source_generation=v.envelope.generation, source_byte_length=v.envelope.source_bytes, source_mime_type='audio/wav', source_duration_ms=v.envelope.duration_ms, source_expires_at=v.envelope.expires_at, source_authority_generation=0, source_creator_kind='user', source_creator_id=v.user.id, source_trace_id=v.trace, source_span_id=v.span, source_direction='mixed', **imports._reservation(now,deadline,amount,source_bytes=v.envelope.source_bytes,window=100))
    with transaction.atomic():
        locks=capacity_db.lock_capacity(scope_id=v.scope.id,pool_keys=(capacity.IMPORT_POOL_KEY,))
        capacity_db.reserve_locked(locks,owner=imports._owner(job),amount=amount,expires_at=deadline)
    job.refresh_from_db()
    return job


def claim(v, job):
    fence=imports.claim(job.id,scope_id=v.scope.id,now=timezone.now())
    assert fence is not None
    job.refresh_from_db()
    # claim creates the recovering epoch; writer phase is the actual worker's
    # transition after cleanup. No DB/source permission is bypassed here.
    job.lease_phase='writing'; job.save(update_fields=['lease_phase'])
    return fence


def staged(v,job,fence,version='combined-v1'):
    recording=job.recording; recording.sample_rate=8000; recording.channels=1
    recording.save(update_fields=['sample_rate','channels'])
    sid=uuid4()
    row=AttachmentRecordingSegment.objects.create(id=sid,scope=v.scope,recording=recording,import_job=job,storage_binding=v.binding,storage_object_id=sid,storage_key=f'original/{sid}',quarantine_key=f'quarantine/{sid}',index=0,attachment_client_id=uuid4(),occurrence_client_id=uuid4(),start_frame=0,frame_count=1,start_ms=0,duration_ms=1,byte_length=46,sha256='a'*64,allocation_epoch=fence.epoch,cleanup_not_before=job.cleanup_not_before)
    row.write_attempts=1;row.write_deadline_at=timezone.now()+timedelta(seconds=30);row.save(update_fields=['write_attempts','write_deadline_at'])
    row.storage_version=version;row.save(update_fields=['storage_version'])
    return row


def manifest(job,row):
    plan=recordings.plan_segments(frame_count=1,rate=8000,channels=1,limits=recordings.Limits(100,100,30000,100000,7200000,1024**3))[0]
    return recordings.build_manifest(recording_id=job.recording.client_id,rate=8000,channels=1,direction='mixed',track_ids=[uuid4()],segments=[(row.occurrence_client_id,plan)],duration_ms=1,capture_complete=True)


def external_recheck_only(monkeypatch):
    calls=[]
    def check(job,fence):
        assert not connection.in_atomic_block
        calls.append((job.id,fence.epoch))
    monkeypatch.setattr(imports,'_remote_recheck',check)
    return calls


def make_upload(v, *, imported=False, short=False, ttl=None):
    now=timezone.now();uid=uuid4();expiry=now+timedelta(seconds=ttl or (1 if short else 240))
    asset=Attachment.objects.create(scope=v.scope,client_id=uuid4(),storage_binding=v.binding,source_kind='imported_recording' if imported else 'Bytes',mime_type='audio/wav',policy_version=v.scope.policy_version,deletion_generation=v.scope.deletion_generation)
    occurrence=AttachmentOccurrence.objects.create(scope=v.scope,attachment=asset,capture_session=v.session,client_id=uuid4(),trace_id=v.trace,span_id=v.span,direction='mixed',expires_at=now+timedelta(hours=24),policy_version=v.scope.policy_version,deletion_generation=v.scope.deletion_generation)
    pool=capacity.IMPORT_POOL_KEY if imported else capacity.upload_pool_key(uid)
    upload=AttachmentUpload.objects.create(id=uid,scope=v.scope,attachment=asset,occurrence=occurrence,capture_session=v.session,idempotency_key=str(uid),request_fingerprint='2'*64,expected_bytes=46,mime_type='audio/wav',expires_at=expiry,producer_kind='import' if imported else 'authenticated_stream',storage_object_id=uid,quarantine_key=f'quarantine/{uid}',serving_key=f'original/{uid}',cleanup_not_before=expiry+timedelta(seconds=15),admission_event_id=uuid4(),policy_version=v.scope.policy_version,deletion_generation=v.scope.deletion_generation,**capacity_db.reservation_fields(pool_key=pool,expires_at=expiry,reserved_bytes=46,decoded_bytes=100,object_count=4,part_count=1,now=now))
    with transaction.atomic():
        locks=capacity_db.lock_capacity(scope_id=v.scope.id,pool_keys=(pool,))
        capacity_db.reserve_locked(locks,owner=ReservationOwner('upload',uid,v.scope.id),amount=capacity.ordinary_upload_peak(expected_bytes=46,decoder_scratch_bytes=100),expires_at=expiry)
        accounting.admit_locked(upload)
    return asset,occurrence,upload


class SyntheticStorage:
    """Typed IO boundary, not a model of authorization/accounting."""
    def __init__(self,binding_id,versions=()):
        self.binding_id=binding_id;self.versions=list(versions);self.erased=[];self.partial=False
    def verify_identity(self,*,operation):
        assert operation=='erase'
    def erase_all_versions(self,locator):
        assert not connection.in_atomic_block
        self.erased.append(locator)
        return EraseResult(not self.partial,self.partial,'pending' if self.partial else None,timezone.now())
    def list_versions(self,locator,*,cursor=None,limit=100):
        assert not connection.in_atomic_block
        return VersionPage(tuple(x for x in self.versions if x.key==locator.key),(),None)
    def erase_exact(self,identity):
        assert not connection.in_atomic_block
        self.erased.append(identity)
        self.versions=[x for x in self.versions if x!=identity]


def test_full_graph_has_corrected_migrations_and_enabled_fences(values):
    applied=MigrationRecorder(connection).applied_migrations()
    assert ('tracer','0143_attachment_value_contraction') in applied
    with connection.cursor() as c:
        c.execute("SELECT tgname,tgenabled FROM pg_trigger WHERE NOT tgisinternal AND tgname LIKE 'attachment%' ORDER BY tgname")
        rows=c.fetchall()
    assert rows and all(flag!='D' for _,flag in rows)
    assert any(name=='attachment_record_fence' for name,_ in rows)


@pytest.mark.parametrize('capacity_failure',[False,True])
def test_existing_takeover_assertions_on_combined_schema(values,settings,capacity_failure):
    v=values;job=make_job(v)
    original_sql.test_expired_takeover_lease_precedes_guarded_resize((v.scope,v.session,v.binding,job,v.tmp_path),settings,capacity_failure)


def test_existing_asset_state_assertions_on_combined_schema(values,settings):
    v=values;job=make_job(v)
    original_sql.test_pending_validating_ready_whole_asset_keeps_initial_sql_fence((v.scope,v.session,v.binding,job,v.tmp_path),settings)


@pytest.mark.parametrize('values',[{'redaction':True}],indirect=True)
def test_required_redaction_still_denies_import_claim(values):
    v=values
    assert v.scope.require_media_redaction is True
    job=make_job(v)
    with pytest.raises(AttachmentError,match='redaction_unavailable'):
        imports.claim(job.id,scope_id=v.scope.id,now=timezone.now())
    job.refresh_from_db();assert job.lease_epoch==0 and job.state=='importing'


def test_actual_whole_publish_keeps_pending_validating_ready_and_manifest(values,monkeypatch):
    v=values;job=make_job(v);fence=claim(v,job);row=staged(v,job,fence)
    calls=external_recheck_only(monkeypatch)
    imports.publish(job_id=job.id,fence=fence,segment_ids=(row.id,),manifest=manifest(job,row))
    job.refresh_from_db();row.refresh_from_db();asset=row.attachment
    assert job.state=='ready' and job.lease_phase=='terminal'
    assert asset.state=='ready' and asset.verified_storage_version=='combined-v1'
    assert row.state=='published' and row.allocation_epoch==fence.epoch
    assert job.recording.manifest['segments'][0]['occurrence_id']==str(row.occurrence_client_id)
    assert AttachmentOccurrence.objects.filter(attachment=asset,recording=job.recording).count()==1
    assert AttachmentRecordingDerivative.objects.filter(attachment=asset,representation='peaks').count()==1
    assert calls==[(job.id,fence.epoch)]


def test_actual_first_binding_retains_thirty_days_then_rejects_extension(values):
    v=values;asset,occurrence,upload=make_upload(v)
    source=NS(scope_id=v.scope.id,parent=NS(trace_id=v.trace,span_id=v.span),generation=4,started_at=timezone.now()-timedelta(minutes=1),expires_at=timezone.now()+timedelta(days=30))
    result=retention.bind_retention(scope_id=v.scope.id,occurrence_id=occurrence.id,source=source)
    assert result.expires_at==source.expires_at
    assert result.expires_at>timezone.now()+timedelta(days=29)
    first=result.bound_at
    assert retention.bind_retention(scope_id=v.scope.id,occurrence_id=occurrence.id,source=source).bound_at==first
    source.expires_at+=timedelta(seconds=1)
    with pytest.raises(AttachmentError,match='binding_fenced'):
        retention.bind_retention(scope_id=v.scope.id,occurrence_id=occurrence.id,source=source)
    occurrence.refresh_from_db();assert occurrence.expires_at==result.expires_at


def test_actual_failed_neverpublished_cleanup_preserves_bound_failure_metadata(values):
    v=values;asset,occurrence,upload=make_upload(v,short=True)
    source=NS(scope_id=v.scope.id,parent=NS(trace_id=v.trace,span_id=v.span),generation=4,started_at=timezone.now()-timedelta(minutes=1),expires_at=timezone.now()+timedelta(days=30))
    retention.bind_retention(scope_id=v.scope.id,occurrence_id=occurrence.id,source=source)
    from tracer.services.attachments import uploads
    uploads.settle(upload.id,scope_id=v.scope.id,outcome='failed')
    upload.refresh_from_db();receipt=(upload.usage_id,upload.usage_outcome,upload.usage_created_at)
    storage=SyntheticStorage(v.binding.id)
    assert not retention._erase_asset(attachment_id=asset.id,scope_id=v.scope.id,now=timezone.now(),storage=storage)
    wait=max(upload.cleanup_not_before,upload.expires_at+retention.TRANSFER_DRAIN)-timezone.now()
    with connection.cursor() as c:c.execute('SELECT pg_sleep(%s)',(max(0,wait.total_seconds())+0.1,))
    storage.partial=True
    assert not retention._erase_asset(attachment_id=asset.id,scope_id=v.scope.id,now=timezone.now(),storage=storage)
    upload.refresh_from_db();assert upload.reservation_settled_at is None
    storage.partial=False
    assert retention._erase_asset(attachment_id=asset.id,scope_id=v.scope.id,now=timezone.now(),storage=storage)
    assert retention._erase_asset(attachment_id=asset.id,scope_id=v.scope.id,now=timezone.now(),storage=storage)
    upload.refresh_from_db();asset.refresh_from_db();occurrence.refresh_from_db()
    assert upload.reservation_settled_at is not None
    assert (upload.usage_id,upload.usage_outcome,upload.usage_created_at)==receipt
    assert asset.state=='failed' and asset.deleted_at is None and asset.verified_blob_id is None
    assert occurrence.deleted_at is None and occurrence.expires_at==source.expires_at


@pytest.mark.parametrize('with_cleanup',[False,True])
def test_actual_single_publish_receipt_is_atomic(values,monkeypatch,with_cleanup):
    v=values;asset,occurrence,upload=make_upload(v,imported=True,ttl=10 if with_cleanup else None);job=make_job(v,upload=upload,ttl=8 if with_cleanup else None);fence=claim(v,job)
    upload.allocation_epoch=fence.epoch
    upload.quarantine_write_attempts=1;upload.serving_write_attempts=1;upload.write_deadline_at=min(timezone.now()+timedelta(seconds=30),fence.expires_at)
    upload.cleanup_not_before=max(upload.cleanup_not_before,job.cleanup_not_before)
    upload.save(update_fields=['allocation_epoch','quarantine_write_attempts','serving_write_attempts','write_deadline_at','cleanup_not_before'])
    upload.state='validating';upload.quarantine_version='q1';upload.quarantine_sha256='a'*64;upload.received_bytes=46;upload.serving_version='s1'
    upload.save(update_fields=['state','quarantine_version','quarantine_sha256','received_bytes','serving_version'])
    asset.state='validating';asset.save(update_fields=['state'])
    verified=VerifiedAssetFacts(StoredObject(ObjectIdentity(v.binding.id,'serving',upload.serving_key,'s1'),46,'a'*64),'audio/wav',timezone.now(),8000,1,1)
    external_recheck_only(monkeypatch)
    # This isolates the publication transaction; actual bubblewrap is a separate
    # mandatory test and is never claimed by this synthetic validation result.
    monkeypatch.setattr(validation,'validate_import_snapshot',lambda **kw:verified)
    actual_enqueue=derivatives.enqueue_for_asset_locked
    def crash_after_receipt(a):
        fresh=AttachmentUpload.objects.get(pk=upload.id)
        assert fresh.usage_outcome=='ready' and fresh.usage_stored_bytes==46
        raise RuntimeError('synthetic-crash-after-receipt')
    monkeypatch.setattr(derivatives,'enqueue_for_asset_locked',crash_after_receipt)
    with pytest.raises(RuntimeError,match='synthetic-crash-after-receipt'):
        imports.publish(job_id=job.id,fence=fence,segment_ids=(),manifest=None)
    upload.refresh_from_db();asset.refresh_from_db();job.refresh_from_db()
    assert upload.usage_id is None and asset.verified_blob_id is None and job.lease_phase=='writing'
    assert not AttachmentAuditEvent.objects.filter(entity_id=upload.id,event='settlement').exists()
    monkeypatch.setattr(derivatives,'enqueue_for_asset_locked',actual_enqueue)
    imports.publish(job_id=job.id,fence=fence,segment_ids=(),manifest=None)
    upload.refresh_from_db();asset.refresh_from_db();job.refresh_from_db()
    assert upload.usage_outcome=='ready' and upload.usage_stored_bytes==46 and upload.usage_id
    assert AttachmentAuditEvent.objects.filter(entity_id=upload.id,event='settlement',event_id=upload.settlement_event_id).count()==1
    assert asset.state=='ready' and job.state=='ready'
    assert upload.reservation_settled_at is None and job.reservation_settled_at is None
    if with_cleanup:
        from tracer.services.attachments import readiness
        receipt=(upload.usage_id,upload.usage_created_at,upload.usage_outcome,upload.usage_stored_bytes)
        keep=ObjectIdentity(v.binding.id,'serving',upload.serving_key,'s1')
        orphan=ObjectIdentity(v.binding.id,'serving',upload.serving_key,'unowned-v0')
        store=SyntheticStorage(v.binding.id,[keep,orphan]);store.erase_version=store.erase_exact
        monkeypatch.setattr(readiness,'resolve_historical',lambda binding_id,operation:store)
        assert not imports._cleanup(job,timezone.now())
        wait=max(upload.cleanup_not_before,job.cleanup_not_before)-timezone.now()
        with connection.cursor() as c:c.execute('SELECT pg_sleep(%s)',(max(0,wait.total_seconds())+0.1,))
        assert imports._cleanup(job,timezone.now())
        upload.refresh_from_db();job.refresh_from_db();asset.refresh_from_db();occurrence.refresh_from_db()
        assert upload.reservation_settled_at is not None and job.reservation_settled_at is not None
        assert (upload.usage_id,upload.usage_created_at,upload.usage_outcome,upload.usage_stored_bytes)==receipt
        assert store.versions==[keep] and orphan in store.erased and keep not in store.erased
        assert asset.state=='ready' and asset.verified_storage_version=='s1'
        assert occurrence.deleted_at is None and occurrence.expires_at>timezone.now()


@pytest.mark.parametrize('wire_kind',['RemoteUrlEnvelope','ProviderFile'])
def test_provider_envelope_connection_lock_blocks_revocation_and_rejects_after(values,wire_kind):
    import psycopg
    v=values;job=make_job(v)
    conn=AttachmentProviderConnection.objects.create(scope=v.scope,provider='livekit',api_key_id=str(uuid4()),key_id='fixture',secret_nonce=b'0'*12,secret_ciphertext=b'fixture',enabled=True,authority_generation=7)
    provider=AttachmentProviderRecording.objects.create(scope=v.scope,connection=conn,registered_output_id=uuid4(),egress_id=str(uuid4()),room_digest='0'*64,registrar_kind='user',registrar_id=v.user.id,recording_client_id=job.recording.client_id,trace_id=v.trace,span_id=v.span,capture_session_client_id=v.session.client_id,correlation_nonce=v.session.correlation_nonce,direction='mixed',expires_at=v.envelope.expires_at)
    provider.provider_state='complete';provider.output_bytes=46;provider.output_duration_ms=1;provider.output_envelope_id=v.envelope.id;provider.source_generation_kind=v.envelope.generation_kind;provider.source_generation=v.envelope.generation;provider.source_mime_type='audio/wav';provider.source_expires_at=v.envelope.expires_at;provider.source_authority_generation=7;provider.dispatch_due_at=timezone.now()
    provider.save(update_fields=['provider_state','output_bytes','output_duration_ms','output_envelope_id','source_generation_kind','source_generation','source_mime_type','source_expires_at','source_authority_generation','dispatch_due_at'])
    ref=SourceRef('RemoteUrlEnvelope',v.envelope.id) if wire_kind=='RemoteUrlEnvelope' else SourceRef('ProviderFile',provider.id)
    source_facts=imports._facts(job) if wire_kind=='RemoteUrlEnvelope' else source_providers._provider_facts(provider,v.session,imports.Importer('user',v.user.id))
    assert source_facts.authority_generation==(0 if wire_kind=='RemoteUrlEnvelope' else 7)
    db=connection.settings_dict
    with psycopg.connect(dbname=db['NAME'],host=db['HOST'],port=db['PORT'],user=db['USER'],password=db['PASSWORD'],autocommit=True) as other:
        with transaction.atomic():
            locked=source_providers.lock_source(scope_id=v.scope.id,source=ref)
            assert locked[0].id==conn.id and locked[2].id==provider.id
            source_providers.revalidate(facts=source_facts,scope_id=v.scope.id,importer=imports.Importer('user',v.user.id),capture_session_id=v.session.id)
            other.execute("SET lock_timeout='200ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                other.execute('UPDATE tracer_attachmentproviderconnection SET revoked_at=NOW(), authority_generation=authority_generation+1 WHERE id=%s',(conn.id,))
        other.execute('UPDATE tracer_attachmentproviderconnection SET revoked_at=NOW(), authority_generation=authority_generation+1 WHERE id=%s',(conn.id,))
    with transaction.atomic(),pytest.raises(AttachmentError):
        source_providers.lock_source(scope_id=v.scope.id,source=ref)
        source_providers.revalidate(facts=source_facts,scope_id=v.scope.id,importer=imports.Importer('user',v.user.id),capture_session_id=v.session.id)


def test_derivative_erased_at_replay_under_real_trigger(values,monkeypatch):
    v=values;job=make_job(v);fence=claim(v,job);row=staged(v,job,fence)
    external_recheck_only(monkeypatch)
    imports.publish(job_id=job.id,fence=fence,segment_ids=(row.id,),manifest=manifest(job,row))
    row.refresh_from_db()
    derivative=AttachmentRecordingDerivative.objects.get(attachment_id=row.attachment_id,representation='peaks')
    derivative.state='cleanup';derivative.save(update_fields=['state'])
    work=v.tmp_path/'derivative-replay';work.mkdir()
    storage=SyntheticStorage(v.binding.id)
    assert derivatives._settle(derivative,storage,work)
    derivative.refresh_from_db();first=derivative.erased_at;settled=derivative.reservation_settled_at
    assert first is not None and settled is not None
    assert derivatives._settle(derivative,storage,work)
    derivative.refresh_from_db()
    assert derivative.erased_at==first and derivative.reservation_settled_at==settled


def test_real_gcs_adapter_pending_receipts_preserve_sql_charge(values,monkeypatch):
    from tracer.tests import test_attachment_simplify_review_storage_gcs_config as gcs_fixture
    from tracer.services.attachments import uploads
    v=values;adapter,http,binding=gcs_fixture.native.__wrapped__(monkeypatch)
    binding.save(force_insert=True);v.binding=binding
    asset,occurrence,upload=make_upload(v,short=True)
    uploads.settle(upload.id,scope_id=v.scope.id,outcome='failed')
    http.live={(upload.serving_key,'101'),(upload.quarantine_key,'102')}
    http.retain_deletes=True
    for purpose in ('quarantine','serving'):
        http.meta[purpose].update(metageneration='2',softDeletePolicy={'retentionDurationSeconds':'604800'})
    wait=max(upload.cleanup_not_before,upload.expires_at+retention.TRANSFER_DRAIN)-timezone.now()
    with connection.cursor() as c:c.execute('SELECT pg_sleep(%s)',(max(0,wait.total_seconds())+0.1,))
    assert not retention._erase_asset(attachment_id=asset.id,scope_id=v.scope.id,now=timezone.now(),storage=adapter)
    assert not http.live and len(http.soft)==2
    upload.refresh_from_db();assert upload.reservation_settled_at is None and upload.cleanup_checked_at is None
    # XML no longer exposes these generations. The second real adapter pass must
    # still return pending using its retained-object evidence, not release U.
    assert not retention._erase_asset(attachment_id=asset.id,scope_id=v.scope.id,now=timezone.now(),storage=adapter)
    upload.refresh_from_db();assert upload.reservation_settled_at is None
    # Synthetic transport changes only: model a later true physical-absence
    # response. This is not a native bucket mutation or historical proof claim.
    http.soft.clear()
    assert retention._erase_asset(attachment_id=asset.id,scope_id=v.scope.id,now=timezone.now(),storage=adapter)
    upload.refresh_from_db();assert upload.reservation_settled_at is not None
    assert upload.usage_outcome=='failed' and upload.usage_stored_bytes==0
    occurrence.refresh_from_db();assert occurrence.deleted_at is None


def test_actual_expired_A_takeover_B_and_stale_exact_erase(values,monkeypatch):
    from tracer.services.attachments import sandbox,readiness
    from tracer.services.attachments.interfaces import LeaseFence
    v=values;job=make_job(v);now=timezone.now()
    job.lease_epoch=1;job.attempts=1;job.lease_phase='recovering';job.lease_token=uuid4();job.lease_until=now+timedelta(seconds=5)
    job.save(update_fields=['lease_epoch','attempts','lease_phase','lease_token','lease_until'])
    sandbox.prepare_import_scratch(job.id,1)
    job.lease_phase='writing';job.save(update_fields=['lease_phase'])
    old=LeaseFence('import',job.id,job.lease_token,1,job.lease_until)
    target=job.recording;target.sample_rate=8000;target.channels=1;target.save(update_fields=['sample_rate','channels'])
    plan=recordings.plan_segments(frame_count=1,rate=8000,channels=1,limits=recordings.Limits(100,100,30000,100000,7200000,1024**3))[0]
    row=imports._allocate(job,old,plan,'a'*64)
    with sandbox.import_execution(job.id,1):
        permit_A=imports.prepare_put(job_id=job.id,allocation_id=row.id,allocation_kind='segment',fence=old,purpose='serving',now=timezone.now())
    with connection.cursor() as c:c.execute('SELECT pg_sleep(5.1)')
    current=imports.claim(job.id,scope_id=v.scope.id,now=timezone.now())
    assert current.epoch==2 and current.token!=old.token
    job.refresh_from_db();store=SyntheticStorage(v.binding.id)
    imports._clean_versions(job,store,fence=current)
    job.refresh_from_db();assert job.lease_phase=='writing'
    B=ObjectIdentity(v.binding.id,'serving',row.storage_key,'version-B')
    A=ObjectIdentity(v.binding.id,'serving',row.storage_key,'version-A')
    def facts(identity):return VerifiedAssetFacts(StoredObject(identity,row.byte_length,row.sha256),'audio/wav',timezone.now(),8000,1,row.frame_count)
    with sandbox.import_execution(job.id,2):
        permit_B=imports.prepare_put(job_id=job.id,allocation_id=row.id,allocation_kind='segment',fence=current,purpose='serving',now=timezone.now())
        assert imports.record_put(job_id=job.id,allocation_id=row.id,allocation_kind='segment',permit=permit_B,identity=B,facts=facts(B))=='recorded'
    store.versions=[A,B]
    store.erase_version=store.erase_exact
    monkeypatch.setattr(readiness,'resolve_historical',lambda binding_id,operation:store)
    assert imports.record_put(job_id=job.id,allocation_id=row.id,allocation_kind='segment',permit=permit_A,identity=A,facts=facts(A))=='stale_unowned'
    assert imports.cleanup_stale_put(job_id=job.id,allocation_id=row.id,allocation_kind='segment',permit=permit_A,identity=A)=='stale_unowned'
    assert store.erased==[A] and store.versions==[B]
    row.refresh_from_db();assert row.storage_version=='version-B' and row.allocation_epoch==2
    assert imports.cleanup_stale_put(job_id=job.id,allocation_id=row.id,allocation_kind='segment',permit=permit_A,identity=B)=='already_owned'
    assert store.erased==[A]


def test_complete_120_segment_manifest_denies_deleted_occurrence_97(values,monkeypatch):
    from tracer.services.attachments import manifests,occurrences
    v=values
    eid=envelopes.seal(scope=v.scope,url='https://storage.invalid/120.wav',creator_kind='user',creator_id=v.user.id,direction='mixed',source_bytes=1964,source_expires_at=timezone.now()+timedelta(minutes=5),generation_kind='http_strong_etag',generation='"fixture-120"',mime_type='audio/wav',duration_ms=120,trace_id=v.trace,span_id=v.span,capture_session_id=v.session.id)
    v.envelope=AttachmentSourceEnvelope.objects.get(pk=eid)
    job=make_job(v);fence=claim(v,job)
    target=job.recording;target.sample_rate=8000;target.channels=1;target.save(update_fields=['sample_rate','channels'])
    plans=recordings.plan_segments(frame_count=960,rate=8000,channels=1,limits=recordings.Limits(100,60,1,100000,7200000,1024**3))
    assert len(plans)==120
    rows=[]
    from tracer.services.attachments import sandbox
    with sandbox.import_execution(job.id,fence.epoch):
        for plan in plans:
            row=imports._allocate(job,fence,plan,'a'*64)
            permit=imports.prepare_put(job_id=job.id,allocation_id=row.id,allocation_kind='segment',fence=fence,purpose='serving',now=timezone.now())
            identity=ObjectIdentity(v.binding.id,'serving',row.storage_key,'fixture-v1')
            facts=VerifiedAssetFacts(StoredObject(identity,row.byte_length,row.sha256),'audio/wav',timezone.now(),8000,1,row.frame_count)
            assert imports.record_put(job_id=job.id,allocation_id=row.id,allocation_kind='segment',permit=permit,identity=identity,facts=facts)=='recorded'
            rows.append(row)
    body=recordings.build_manifest(recording_id=target.client_id,rate=8000,channels=1,direction='mixed',track_ids=[uuid4()],segments=[(r.occurrence_client_id,p) for r,p in zip(rows,plans,strict=True)],duration_ms=120,capture_complete=True)
    external_recheck_only(monkeypatch)
    imports.publish(job_id=job.id,fence=fence,segment_ids=tuple(r.id for r in rows),manifest=body)
    source=occurrences.SourceState(v.scope.id,occurrences.Parent(v.trace,v.span),4,timezone.now()-timedelta(minutes=1),timezone.now()+timedelta(days=30),True)
    for occurrence in AttachmentOccurrence.objects.filter(recording=target):
        retention.bind_retention(scope_id=v.scope.id,occurrence_id=occurrence.id,source=source)
    viewer=occurrences.Viewer(v.user.id,uuid4(),v.scope.organization_id,v.scope.workspace_id,v.scope.project_id)
    # Only the authority adapter's external session/source IO is synthetic;
    # membership, policy, every occurrence gate and manifest are real ORM/code.
    authority=NS(session_is_live=lambda who:who==viewer,source=lambda who,scope,parent:source)
    parent={'trace_id':str(v.trace),'span_id':v.span}
    before=manifests.read_manifest(viewer,authority,recording_id=target.client_id,parent=parent)
    assert len(before['segments'])==120 and before==body
    denied=AttachmentOccurrence.objects.get(client_id=rows[97].occurrence_client_id)
    denied.deleted_at=timezone.now();denied.save(update_fields=['deleted_at'])
    with pytest.raises(AttachmentError,match='not_found'):
        manifests.read_manifest(viewer,authority,recording_id=target.client_id,parent=parent)
    target.refresh_from_db();assert target.manifest==body and len(target.manifest['segments'])==120


def test_real_gcs_exact_success_composes_with_derivative_unknown_cleanup(values,monkeypatch):
    """Real ORM + real GCS adapter; only provider HTTP transport is synthetic.

    A successful exact erase returns None by frozen interface. The derivative
    consumer must not convert proven absence to cleanup_unavailable. This is a
    positive contract assertion, not xfail or an expected-exception oracle.
    """
    from tracer.tests import test_attachment_simplify_review_storage_gcs_config as gcs_fixture
    v=values;adapter,http,binding=gcs_fixture.native.__wrapped__(monkeypatch)
    binding.save(force_insert=True);v.binding=binding
    job=make_job(v);fence=claim(v,job);segment=staged(v,job,fence,version='200')
    external_recheck_only(monkeypatch)
    imports.publish(job_id=job.id,fence=fence,segment_ids=(segment.id,),manifest=manifest(job,segment))
    segment.refresh_from_db();asset=segment.attachment
    row=AttachmentRecordingDerivative.objects.get(attachment=asset,representation='peaks')
    # Seed the real recovering cleanup owner through the actual capacity/lease
    # transition. No source bytes, authorization grant, or ready state is faked.
    derivative_fence=derivatives._claim(row,None)
    assert derivative_fence is not None
    row.refresh_from_db();assert row.state=='recovering' and row.reservation_charge_bytes>0
    # A single proven absence is not owner settlement, much less settlement of
    # the import or another upload sharing the project/organization budgets.
    _, _, outstanding_upload = make_upload(v)
    job.refresh_from_db();outstanding_upload.refresh_from_db()
    assert job.reservation_settled_at is None and job.reservation_charge_bytes > 0
    assert outstanding_upload.reservation_settled_at is None
    assert outstanding_upload.reservation_charge_bytes > 0
    budget_keys = (
        capacity.IMPORT_POOL_KEY, capacity.DERIVATIVE_POOL_KEY,
        outstanding_upload.reservation_pool_key,
        f'org:{v.scope.organization_id}', f'project:{v.scope.project_id}',
    )
    budgets_before = list(capacity_db.AttachmentBudget.objects.filter(
        pk__in=budget_keys
    ).order_by('pk').values_list('pk', *capacity_db.BUDGET_FIELDS))
    orphan=(row.quarantine_key,'101')
    http.live={orphan};http.soft.clear()
    real_erase=adapter.erase_version;observed=[]
    def exact(identity):
        result=real_erase(identity)
        observed.append((identity,result))
        assert result is None  # frozen PrivateMediaStorage exact-erase contract
        return result
    monkeypatch.setattr(adapter,'erase_version',exact)
    try:
        derivatives._erase_unknown(adapter,row,asset,fence=derivative_fence)
    finally:
        row.refresh_from_db()
        print('EXACT_ERASE_PROTOCOL',{'returned_none':bool(observed) and observed[0][1] is None,'live_orphan_absent':orphan not in http.live,'retained_orphan_absent':orphan not in http.soft,'derivative_state':row.state,'charge_retained':row.reservation_settled_at is None,'cleanup_checked_at':str(row.cleanup_checked_at)})
        assert len(observed)==1 and observed[0][1] is None
        assert orphan not in http.live and orphan not in http.soft
        assert row.reservation_settled_at is None
    assert len(observed)==1
    assert row.cleanup_checked_at is None
    job.refresh_from_db();outstanding_upload.refresh_from_db()
    assert job.reservation_settled_at is None
    assert outstanding_upload.reservation_settled_at is None
    assert list(capacity_db.AttachmentBudget.objects.filter(
        pk__in=budget_keys
    ).order_by('pk').values_list('pk', *capacity_db.BUDGET_FIELDS)) == budgets_before
