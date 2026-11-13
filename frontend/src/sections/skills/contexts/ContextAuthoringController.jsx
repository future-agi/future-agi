import { useCallback, useState } from 'react';
import PropTypes from 'prop-types';
import { Link as RouterLink, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Alert, Button, Stack, Typography } from '@mui/material';
import { useSkillV11Scope } from 'src/api/skills/testing';
import { contextAuthoringReady, useContexts, useCreateContext, useContextDetail, useContextDraft, useStartContextDraft, useSaveContextDraft, useDiscardContextDraft, usePreviewContextDependencies, useApplyContextDependencies } from 'src/api/skills/context-authoring';
import { useCheckpointDraft, checkpointsReady } from 'src/api/skills/r3-intake';
import { paths } from 'src/routes/paths';
import { useContextSkillInventory } from 'src/api/skills/context-skill-inventory';
import { useContextDraftInventory, useContextDraftFile, usePreviewContextRelease, useAcknowledgeContextHead, contextReviewReady } from 'src/api/skills/context-production';
import ContentSkeleton from 'src/components/content-skeleton';
import CapabilityNotice from '../components/CapabilityNotice';
import ErrorState from 'src/components/error-state';
import ContextLibraryView from './ContextLibraryView';
import ContextDraftSwitch from './ContextDraftSwitch';
import ContextTestsPanel from './ContextTestsPanel';
import SkillEditorSession from '../editor/SkillEditorSession';
import { useAuthoringOperation, AuthoringError } from '../editor/HotfixHeadReview';

function Library({ scopeKey, state }) {
  const [filters, setFilters] = useState({ page: 1, pageSize: 25, sort: 'updated', order: 'desc' });
  const query = useContexts(filters);
  const create = useCreateContext();
  const navigate = useNavigate();
  const proposedLinks = useLocation().state?.proposedLinks;
  return <ContextLibraryView scopeKey={scopeKey} query={{ ...query, scopeKey }} filters={filters} onFilterChange={setFilters} state={state}
    onSkills={() => navigate(paths.dashboard.skills.root)} onOpen={(context) => navigate(paths.dashboard.skills.contextDetail(context.id))}
    importActions={{ proposedLinks: Array.isArray(proposedLinks) ? proposedLinks : undefined, onCreate: (input, { idempotencyKey }) => create.mutateAsync({ ...input, idempotencyKey }), onCreated: (receipt) => { if (receipt.contextId) navigate(paths.dashboard.skills.contextEdit(receipt.contextId), Array.isArray(proposedLinks) ? { state: { proposedLinks } } : undefined); } }} />;
}
Library.propTypes = { scopeKey: PropTypes.string.isRequired, state: PropTypes.string };

