import { useState } from 'react';
import PropTypes from 'prop-types';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useSkillV11Scope, useSkillV11Query } from 'src/api/skills/testing';
import { useContextDetail, useContextDraft } from 'src/api/skills/context-authoring';
import { contextReviewReady, loadContextReview, useAcknowledgeContextHead, usePublishContext } from 'src/api/skills/context-production';
import { Alert, Button, Stack } from '@mui/material';
import { reviewsReady, isStaleReview, useReviewInbox, useMarkReviewSeen, useReleaseRequest, useDecideReleaseRequest, useExecuteReleaseRequest } from 'src/api/skills/review-production';
import { paths } from 'src/routes/paths';
import ReleaseRequestPanel, { useReviewedOperation } from './ReleaseRequestPanel';
import ReleaseRequestComposer from './ReleaseRequestComposer';
import ReleaseRenewalDialog from './ReleaseRenewalDialog';

// Intent: an assigned reviewer inspects immutable bytes, then makes an explicit
// decision. Hierarchy: candidate/diff before action; no inferred evidence.
// Palette/depth/surfaces/type/spacing: reuse existing flat review components,
// theme status roles, m3/s1/s3 and 12px groups; no new visual system.
export function RequestReviewController({ scopeKey, requestId, contextId, viewState, View = ReleaseRequestPanel }) {
  const access = useSkillV11Scope();
  const query = useReleaseRequest(requestId, contextId);
  const decide = useDecideReleaseRequest();
  const execute = useExecuteReleaseRequest();
  const [renewing, setRenewing] = useState(false);
  // J4: the reviewer's attestation for one exact revision/fingerprint survives
  // the pending -> approved refresh; a renewal approved in the dialog attests
  // the renewed revision it created. Nothing carries across revisions.
  const [reviewedIdentity, setReviewedIdentity] = useState(null);
  const state = viewState || (query.isLoading ? 'loading' : query.isError ? 'error' : query.data ? 'ready' : 'unavailable');
  const canDecide = Boolean(query.data && query.data.request.authorId !== access.scope.userId);
  // The dialog approval attests only the changes it showed; the renewed packet
  // itself is attested on the page before release. One stable parent keeps
  // the dialog mounted while the page switches between stale and renewed.
  const renewal = <ReleaseRenewalDialog open={renewing} requestId={requestId} onClose={() => { setRenewing(false); query.refetch(); }} />;
  // Detailed evidence became unavailable: only the assigned reviewer's
  // explicit renewal remains. Expired evidence is removed, never substituted.
  if (state === 'ready' && query.data?.renewalRequired) return <>
    <Stack spacing={1.5} sx={{ px: { xs: 2, sm: 3 }, py: 2 }}>
      <Alert severity="warning">Detailed evidence for revision {query.data.request.revision} is no longer available ({query.data.renewalRequired}). No approval of that revision can release it.</Alert>
      <Stack direction="row"><Button variant="contained" onClick={() => setRenewing(true)}>Renew review</Button></Stack>
    </Stack>
    {renewal}
  </>;
  return <>
    <View scopeKey={scopeKey} state={state} {...query.data} onRetry={query.refetch} onRefresh={query.refetch} onRenew={() => setRenewing(true)}
      permissions={{ canDecide, canExecute: query.data?.canExecute === true }} reviewedIdentity={reviewedIdentity} onReviewedChange={setReviewedIdentity}
      onDecide={(input, options) => decide.mutateAsync({ ...input, ...options })}
      onExecute={async (input, options) => {
        try { return await execute.mutateAsync({ ...input, ...options }); } catch (error) {
          // A server refusal for changed preconditions opens the renewal
          // inspection directly for the assigned reviewer; nothing is renewed
          // or approved until they inspect and decide.
          if (canDecide && isStaleReview(error)) setRenewing(true);
          throw error;
        }
      }} />
    {renewal}
  </>;
}
RequestReviewController.propTypes = { scopeKey: PropTypes.string.isRequired, requestId: PropTypes.string.isRequired, contextId: PropTypes.string, viewState: PropTypes.string, View: PropTypes.elementType };

