import { useRef, useState } from 'react';
import PropTypes from 'prop-types';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useSkillV11Scope, useSkillV11Query } from 'src/api/skills/testing';
import { useContextDetail, useContextDraft } from 'src/api/skills/context-authoring';
import { contextReviewReady, loadContextReview, useAcknowledgeContextHead, usePublishContext } from 'src/api/skills/context-production';
import { Button, Stack } from '@mui/material';
import { isStaleReview, reviewsReady, useReviewInbox, useMarkReviewSeen, useReleaseRequest, useRenewalPreview, useDecideReleaseRequest, useExecuteReleaseRequest } from 'src/api/skills/review-production';
import { paths } from 'src/routes/paths';
import ObjectHeader from '../components/ObjectHeader';
import axios from 'src/utils/axios';
import { apiPath } from 'src/api/contracts/api-surface';
import { operationStatus } from 'src/api/skills/operation-errors';
import CapabilityNotice from '../components/CapabilityNotice';
import { releaseTitle, WAITING_FOR_APPROVAL } from './reviewPresentation';
import { skillsRouteSx } from '../components/routeGutter';
import StatusBadge from 'src/components/status-badge';
import { reviewComposerUrl } from '../utils/review-links';
import { loadReviewOptions } from 'src/api/skills/review-authoring';
import { publishedDraftResults } from './evidenceSuggestions';
import ReleaseRequestPanel, { useReviewedOperation } from './ReleaseRequestPanel';
import ReleaseRequestComposer from './ReleaseRequestComposer';
import ReleaseRenewalDialog from './ReleaseRenewalDialog';
import { enrichReviewDisplay, useReviewMembers, useReviewSubject } from 'src/api/skills/review-metadata';

