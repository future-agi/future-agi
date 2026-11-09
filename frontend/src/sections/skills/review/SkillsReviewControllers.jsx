import { useState } from 'react';
import PropTypes from 'prop-types';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useSkillV11Scope, useSkillV11Query } from 'src/api/skills/testing';
import { useContextDetail, useContextDraft } from 'src/api/skills/context-authoring';
import { contextReviewReady, loadContextReview, useAcknowledgeContextHead, usePublishContext } from 'src/api/skills/context-production';
import { reviewsReady, useReviewInbox, useMarkReviewSeen, useReleaseRequest, useDecideReleaseRequest, useExecuteReleaseRequest } from 'src/api/skills/review-production';
import { paths } from 'src/routes/paths';
import ReleaseRequestPanel from './ReleaseRequestPanel';

// Intent: an assigned reviewer inspects immutable bytes, then makes an explicit
// decision. Hierarchy: candidate/diff before action; no inferred evidence.
// Palette/depth/surfaces/type/spacing: reuse existing flat review components,
// theme status roles, m3/s1/s3 and 12px groups; no new visual system.
export function RequestReviewController({ scopeKey, requestId, contextId, viewState, View = ReleaseRequestPanel }) {
  const access = useSkillV11Scope();
  const query = useReleaseRequest(requestId, contextId);
  const decide = useDecideReleaseRequest();
  const execute = useExecuteReleaseRequest();
  const state = viewState || (query.isLoading ? 'loading' : query.isError ? 'error' : query.data ? 'ready' : 'unavailable');
  return <View scopeKey={scopeKey} state={state} {...query.data} onRetry={query.refetch} onRefresh={query.refetch}
    permissions={{ canDecide: Boolean(query.data && query.data.request.authorId !== access.scope.userId), canExecute: query.data?.canExecute === true }}
    onDecide={(input, options) => decide.mutateAsync({ ...input, ...options })} onExecute={(input, options) => execute.mutateAsync({ ...input, ...options })} />;
}
RequestReviewController.propTypes = { scopeKey: PropTypes.string.isRequired, requestId: PropTypes.string.isRequired, contextId: PropTypes.string, viewState: PropTypes.string, View: PropTypes.elementType };

export function ReviewInboxController({ scopeKey, viewState, View }) {
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState('');
  const [params] = useSearchParams();
  const query = useReviewInbox({ page, status });
  const seen = useMarkReviewSeen();
  const navigate = useNavigate();
  const requestId = params.get('request');
  if (requestId) return <RequestReviewController key={`${scopeKey}:${requestId}`} scopeKey={scopeKey} requestId={requestId} viewState={viewState} />;
  const state = viewState || (!reviewsReady() ? 'unavailable' : query.isLoading ? 'loading' : query.isError ? 'error' : query.data ? 'ready' : 'unavailable');
  return <View scopeKey={scopeKey} state={state} data={query.data} status={status} onStatusChange={(value) => { setStatus(value); setPage(1); }} onPageChange={setPage} onRetry={query.refetch} onSeen={seen.mutateAsync}
    onOpen={(request) => navigate(request.subjectKind === 'agent_context' ? `${paths.dashboard.skills.contextReview(request.subjectId)}?request=${request.id}` : `${paths.dashboard.skills.reviews}?request=${request.id}`)} />;
}
ReviewInboxController.propTypes = { scopeKey: PropTypes.string.isRequired, viewState: PropTypes.string, View: PropTypes.elementType.isRequired };

function DraftReviewController({ scopeKey, contextId, viewState, View }) {
  const navigate = useNavigate();
  const detail = useContextDetail(contextId);
  const draft = useContextDraft(contextId);
  const query = useSkillV11Query({ parts: ['agent_context', contextId, 'publication-review', draft.data?.revision, draft.data?.reviewFingerprint], enabled: contextReviewReady() && Boolean(draft.data) && !draft.isError, load: (config) => loadContextReview(contextId, draft.data.revision, config) });
  const acknowledge = useAcknowledgeContextHead(contextId);
  const publish = usePublishContext(contextId);
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
    onPublish={async (input, options) => { const receipt = await publish.mutateAsync({ ...input, ...options }); navigate(`${paths.dashboard.skills.contextDetail(contextId)}?version=${receipt.version.number}`); return receipt; }} />;
}
DraftReviewController.propTypes = { scopeKey: PropTypes.string.isRequired, contextId: PropTypes.string.isRequired, viewState: PropTypes.string, View: PropTypes.elementType.isRequired };
export function ContextReviewController(props) {
  return props.requestId ? <RequestReviewController {...props} /> : <DraftReviewController {...props} />;
}
ContextReviewController.propTypes = { requestId: PropTypes.string };
