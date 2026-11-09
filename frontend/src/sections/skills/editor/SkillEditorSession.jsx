import { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { useNavigate, useSearchParams } from "react-router-dom";
import { enqueueSnackbar } from "notistack";
import { Alert, Box, Button, IconButton, Stack, Tab, TextField, Typography } from "@mui/material";

import { useDiscardSkillDraft } from "src/api/skills";
import { useSkillTestingOptions } from "src/api/skills/testing";
import { paths } from "src/routes/paths";
import PageHeader from "src/components/page-header";
import ContentSkeleton from "src/components/content-skeleton";
import ErrorState from "src/components/error-state";
import Iconify from "src/components/iconify";
import { ConfirmDialog } from "src/components/custom-dialog";
import { SegmentedTabs } from "src/components/tabs/tabs";
import FileTree from "src/sections/skills/components/FileTree";
import ChecksList from "src/sections/skills/components/ChecksList";
import SkillMarkdownPreview from "src/sections/skills/components/SkillMarkdownPreview";
import EditorTestsPanel from '../tests/EditorTestsPanel';
import AssistPanel from '../assist/AssistPanel';
import ContextLinksPanel from '../contexts/ContextLinksPanel';
import HotfixHeadReview from './HotfixHeadReview';
import RecoverableDraftChanges from './RecoverableDraftChanges';
import DraftChecksPanel from "./DraftChecksPanel";
import DraftConflictDialog from "./DraftConflictDialog";
import PublishDialog from "./PublishDialog";
import SkillFileEditor from "./SkillFileEditor";
import SkillDocumentEditor from "./SkillDocumentEditor";
import useDraftSession from "./useDraftSession";
import useUnsavedNavigation from "./useUnsavedNavigation";

const contextReadOnly = (path) => /^(skills|\.futureagi)(\/|$)/i.test(path);
const contextReserved = (path) => contextReadOnly(path) || /(^|\/)(AGENTS|CLAUDE|GEMINI|SKILL)\.md$/i.test(path);

function EditorSession({ skill, initialDraft, refetchDraft, kind = 'skill', resourceApi, scopeKey = '', contextControls = {}, readOnlyFiles = [], readOnlyFileQuery, onSelectReadOnlyFile, onReview }) {
  const isContext = kind === 'agent_context';
  const root = isContext ? 'AGENTS.md' : 'SKILL.md';
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [panel, setPanel] = useState(params.get('tab') === 'tests' ? 'tests' : 'checks');
  const assistKind = !isContext && ['draft', 'description'].includes(params.get('assist')) ? params.get('assist') : null;
  const sourceRunId = params.get('run');
  const openAssist = (value, selection = {}) => setParams((previous) => {
    const next = new URLSearchParams(previous);
    next.set('assist', value);
    if (selection.sourceRunId) next.set('run', selection.sourceRunId);
    if (selection.caseId) next.set('case', selection.caseId);
    return next;
  });
  const closeAssist = () => setParams((previous) => { const next = new URLSearchParams(previous); next.delete('assist'); return next; });
  const [discardOpen, setDiscardOpen] = useState(false);
  const [conflictOpen, setConflictOpen] = useState(false);
  const [publishDraft, setPublishDraft] = useState(null);
  const [preparing, setPreparing] = useState(false);
  const [operationError, setOperationError] = useState(null);
  const [serverDraft, setServerDraft] = useState(null);
  const legacyDiscard = useDiscardSkillDraft(skill.id);
  const options = useSkillTestingOptions();
  const session = useDraftSession(skill.id, initialDraft, preparing || discardOpen || Boolean(publishDraft), resourceApi);
  const latestSession = useRef(session);
  const deletionCheckpoint = useRef(null);
  latestSession.current = session;
  const guard = useUnsavedNavigation(session.dirty || session.status === 'saving');
  const [selectedPath, setSelectedPath] = useState(root);
  const [mode, setMode] = useState('edit');
  const [target, setTarget] = useState(null);
  const locked = preparing || discardOpen || Boolean(publishDraft) || legacyDiscard.isPending;
  const saving = session.status === 'saving';
  const ownedFile = session.files.find((item) => item.path === selectedPath);
  const linked = isContext && !ownedFile && readOnlyFiles.find((item) => item.path === selectedPath);
  // A missing linked read must not display the owned root under another path.
  const isLinked = Boolean(linked);
  const linkedData = readOnlyFileQuery?.scopeKey === scopeKey ? readOnlyFileQuery.data : null;
  const linkedContent = linked && linkedData?.path === selectedPath && linkedData?.revision === session.revision ? linkedData.text : null;
  const file = ownedFile || (linked && typeof linkedContent === 'string' ? { path: selectedPath, content: linkedContent } : null);
  const fileReadOnly = locked || Boolean(linked) || (isContext && contextReadOnly(selectedPath));
  const isMarkdown = /\.(md|markdown)$/i.test(file?.path ?? '');
  const stale = skill.latestVersion > (session.baseVersion ?? 0);
  const detailPath = isContext ? paths.dashboard.skills.contextDetail(skill.id) : paths.dashboard.skills.detail(skill.id);
  const files = [...session.files, ...readOnlyFiles.filter((item) => !session.files.some((owned) => owned.path === item.path)).map((item) => ({ ...item, readOnly: true }))];

  useEffect(() => { if (session.status === 'conflict') setConflictOpen(true); }, [session.status]);
  useEffect(() => {
    if (isLinked) onSelectReadOnlyFile?.(selectedPath, session.revision);
  }, [isLinked, selectedPath, session.revision, onSelectReadOnlyFile]);

  const openFile = (path, line) => {
    if (!files.some((item) => item.path === path)) return;
    setSelectedPath(path); setTarget(line ? { line } : null);
    if (line) setMode('edit');
  };
  const startPublish = async () => {
    setPreparing(true); setOperationError(null);
    try {
      const saved = await session.flush();
      if (!saved) return;
      if (isContext) await onReview?.(saved);
      else setPublishDraft(saved);
    } catch { setOperationError('The review could not be opened. Your draft is retained; retry without changing its origin.'); }
    finally { setPreparing(false); }
  };
  const compareLatest = async () => {
    setPreparing(true);
    try {
      const saved = await session.flush();
      if (saved) guard.allow(() => navigate(`${paths.dashboard.skills.compare(skill.id)}?from=draft&to=${skill.latestVersion}`));
    } finally { setPreparing(false); }
  };
  const discardDraft = async () => {
    if (saving) return;
    setPreparing(true);
    try {
      if (isContext) await resourceApi.discardDraft({ revision: session.revision });
      else await legacyDiscard.mutateAsync();
      enqueueSnackbar('Draft discarded.', { variant: 'success' });
      guard.allow(() => navigate(detailPath));
    } catch { enqueueSnackbar("Couldn't discard the draft. Your edits are still here.", { variant: 'error' }); }
    finally { setPreparing(false); }
  };
  const deleteFile = async (path) => {
    if (locked || path === root || !session.files.some((item) => item.path === path) || (isContext && contextReadOnly(path))) return;
    if (isContext && !resourceApi.checkpointDraft) { setOperationError('Recovery checkpoints are unavailable. No file was deleted.'); return; }
    setPreparing(true); setOperationError(null);
    try {
      if (resourceApi?.checkpointDraft) {
        let sequence;
        const saved = await session.mutateDraft(async (draft) => {
          if (!/^sha256:[a-f0-9]{64}$/.test(draft.recoveryDigest || '')) throw new Error('Recovery identity unavailable');
          if (deletionCheckpoint.current?.path && deletionCheckpoint.current.path !== path) throw new Error('Recover the original deletion checkpoint first.');
          deletionCheckpoint.current ||= { path, idempotencyKey: crypto.randomUUID(), input: { revision: draft.revision, expectedDigest: draft.recoveryDigest, reason: 'deletion', suspend: false } };
          const retained = deletionCheckpoint.current;
          sequence = latestSession.current.editSequence;
          await resourceApi.checkpointDraft(retained.input, { idempotencyKey: retained.idempotencyKey });
          deletionCheckpoint.current = null;
          if (draft.revision !== retained.input.revision || draft.recoveryDigest !== retained.input.expectedDigest) throw new Error('Checkpoint recovered, but the draft changed. Review deletion again.');
          return draft;
        }, { operationKey: `checkpoint-delete:${path}` });
        if (!saved || latestSession.current.editSequence !== sequence) { setOperationError('Deletion was not applied. Preserve the latest edits and retry the recovery checkpoint.'); return; }
      }
      session.edit(latestSession.current.files.filter((item) => item.path !== path));
      if (selectedPath === path) setSelectedPath(root);
    } catch { setOperationError('The recovery checkpoint could not be confirmed. No file was deleted.'); }
    finally { setPreparing(false); }
  };
  const changeContent = (content) => {
    if (!fileReadOnly && ownedFile) session.edit(session.files.map((item) => item.path === ownedFile.path ? { ...item, content } : item));
  };
  const refreshConflict = async () => {
    setOperationError(null);
    try {
      const result = await refetchDraft();
      if (result.isError) throw result.error;
      setServerDraft(result.data);
    } catch { setOperationError('Could not load the current server draft. Your local edits are retained.'); }
  };
  let saveLabel = 'Saved';
  if (session.status === 'conflict') saveLabel = 'Conflict';
  else if (session.status === 'error') saveLabel = "Couldn't save";
  else if (session.dirty || saving) saveLabel = 'Saving…';
  else if (session.revision === 0) saveLabel = 'Not saved yet';

  return <Stack spacing={2} sx={{ flex: { xs: '1 0 auto', md: '1 1 0%' }, minHeight: 0 }}>
    <PageHeader title={<Box component="span" sx={{ fontFamily: 'monospace' }}>{skill.name}</Box>}
      description={isContext ? `Content origin ${session.baseVersion == null ? 'unpublished' : `v${session.baseVersion}`} · draft r${session.revision} · ${session.resolvedLock?.links?.length || 0} locked skills` : session.baseVersion === null ? 'New skill' : `Draft based on v${session.baseVersion}`}
      back={<IconButton aria-label={isContext ? 'Back to context' : 'Back to skill'} onClick={() => navigate(detailPath)}><Iconify icon="solar:arrow-left-linear" /></IconButton>}
      actions={<Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap', justifyContent: 'flex-end', alignItems: 'center' }}>
        <Typography role="status" variant="s3" color="text.secondary">{saveLabel}</Typography>
        {session.status === 'error' && <Button size="small" disabled={Boolean(session.mutationError)} onClick={() => session.save()}>Retry</Button>}
        {session.status === 'conflict' && <Button size="small" onClick={() => setConflictOpen(true)}>Resolve conflict</Button>}
        {!isContext && <Button variant="outlined" disabled={locked || !skill.permissions?.canRunTests || !options.data?.assistEnabled} onClick={() => openAssist('draft')}>Draft with AI</Button>}
        <Button variant="outlined" disabled={locked || saving} onClick={() => setDiscardOpen(true)}>Discard draft</Button>
        <Button variant="contained" disabled={locked || session.status === 'conflict' || session.status === 'error' || (isContext && !onReview)} onClick={startPublish}>{isContext ? 'Review changes' : 'Publish'}</Button>
      </Box>} sx={{ flexWrap: 'wrap', rowGap: 1.5, flexShrink: 0 }} />
    {operationError && <Alert severity="error">{operationError}</Alert>}
    {stale && <Alert severity="warning" sx={{ flexShrink: 0 }} action={!isContext && <Button color="inherit" disabled={locked || saving || session.status === 'conflict'} onClick={compareLatest}>Compare</Button>}>
      {isContext ? `The content origin remains v${session.baseVersion}. Latest is v${skill.latestVersion}; review it separately without copying its files.` : `v${skill.latestVersion} was published after you started. Compare your saved draft with the latest version before publishing.`}
    </Alert>}
    {isContext && <TextField label="Context purpose" size="small" value={session.description ?? session.contextSpec?.description ?? ''} onChange={(event) => session.editFields({ description: event.target.value })} disabled={locked} inputProps={{ maxLength: 1024 }} helperText="Versioned with the standing instructions and accepted skills." />}
    <Box sx={{ display: 'grid', flex: 1, minHeight: 0, gridTemplateColumns: { xs: 'minmax(0, 1fr)', md: '200px minmax(0, 1fr)', lg: '200px minmax(0, 1fr) 280px' }, gridTemplateRows: { xs: 'auto minmax(400px, 1fr) auto', md: 'minmax(0, 1fr) auto', lg: 'minmax(0, 1fr)' }, gap: 3 }}>
      <Box sx={{ minWidth: 0, minHeight: 0, maxHeight: { xs: 240, md: 'none' }, overflowY: 'auto' }}>
        <FileTree files={files} pinned={[root]} protectedPaths={[root]} isPathReadOnly={isContext ? contextReserved : undefined} selectedPath={selectedPath} onSelect={openFile} readOnly={locked}
          onCreate={(path) => {
            if (locked || (isContext && contextReserved(path)) || session.files.some((item) => item.path.toLowerCase() === path.toLowerCase())) return;
            session.edit([...session.files, { path, content: '' }]); setSelectedPath(path); setTarget(null); setMode('edit');
          }}
          onRename={(from, to) => {
            if (locked || from === root || (isContext && (contextReserved(from) || contextReserved(to))) || !session.files.some((item) => item.path === from) || files.some((item) => item.path.toLowerCase() === to.toLowerCase())) return;
            session.edit(session.files.map((item) => item.path === from ? { ...item, path: to } : item)); setSelectedPath(to);
          }} onDelete={deleteFile} />
      </Box>
      <Stack spacing={1.5} sx={{ minWidth: 0, minHeight: 0 }}>
        {linked && <Alert severity="info">Linked or generated content is read-only. Only the current saved draft revision is shown.</Alert>}
        {!file && (readOnlyFileQuery?.error ? <ErrorState title="File unavailable" onRetry={readOnlyFileQuery.refetch} /> : linked ? <ContentSkeleton label="Loading the saved context file" /> : <Alert severity="info">Select a file in this draft.</Alert>)}
        {isMarkdown && file.path !== 'SKILL.md' && <SegmentedTabs value={mode} onChange={(_, value) => setMode(value)} aria-label="Markdown view" sx={{ flexShrink: 0 }}><Tab value="edit" label={fileReadOnly ? 'Source' : 'Edit'} /><Tab value="preview" label="Preview" /></SegmentedTabs>}
        {file?.path === 'SKILL.md' && <SkillDocumentEditor file={file} target={target} readOnly={fileReadOnly} onOpenPath={openFile} onChange={changeContent} />}
        {file && file.path !== 'SKILL.md' && (isMarkdown && mode === 'preview' ? <Box sx={{ flex: 1, minHeight: 0, overflowY: 'auto' }}><SkillMarkdownPreview literal={isContext} content={file.content} currentPath={file.path} onOpenPath={openFile} /></Box> : <SkillFileEditor key={file.path} file={file} target={target} readOnly={fileReadOnly} onChange={changeContent} />)}
      </Stack>
      <Box sx={{ minWidth: 0, minHeight: 0, overflowY: 'auto', maxHeight: { xs: 320, md: 200, lg: 'none' }, gridColumn: { md: '1 / -1', lg: 'auto' } }}>
        <SegmentedTabs value={panel} onChange={(_, value) => setPanel(value)} aria-label="Draft review panels"><Tab value="checks" label="Checks" /><Tab value="tests" label="Tests" disabled={isContext && !contextControls.renderTests} /></SegmentedTabs>
        <Box sx={{ pt: 1.5 }} hidden={panel !== 'checks'}>{isContext ? <Stack spacing={1}>
          <Typography variant="s3">Checks cover the saved complete context, including accepted dependencies.</Typography>
          {session.dirty ? <Alert severity="info">For earlier content. Save and review the complete context again.</Alert> : session.checks ? <ChecksList checks={session.checks} onSelect={(check) => openFile(check.path, check.line)} dense /> : <Alert severity="info">Compiled checks are unavailable. Review is required before publication.</Alert>}
        </Stack> : <DraftChecksPanel files={session.files} name={skill.name} onSelect={(check) => openFile(check.path, check.line)} />}</Box>
        <Box sx={{ pt: 1.5 }} hidden={panel !== 'tests'}>{isContext ? contextControls.renderTests?.({ session, onSave: session.flush, onOpenFile: openFile }) : <EditorTestsPanel skill={skill} draft={session} isDirty={session.dirty} onSave={session.flush} onOpenFile={openFile} onImproveSelection={options.data?.assistEnabled ? (selection) => openAssist('description', selection) : undefined} />}</Box>
      </Box>
    </Box>
    {isContext && <Stack spacing={2} sx={{ flexShrink: 0 }}>
      <ContextLinksPanel scopeKey={scopeKey} session={session} availableSkills={contextControls.availableSkills} upstream={contextControls.upstream} onPreview={contextControls.previewDependencies} onApply={contextControls.applyDependencies} readOnly={locked} />
      <HotfixHeadReview scopeKey={scopeKey} session={session} onPreview={locked ? undefined : contextControls.previewHead} onAcknowledge={locked ? undefined : contextControls.acknowledgeHead} />
    </Stack>}
    {assistKind && !publishDraft && <Stack spacing={1.5}><Box><Button size="small" onClick={closeAssist}>Close assistance</Button></Box><AssistPanel skill={skill} draft={session} kind={assistKind} sourceRunId={sourceRunId} isDirty={session.dirty} onSave={session.flush} onApplied={session.reload} /></Stack>}
    {isContext && conflictOpen ? <Stack spacing={1.5}>
      <Button onClick={refreshConflict}>Load current server draft</Button>
      <RecoverableDraftChanges scopeKey={scopeKey} session={session} baseFiles={contextControls.baseFiles} serverDraft={serverDraft} onReload={(draft) => { session.reload(draft); setConflictOpen(false); }} />
    </Stack> : !isContext && <DraftConflictDialog open={conflictOpen} onClose={() => setConflictOpen(false)} refetch={refetchDraft} onOverwrite={session.save} onReload={(draft) => { if (draft) session.reload(draft); else guard.allow(() => navigate(detailPath)); }} />}
    <ConfirmDialog open={discardOpen} onClose={() => { if (!preparing && !legacyDiscard.isPending) setDiscardOpen(false); }} closeDisabled={preparing || legacyDiscard.isPending} title="Discard draft?" content="This removes your saved draft and unsaved edits. Published versions and labels will not change." action={<Button variant="contained" color="error" disabled={saving || preparing || legacyDiscard.isPending} onClick={discardDraft}>Discard draft</Button>} />
    <ConfirmDialog open={guard.blocked} onClose={guard.cancel} title="Leave with unsaved changes?" content="Your latest edits have not been saved. Stay to save them, or leave without those changes." action={<Button variant="contained" color="error" onClick={guard.proceed}>Leave without saving</Button>} />
    {publishDraft && !isContext && <PublishDialog open onClose={() => setPublishDraft(null)} onDraftChange={session.reload} skill={skill} draft={publishDraft} />}
  </Stack>;
}

const propTypes = {
  skill: PropTypes.shape({ id: PropTypes.string.isRequired, name: PropTypes.string.isRequired, latestVersion: PropTypes.number, permissions: PropTypes.object }).isRequired,
  initialDraft: PropTypes.shape({ files: PropTypes.array.isRequired, note: PropTypes.string.isRequired, revision: PropTypes.number.isRequired, baseVersion: PropTypes.number }).isRequired,
  refetchDraft: PropTypes.func.isRequired, kind: PropTypes.oneOf(['skill', 'agent_context']), resourceApi: PropTypes.object,
  scopeKey: PropTypes.string, contextControls: PropTypes.object, readOnlyFiles: PropTypes.array,
  readOnlyFileQuery: PropTypes.object, onSelectReadOnlyFile: PropTypes.func, onReview: PropTypes.func,
};
EditorSession.propTypes = propTypes;

export default function SkillEditorSession(props) {
  if (props.kind === 'agent_context' && (!props.scopeKey || !props.resourceApi?.saveDraft || !props.resourceApi?.saveInput || !props.resourceApi?.discardDraft)) return <ErrorState title="Context authoring unavailable" detail="A scoped context API is required. Legacy skill operations cannot edit an agent context." />;
  return <EditorSession key={`${props.scopeKey || ''}:${props.kind || 'skill'}:${props.skill.id}`} {...props} />;
}
SkillEditorSession.propTypes = propTypes;