// Intent: an assigned reviewer inspects immutable bytes, then makes an explicit
// decision. Hierarchy: candidate/diff before action; no inferred evidence.
// Palette/depth/surfaces/type/spacing: reuse existing flat review components,
// theme status roles, m3/s1/s3 and 12px groups; no new visual system.
export function RequestReviewController({ scopeKey, requestId, contextId, viewState, View = ReleaseRequestPanel }) {
  const access = useSkillV11Scope();
  const navigate = useNavigate();
  const query = useReleaseRequest(requestId, contextId);
  const [refusedRevision, setRefusedRevision] = useState(null);
  const revisionKey = query.data ? `${scopeKey}:${requestId}:${query.data.request.revision}` : null;
  const paused = Boolean(query.data?.renewalRequired || query.data?.staleCode || (revisionKey && refusedRevision === revisionKey));
  const renewalPreview = useRenewalPreview(requestId, paused);
  const renewalReason = renewalPreview.data?.changes?.some((row) => row.item.endsWith('expected_security_revision')) ? 'The security policy changed after this request was made.' : undefined;
  const decide = useDecideReleaseRequest();
  const execute = useExecuteReleaseRequest();
  const [renewing, setRenewing] = useState(false);
  const [executedReceipt, setExecutedReceipt] = useState(null);
  // J4: the reviewer's attestation for one exact revision/fingerprint survives
  // the pending -> approved refresh; a renewal approved in the dialog attests
  // the renewed revision it created. Nothing carries across revisions.
  const [reviewedIdentity, setReviewedIdentity] = useState(null);
  const state = viewState || (query.isLoading ? 'loading' : query.isError ? 'error' : query.data ? 'ready' : 'unavailable');
  const canDecide = Boolean(query.data && query.data.request.authorId !== access.scope.userId);
  const members = useReviewMembers();
  const subject = useReviewSubject(query.data?.request?.subjectId || query.data?.receipt?.subjectId, query.data?.request?.subjectKind || query.data?.receipt?.subjectKind);
  const display = enrichReviewDisplay(query.data?.request, query.data?.receipt || (executedReceipt?.requestId === requestId && executedReceipt?.scopeKey === scopeKey ? executedReceipt.value : null), members.data, subject.data);
  const request = display.request;
  const author = Boolean(request?.authorId && request.authorId === access.scope.userId);
  const revise = () => navigate(`${paths.dashboard.skills.reviews}?${new URLSearchParams({ compose: '1', renew: request.id, revision: String(request.revision), subject: request.subjectId, kind: request.subjectKind })}`);
  const transition = query.data?.preview?.packet.transition;
  const resourceName = request?.name || query.data?.receipt?.name || 'Release review';
  // Once released, the title states the outcome and no badge repeats it; until then it names the move being asked for.
  const released = request?.status === 'released' || Boolean(display.receipt);
  const movedFrom = display.receipt?.previousVersion ?? request?.previousVersion ?? transition?.expectedVersion;
  const movedLabel = display.receipt?.postMoveState?.label || transition?.label;
  const movedTo = display.receipt?.postMoveState?.version ?? transition?.version;
  const movedTitle = released && movedLabel && movedTo != null ? `${movedLabel.charAt(0).toUpperCase()}${movedLabel.slice(1)} moved ${movedFrom != null ? `from v${movedFrom} ` : ''}to v${movedTo}` : null;
  const header = <ObjectHeader crumbs={[{ label: 'Reviews', to: paths.dashboard.skills.reviews }, { label: resourceName }]} badge={released ? undefined : paused ? <StatusBadge status="stale" label="Paused" /> : request?.status ? <StatusBadge status={request.status === 'pending' ? 'in_review' : request.status} label={request.status === 'pending' ? WAITING_FOR_APPROVAL : undefined} /> : undefined} title={movedTitle || (transition ? releaseTitle(transition) : resourceName)} purpose={query.data?.renewalRequired ? `Revision ${request.revision} · Current candidate shown for renewal. The previous approval does not carry forward.` : undefined} actions={author && request.status !== 'released' ? <Button variant="outlined" onClick={revise}>Revise request</Button> : undefined} />;

  // The dialog approval attests only the changes it showed; the renewed packet
  // itself is attested on the page before release. One stable parent keeps
  // the dialog mounted while the page switches between stale and renewed.
  const renewal = <ReleaseRenewalDialog open={renewing} requestId={requestId} onClose={() => { setRenewing(false); query.refetch(); }} />;
  return <Stack spacing={1.5} sx={skillsRouteSx}>
    {header}
    <View scopeKey={scopeKey} state={state} {...query.data} renewalReason={renewalReason} staleCode={query.data?.staleCode || query.data?.renewalRequired || (paused ? 'review_stale' : undefined)} request={request} receipt={display.receipt} onRetry={query.refetch} onRefresh={async () => { const result = await query.refetch(); if (!result?.isError && result?.data?.preview?.current) setRefusedRevision(null); return result; }} onRenew={() => setRenewing(true)}
      renewalOpen={renewing} currentUserId={access.scope?.userId} permissions={{ canDecide, canExecute: !author && query.data?.canExecute === true }} reviewedIdentity={reviewedIdentity} onReviewedChange={setReviewedIdentity}
      onDecide={async (input, options) => {
        try { return await decide.mutateAsync({ ...input, ...options }); }
        catch (error) { if (isStaleReview(error)) setRefusedRevision(revisionKey); throw error; }
      }}
      onExecute={async (input, options) => {
        try { const receipt = await execute.mutateAsync({ ...input, ...options }); setExecutedReceipt({ requestId, scopeKey, value: receipt }); return receipt; }
        catch (error) { if (isStaleReview(error)) { setRefusedRevision(revisionKey); setRenewing(true); } throw error; }
      }} />
    {renewal}
  </Stack>;
}
RequestReviewController.propTypes = { scopeKey: PropTypes.string.isRequired, requestId: PropTypes.string.isRequired, contextId: PropTypes.string, viewState: PropTypes.string, View: PropTypes.elementType };