function Editor({ scopeKey, contextId }) {
  const detail = useContextDetail(contextId);
  const draft = useContextDraft(contextId);
  const skills = useContextSkillInventory();
  const [savedRevision, setSavedRevision] = useState(null);
  const [readOnlyPath, setReadOnlyPath] = useState(null);
  const inventory = useContextDraftInventory(contextId, savedRevision);
  const readOnlyFile = useContextDraftFile(contextId, savedRevision, readOnlyPath, inventory.data);
  const selectReadOnlyFile = useCallback((path) => setReadOnlyPath(path), []);
  const previewDependencies = usePreviewContextDependencies(contextId);
  const applyDependencies = useApplyContextDependencies(contextId);
  const previewHead = usePreviewContextRelease(contextId);
  const acknowledgeHead = useAcknowledgeContextHead(contextId);
  const navigate = useNavigate();
  const start = useStartContextDraft(contextId);
  const save = useSaveContextDraft(contextId);
  const discard = useDiscardContextDraft(contextId);
  const checkpoint = useCheckpointDraft('agent_context', contextId);
  const [initial, setInitial] = useState(null);
  const [switchDraft, setSwitchDraft] = useState(null);
  const operation = useAuthoringOperation(scopeKey);
  const [params] = useSearchParams();
  const proposedLinks = useLocation().state?.proposedLinks;
  const from = params.get('from');
  const origin = from === null ? null : /^[1-9]\d*$/.test(from) && Number.isSafeInteger(Number(from)) ? Number(from) : undefined;
  // Initialization alone owns latching. Background query refetches cannot replace
  // a live editor; scope/resource changes remount this controller in its parent.
  const differentOrigin = from !== null && draft.data && draft.data.baseVersion !== origin;
  if (switchDraft) return <ContextDraftSwitch scopeKey={scopeKey} draft={switchDraft.draft} targetVersion={switchDraft.origin}
    onSuspend={(input, options) => checkpoint.mutateAsync({ ...input, ...options })}
    onStart={(input, options) => start.mutateAsync({ ...input, ...options })}
    onStarted={(result) => { setInitial(result); setSwitchDraft(null); }} />;
  // "Repair from recorded vN" already names the origin and carries the intent to preserve the
  // current draft; the explicit checkpoint consent is still given in the preservation review.
  if (!initial && !switchDraft && differentOrigin && params.get('preserve') === '1' && Number.isSafeInteger(origin) && !draft.isFetching && checkpointsReady('agent_context')) { setSwitchDraft({ draft: draft.data, origin }); return null; }
  if (!initial && draft.data && !differentOrigin && !draft.isFetching && !draft.isError) setInitial(draft.data);
  if (!detail.data || (!initial && (draft.isPending || draft.isFetching))) return detail.isError ? <ErrorState title="Context unavailable" onRetry={detail.refetch} /> : <ContentSkeleton label="Loading your context draft" />;
  if (!initial && differentOrigin) return <Stack spacing={1.5}>
    <Alert severity="warning">Your existing draft has a different content origin. It was not replaced. Preserve it before starting the viewed version, or resume your existing work.</Alert>
    <Button color="primary" variant="contained" disabled={!Number.isSafeInteger(origin) || !checkpointsReady('agent_context')} onClick={() => setSwitchDraft({ draft: draft.data, origin })}>Review draft preservation</Button>
    <Button variant="outlined" onClick={() => setInitial(draft.data)}>Resume existing draft</Button>
  </Stack>;
  // Intent: inspect owned instructions and accepted dependency bytes together.
  // Hierarchy: editor stays mounted; inventory failures sit above it, never
  // masquerading as an empty tree. Palette/depth/surfaces: existing neutral
  // MUI theme and flat alerts. Typography/spacing: shared s1 and 12px groups.
  if (initial) return <Stack spacing={1.5} sx={{ minHeight: 0, flex: 1 }}>
    {skills.isLoading && <Alert severity="info">Loading all authorized published skills for linking…</Alert>}
    {skills.isError && <ErrorState title="Available skills could not be loaded" onRetry={skills.refetch} />}
    {inventory.isLoading && <Alert severity="info">Loading linked and generated files for the saved revision…</Alert>}
    {inventory.isError && <ErrorState title="Saved context files unavailable" onRetry={inventory.refetch} />}
    <SkillEditorSession kind="agent_context" scopeKey={scopeKey} skill={{ ...detail.data.context, permissions: detail.data.releaseState.permissions }} initialDraft={initial} refetchDraft={draft.refetch}
    onDraftRevisionChange={setSavedRevision}
    readOnlyFiles={inventory.isError ? [] : inventory.data?.files || []}
    readOnlyFileQuery={{ ...readOnlyFile, scopeKey }} onSelectReadOnlyFile={selectReadOnlyFile}
    onReview={contextReviewReady() ? () => navigate(paths.dashboard.skills.contextReview(contextId)) : undefined}
    contextControls={{
      renderTests: ({ session, onSave }) => <ContextTestsPanel contextId={contextId} canWrite={!detail.isError && detail.data.releaseState.permissions.canPublish === true}
        openSavedDraftDisabled={Boolean(session.mutationError) || session.pendingMutations > 0}
        onOpenSavedDraft={async () => { const saved = await onSave(); if (saved) navigate(`${paths.dashboard.skills.contextDetail(contextId)}?tab=tests&draft=1`); }} />,
      availableSkills: skills.isError ? [] : skills.data || [],
      proposedLinks: Array.isArray(proposedLinks) ? proposedLinks : undefined,
      previewDependencies: previewDependencies.mutateAsync,
      applyDependencies: (input, options) => applyDependencies.mutateAsync({ ...input, ...options }),
      previewHead: contextReviewReady() ? previewHead.mutateAsync : undefined,
      acknowledgeHead: (input, options) => acknowledgeHead.mutateAsync({ ...input, ...options }),
    }}
    resourceApi={{
      saveDraft: save.mutateAsync,
      saveInput: (value, revision) => ({ revision, baseVersion: value.baseVersion, files: value.files, description: value.description ?? value.contextSpec.description, note: value.note }),
      discardDraft: discard.mutateAsync,
      ...(checkpointsReady('agent_context') ? { checkpointDraft: (input, { idempotencyKey }) => checkpoint.mutateAsync({ ...input, idempotencyKey }) } : {}),
    }} /></Stack>;
  // Only the specific missing-draft code permits starting. A scope/permission
  // failure must never be converted into an empty draft or a latest-version edit.
  const absent = (draft.error?.code || draft.error?.response?.data?.code) === 'draft_not_found';
  if (!absent) return <ErrorState title="Draft unavailable" detail="No empty-draft assumption was made. Retry the authorized read." onRetry={draft.refetch} />;
  const canStart = origin !== undefined && (origin !== null || detail.data.context.latestVersion === null);
  const latest = detail.data.context.latestVersion;
  // A small decision, not a page: which version the draft starts from, stated once, with a way back.
  return <Stack spacing={1.5} sx={{ maxWidth: 560, p: 2, border: 1, borderColor: 'divider', borderRadius: 1 }}>
    <Typography variant="m3" component="h2">{!canStart ? 'Choose a version first' : origin === null ? 'Start a new draft?' : `Start a draft from v${origin}${latest && latest !== origin ? ` (latest is v${latest})` : ''}?`}</Typography>
    <Typography variant="s1" color="text.secondary">{canStart ? (origin === null ? 'The draft starts blank and stays private until you publish it.' : `Your draft starts from exactly v${origin}; it is never swapped for the latest version.`) : 'Choose an explicit published version on the context detail page before starting a draft.'}</Typography>
    <AuthoringError error={operation.error} />
    <Stack direction="row" spacing={1}>
      <Button color="primary" variant="contained" disabled={!canStart || operation.pending} onClick={async () => {
        const result = await operation.run((input, options) => start.mutateAsync({ ...input, ...options }), { revision: 0, fromVersion: origin });
        if (result) setInitial(result);
      }}>Start selected draft</Button>
      <Button color="primary" variant="outlined" component={RouterLink} to={paths.dashboard.skills.contextDetail(contextId)}>Cancel</Button>
    </Stack>
  </Stack>;
}
Editor.propTypes = { scopeKey: PropTypes.string.isRequired, contextId: PropTypes.string.isRequired };

export default function ContextAuthoringController({ mode }) {
  const access = useSkillV11Scope();
  const { contextId } = useParams();
  const [params] = useSearchParams();
  if (!access.enabled || !contextAuthoringReady()) return <CapabilityNotice title="Agent contexts aren't enabled for this workspace." description="Existing skills stay available. Ask a workspace admin about access." />;
  const scopeKey = `${access.identity}:agent_context:${contextId || 'library'}`;
  return <Stack sx={{ p: { xs: 2, md: 3 }, minWidth: 0, minHeight: 0, flex: 1 }}>
    {mode === 'library' ? <Library key={scopeKey} scopeKey={scopeKey} state={params.get('state')} /> : <Editor key={scopeKey} scopeKey={scopeKey} contextId={contextId} />}
  </Stack>;
}
ContextAuthoringController.propTypes = { mode: PropTypes.oneOf(['library', 'editor']).isRequired };