export function ReviewInboxController({ scopeKey, viewState, View }) {
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState('');
  const [params] = useSearchParams();
  const authored = params.get('mine') === '1';
  const query = useReviewInbox({ page, status, authored });
  const seen = useMarkReviewSeen();
  const navigate = useNavigate();
  const requestId = params.get('request');
  if (params.get('compose') === '1') return <ReleaseRequestComposer key={`${scopeKey}:${params.get('renew') || params.get('candidate') || 'new'}`} scopeKey={scopeKey}
    renewal={params.get('renew') ? { id: params.get('renew'), revision: Number(params.get('revision')), subjectId: params.get('subject'), kind: params.get('kind') } : undefined}
    prefill={!params.get('renew') && params.get('subject') && params.get('candidate') ? { subjectId: params.get('subject'), kind: params.get('kind') || 'skill', name: params.get('name') || '',
      candidate: { id: params.get('candidate'), number: Number(params.get('number')), digest: params.get('digest') } } : undefined} />;
  if (requestId) return <RequestReviewController key={`${scopeKey}:${requestId}`} scopeKey={scopeKey} requestId={requestId} viewState={viewState} />;
  const state = viewState || (!reviewsReady() ? 'unavailable' : query.isLoading ? 'loading' : query.isError ? 'error' : query.data ? 'ready' : 'unavailable');
  return <View scopeKey={scopeKey} state={state} data={query.data} status={status} authored={authored}
    onCompose={() => navigate(`${paths.dashboard.skills.reviews}?compose=1`)}
    onToggleAuthored={() => { setPage(1); navigate(`${paths.dashboard.skills.reviews}${authored ? '' : '?mine=1'}`); }} onStatusChange={(value) => { setStatus(value); setPage(1); }} onPageChange={setPage} onRetry={query.refetch} onSeen={seen.mutateAsync}
    onOpen={(request) => navigate(authored && request.status !== 'released'
      ? `${paths.dashboard.skills.reviews}?${new URLSearchParams({ compose: '1', renew: request.id, revision: String(request.revision), subject: request.subjectId, kind: request.subjectKind })}`
      : request.subjectKind === 'agent_context' ? `${paths.dashboard.skills.contextReview(request.subjectId)}?request=${request.id}` : `${paths.dashboard.skills.reviews}?request=${request.id}`)} />;
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
  const data = query.data;
  const head = data?.review;
  const state = viewState || (!contextReviewReady() ? 'unavailable' : detail.isError || draft.isError || query.isError ? 'error' : detail.isLoading || draft.isLoading || query.isLoading ? 'loading' : data ? 'ready' : 'unavailable');
  const refresh = () => { detail.refetch(); draft.refetch(); query.refetch(); };
  const draftReview = head && { ...head, revision: head.draftRevision, current: head.draftRevision === draft.data?.revision && head.candidateDigest === draft.data?.digest,
    complete: data.complete, changes: data.originChanges, headChanges: data.headChanges, acceptedLock: draft.data?.resolvedLock, checks: data.checks,
    headAcknowledged: draft.data?.headAcknowledged && draft.data?.reviewFingerprint === head.reviewFingerprint,
    note: draft.data?.note, productionVersion: data.releaseState.labels.find((label) => label.name === 'production')?.version };
  return <View scopeKey={scopeKey} state={state} name={detail.data?.context.name} draftReview={draftReview} canPublish={data?.releaseState.permissions.canPublish === true}
    onRetry={refresh} onRefresh={refresh} onAcknowledgeHead={(input, options) => acknowledge.mutateAsync({ ...input, ...options })}
    onPublish={async (input, options) => { const receipt = await publish.mutateAsync({ ...input, ...options }); navigate(`${paths.dashboard.skills.contextDetail(contextId)}?version=${receipt.version.number}`); return receipt; }}
    oneStepOperation={oneStep}
    onPublishAndRequest={async ({ headAcknowledged, idempotencyKey, ...input }, options) => {
      // J4: acknowledge the reviewed head only when the current draft still
      // needs it (a retry after an unknown publish outcome reuses the earlier
      // acknowledgment of the same bytes). The server rechecks digest, head
      // and fingerprint on both writes.
      let fields = input;
      if (!headAcknowledged) {
        const current = (await draft.refetch()).data;
        if (current?.headAcknowledged && current.digest === input.expectedDigest) fields = { ...input, revision: current.revision, reviewFingerprint: current.reviewFingerprint };
        else {
          const acked = await acknowledge.mutateAsync({ ...input, ...options });
          fields = { ...input, revision: acked.revision, reviewFingerprint: acked.reviewFingerprint };
        }
      }
      const receipt = await publish.mutateAsync({ ...fields, idempotencyKey, ...options });
      navigate(`${paths.dashboard.skills.reviews}?${new URLSearchParams({ compose: '1', kind: 'agent_context', subject: contextId, name: detail.data?.context.name || '',
        candidate: receipt.version.id, number: String(receipt.version.number), digest: receipt.version.digest })}`);
      return receipt;
    }} />;
}
DraftReviewController.propTypes = { scopeKey: PropTypes.string.isRequired, contextId: PropTypes.string.isRequired, viewState: PropTypes.string, View: PropTypes.elementType.isRequired };
export function ContextReviewController(props) {
  return props.requestId ? <RequestReviewController {...props} /> : <DraftReviewController {...props} />;
}
ContextReviewController.propTypes = { requestId: PropTypes.string };