export function ReviewInboxController({ scopeKey, viewState, View }) {
  const access = useSkillV11Scope();
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState('');
  const [params] = useSearchParams();
  const authored = params.get('mine') === '1';
  const query = useReviewInbox({ page, status, authored });
  // Unread includes historical releases. Decision counts come from pending requests only.
  const pending = useReviewInbox({ page: 1, status: 'pending', authored: false });
  const members = useReviewMembers();
  // Inbox rows omit recipients. Read display metadata only for pending rows,
  // using the existing scoped endpoint, and reject a different revision.
  const waiting = (query.data?.results || []).filter((row) => row.status === 'pending' && !row.recipients?.length);
  const recipientMetadata = useSkillV11Query({
    parts: ['review-inbox-recipients', waiting.map((row) => [row.id, row.revision])],
    enabled: waiting.length > 0 && !query.isError,
    load: async (config) => Promise.all(waiting.map(async (row) => {
      try {
        const response = await axios.get(apiPath('/model-hub/skills/release-requests/{request_id}/', { request_id: row.id }), config);
        const request = response.data.result;
        return request.revision === row.revision ? { id: row.id, revision: row.revision, recipients: request.recipients } : null;
      } catch { return null; }
    })),
  });
  const data = query.data && { ...query.data, pendingCount: pending.isError ? undefined : pending.data?.count, results: query.data.results.map((row) => {
    const metadata = Array.isArray(recipientMetadata.data) ? recipientMetadata.data.find((item) => item?.id === row.id && item.revision === row.revision) : null;
    return enrichReviewDisplay({ ...row, recipients: row.recipients?.length ? row.recipients : metadata?.recipients }, null, members.data).request;
  }) };
  const seen = useMarkReviewSeen();
  const navigate = useNavigate();
  const requestId = params.get('request');
  if (operationStatus(query.error) === 403 || operationStatus(pending.error) === 403) return <View scopeKey={scopeKey} state="forbidden" />;
  if (params.get('compose') === '1' && !query.data) return <CapabilityNotice title="Checking review access" description="Wait for your workspace permissions before creating a request." />;
  if (params.get('compose') === '1') return <ReleaseRequestComposer key={`${scopeKey}:${params.get('renew') || params.get('candidate') || 'new'}`} scopeKey={scopeKey} state={viewState}
    renewal={params.get('renew') ? { id: params.get('renew'), revision: Number(params.get('revision')), subjectId: params.get('subject'), kind: params.get('kind') } : undefined}
    prefill={!params.get('renew') && params.get('subject') && params.get('candidate') ? { subjectId: params.get('subject'), kind: params.get('kind') || 'skill', name: params.get('name') || '', label: params.get('label') || 'production', target: params.get('target') || '', evidence: (params.get('evidence') || '').split(',').filter(Boolean),
      candidate: { id: params.get('candidate'), number: Number(params.get('number')), digest: params.get('digest') } } : undefined} />;
  if (requestId) return <RequestReviewController key={`${scopeKey}:${requestId}`} scopeKey={scopeKey} requestId={requestId} viewState={viewState} />;
  const state = viewState || (!reviewsReady() ? 'unavailable' : query.isLoading ? 'loading' : query.isError ? 'error' : query.data ? 'ready' : 'unavailable');
  return <View scopeKey={scopeKey} state={state} data={data} status={status} authored={authored} currentUserId={access.scope?.userId}
    onCompose={state === 'ready' ? () => navigate(`${paths.dashboard.skills.reviews}?compose=1`) : undefined}
    onToggleAuthored={() => { setPage(1); navigate(`${paths.dashboard.skills.reviews}${authored ? '' : '?mine=1'}`); }} onStatusChange={(value) => { setStatus(value); setPage(1); }} onPageChange={setPage} onRetry={query.refetch} onSeen={seen.mutateAsync}
    onOpen={(item) => navigate(`${paths.dashboard.skills.reviews}?request=${item.id}`)} />;
}
ReviewInboxController.propTypes = { scopeKey: PropTypes.string.isRequired, viewState: PropTypes.string, View: PropTypes.elementType.isRequired };

