import { useState } from 'react';
import PropTypes from 'prop-types';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Alert, Button, Stack } from '@mui/material';
import { useSkillV11Scope } from 'src/api/skills/testing';
import { contextAuthoringReady, useContexts, useCreateContext, useContextDetail, useContextDraft, useStartContextDraft, useSaveContextDraft, useDiscardContextDraft } from 'src/api/skills/context-authoring';
import { useCheckpointDraft, checkpointsReady } from 'src/api/skills/r3-intake';
import { paths } from 'src/routes/paths';
import ContentSkeleton from 'src/components/content-skeleton';
import ErrorState from 'src/components/error-state';
import ContextLibraryView from './ContextLibraryView';
import SkillEditorSession from '../editor/SkillEditorSession';
import { useAuthoringOperation, AuthoringError } from '../editor/HotfixHeadReview';

function Library({ scopeKey, state }) {
  const [filters, setFilters] = useState({ page: 1, pageSize: 25, sort: 'updated', order: 'desc' });
  const query = useContexts(filters);
  const create = useCreateContext();
  const navigate = useNavigate();
  return <ContextLibraryView scopeKey={scopeKey} query={{ ...query, scopeKey }} filters={filters} onFilterChange={setFilters} state={state}
    onSkills={() => navigate(paths.dashboard.skills.root)} onOpen={(context) => navigate(paths.dashboard.skills.contextDetail(context.id))}
    importActions={{ onCreate: (input, { idempotencyKey }) => create.mutateAsync({ ...input, idempotencyKey }), onCreated: (receipt) => { if (receipt.contextId) navigate(paths.dashboard.skills.contextEdit(receipt.contextId)); } }} />;
}
Library.propTypes = { scopeKey: PropTypes.string.isRequired, state: PropTypes.string };

function Editor({ scopeKey, contextId }) {
  const detail = useContextDetail(contextId);
  const draft = useContextDraft(contextId);
  const start = useStartContextDraft(contextId);
  const save = useSaveContextDraft(contextId);
  const discard = useDiscardContextDraft(contextId);
  const checkpoint = useCheckpointDraft('agent_context', contextId);
  const [initial, setInitial] = useState(null);
  const operation = useAuthoringOperation(scopeKey);
  const [params] = useSearchParams();
  const from = params.get('from');
  const origin = from === null ? null : /^[1-9]\d*$/.test(from) && Number.isSafeInteger(Number(from)) ? Number(from) : undefined;
  // Initialization alone owns latching. Background query refetches cannot replace
  // a live editor; scope/resource changes remount this controller in its parent.
  const differentOrigin = from !== null && draft.data && draft.data.baseVersion !== origin;
  if (!initial && draft.data && !differentOrigin && !draft.isFetching && !draft.isError) setInitial(draft.data);
  if (!detail.data || (!initial && (draft.isPending || draft.isFetching))) return detail.isError ? <ErrorState title="Context unavailable" onRetry={detail.refetch} /> : <ContentSkeleton label="Loading your context draft" />;
  if (!initial && differentOrigin) return <Stack spacing={1.5}><Alert severity="warning">Your existing draft has a different content origin. It was not replaced. Resume and preserve it before starting another version.</Alert><Button variant="contained" onClick={() => setInitial(draft.data)}>Resume existing draft</Button></Stack>;
  if (initial) return <SkillEditorSession kind="agent_context" scopeKey={scopeKey} skill={{ ...detail.data.context, permissions: detail.data.releaseState.permissions }} initialDraft={initial} refetchDraft={draft.refetch}
    resourceApi={{
      saveDraft: save.mutateAsync,
      saveInput: (value, revision) => ({ revision, baseVersion: value.baseVersion, files: value.files, description: value.description ?? value.contextSpec.description, note: value.note }),
      discardDraft: discard.mutateAsync,
      ...(checkpointsReady('agent_context') ? { checkpointDraft: (input, { idempotencyKey }) => checkpoint.mutateAsync({ ...input, idempotencyKey }) } : {}),
    }} />;
  // Only the specific missing-draft code permits starting. A scope/permission
  // failure must never be converted into an empty draft or a latest-version edit.
  const absent = draft.error?.response?.data?.code === 'draft_not_found';
  if (!absent) return <ErrorState title="Draft unavailable" detail="No empty-draft assumption was made. Retry the authorized read." onRetry={draft.refetch} />;
  const canStart = origin !== undefined && (origin !== null || detail.data.context.latestVersion === null);
  return <Stack spacing={1.5}>
    <Alert severity="info">{canStart ? `Start from ${origin === null ? 'a blank unpublished context' : `the exact viewed version v${origin}`}? No latest-version substitution is made.` : 'Choose an explicit published version on the context detail page before starting a draft.'}</Alert>
    <AuthoringError error={operation.error} />
    <Button variant="contained" disabled={!canStart || operation.pending} onClick={async () => {
      const result = await operation.run((input, options) => start.mutateAsync({ ...input, ...options }), { revision: 0, fromVersion: origin });
      if (result) setInitial(result);
    }}>Start selected draft</Button>
  </Stack>;
}
Editor.propTypes = { scopeKey: PropTypes.string.isRequired, contextId: PropTypes.string.isRequired };

export default function ContextAuthoringController({ mode }) {
  const access = useSkillV11Scope();
  const { contextId } = useParams();
  const [params] = useSearchParams();
  if (!access.enabled || !contextAuthoringReady()) return <ErrorState title="Agent contexts unavailable" detail="The human authoring contract is not enabled in this deployment. Existing Skills are unchanged; no consume credential or leaf API is substituted." />;
  const scopeKey = `${access.identity}:agent_context:${contextId || 'library'}`;
  return <Stack sx={{ p: { xs: 2, md: 3 }, minWidth: 0, minHeight: 0, flex: 1 }}>
    {mode === 'library' ? <Library key={scopeKey} scopeKey={scopeKey} state={params.get('state')} /> : <Editor key={scopeKey} scopeKey={scopeKey} contextId={contextId} />}
  </Stack>;
}
ContextAuthoringController.propTypes = { mode: PropTypes.oneOf(['library', 'editor']).isRequired };