function DraftReviewController({ scopeKey, contextId, viewState, View }) {
  const navigate = useNavigate();
  const detail = useContextDetail(contextId);
  const draft = useContextDraft(contextId);
  const query = useSkillV11Query({ parts: ['agent_context', contextId, 'publication-review', draft.data?.revision, draft.data?.reviewFingerprint], enabled: contextReviewReady() && Boolean(draft.data) && !draft.isError, load: (config) => loadContextReview(contextId, draft.data.revision, config) });
  const acknowledge = useAcknowledgeContextHead(contextId);
  const publish = usePublishContext(contextId);
  // Held here, not in the form: the head acknowledgment advances the draft
  // revision and remounts the review, and an unknown publish outcome must stay
  // retryable with its original idempotency key.
  const oneStep = useReviewedOperation();
  const publishOperation = useReviewedOperation();
  const data = query.data;
  const head = data?.review;
  const state = viewState || (!contextReviewReady() ? 'unavailable' : detail.isError || draft.isError || query.isError ? 'error' : detail.isLoading || draft.isLoading || query.isLoading ? 'loading' : data ? 'ready' : 'unavailable');
  const refresh = () => { detail.refetch(); draft.refetch(); query.refetch(); };
  const draftReview = head && { ...head, revision: head.draftRevision, current: head.draftRevision === draft.data?.revision && head.candidateDigest === draft.data?.digest,
    complete: data.complete, changes: data.originChanges, headChanges: data.headChanges, acceptedLock: draft.data?.resolvedLock, checks: data.checks,
    headAcknowledged: draft.data?.headAcknowledged && draft.data?.reviewFingerprint === head.reviewFingerprint,
    note: draft.data?.note, productionVersion: data.releaseState.labels.find((label) => label.name === 'production')?.version };
  // Hold the post-acknowledgment body as well as the operation key. A successful
  // publish removes the draft: replaying an unknown outcome must go straight to
  // publish's receipt lookup, not refetch/re-acknowledge a missing or newer draft.
  const publication = useRef(null);
  const acknowledgeThenPublish = async ({ headAcknowledged, idempotencyKey, ...input }, options) => {
    if (publication.current?.idempotencyKey !== idempotencyKey) {
      let fields = input;
      if (!headAcknowledged) {
        const refreshed = await draft.refetch();
        if (refreshed.isError) throw refreshed.error;
        const current = refreshed.data;
        // Recover an acknowledgment whose response was lost only across its
        // single revision increment, never across a same-bytes edit/restore ABA.
        if (current?.headAcknowledged && current.revision === input.revision + 1 &&
          current.digest === input.expectedDigest && current.baseVersion === input.baseVersion &&
          current.expectedHeadVersion === input.expectedHeadVersion && current.reviewFingerprint) {
          fields = { ...input, revision: current.revision, reviewFingerprint: current.reviewFingerprint };
        } else {
          const acked = await acknowledge.mutateAsync({ ...input, ...options });
          fields = { ...input, revision: acked.revision, reviewFingerprint: acked.reviewFingerprint };
        }
      }
      publication.current = { ...fields, idempotencyKey, ...options };
    }
    return publish.mutateAsync(publication.current);
  };
  return <View scopeKey={scopeKey} state={state} name={detail.data?.context.name} draftReview={draftReview} canPublish={data?.releaseState.permissions.canPublish === true}
    onRetry={refresh} onRefresh={refresh}
    onPublish={async (input, options) => { const receipt = await acknowledgeThenPublish(input, options); // A repair published from older content is verified next: open its Run a test view directly.
      const repair = input.baseVersion != null && input.expectedHeadVersion != null && input.baseVersion !== input.expectedHeadVersion;
      navigate(`${paths.dashboard.skills.contextDetail(contextId)}?version=${receipt.version.number}${repair ? '&tab=tests&tests=run' : ''}`); return receipt; }}
    publishOperation={publishOperation}
    oneStepOperation={oneStep}
    onPublishAndRequest={async (input, options) => {
      const receipt = await acknowledgeThenPublish(input, options);
      // The author's own settled results for exactly these bytes travel with the request (UXF-17): they
      // arrive ticked and private, and nothing is shared or attached until the author confirms it there.
      const evidence = await loadReviewOptions('agent_context', contextId, receipt.version.id)
        .then((value) => publishedDraftResults(value.evidence, { subjectId: contextId, subjectKind: 'agent_context', digest: receipt.version.digest }))
        .catch(() => []);
      navigate(reviewComposerUrl({ kind: 'agent_context', subjectId: contextId, name: detail.data?.context.name || '', candidateId: receipt.version.id, number: receipt.version.number, digest: receipt.version.digest, evidence }));
      return receipt;
    }} />;
}
DraftReviewController.propTypes = { scopeKey: PropTypes.string.isRequired, contextId: PropTypes.string.isRequired, viewState: PropTypes.string, View: PropTypes.elementType.isRequired };
export function ContextReviewController(props) {
  return props.requestId ? <RequestReviewController {...props} /> : <DraftReviewController {...props} />;
}
ContextReviewController.propTypes = { requestId: PropTypes.string };
