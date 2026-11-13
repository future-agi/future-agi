import { useEffect, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Alert, Button, Stack } from '@mui/material';
import { useSkillV11Scope } from 'src/api/skills/testing';
import { useContextDetail, useContextDraft } from 'src/api/skills/context-authoring';
import { useContextBindings } from 'src/api/skills/context-consumers';
import ContextLabelRequestPanel from './ContextLabelRequestPanel';
import { contextReviewReady, useContextVersions, useContextInspection, useContextInspectionFile, useContextConsumers } from 'src/api/skills/context-production';
import { paths } from 'src/routes/paths';
import ContextDetailView from './ContextDetailView';
import ContextConsumersView from './ContextConsumersView';
import ContextUsePanel from './ContextUsePanel';
import ContextPilotCredentials from './ContextPilotCredentials';
import ContextMigrationExport from './ContextMigrationExport';
import ContextTestsPanel from './ContextTestsPanel';
import { contextSkillChoices } from './context-skill-choices';
import { productionBaseline } from './production-baseline';
import ContextTargetRegistration from './ContextTargetRegistration';
import J6RecoveryPanel from './J6RecoveryPanel';
import Section from 'src/components/section';
import TechnicalDetails from '../components/TechnicalDetails';
import { publicApiOrigin } from '../utils/origin';

// Intent: authors inspect one exact context before choosing a draft origin.
// Hierarchy: immutable identity first, delivery observations separate from use.
// Palette/depth/surfaces: existing neutral theme and flat divider surfaces.
// Typography/spacing: shared m3/s1/s3 variants and 12px control rhythm.
function Detail() {
  const access = useSkillV11Scope();
  const { contextId } = useParams();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const [path, selectPath] = useState('AGENTS.md');
  const [consumerPage, setConsumerPage] = useState(1);
  const [windowDays, setWindowDays] = useState(7);
  const [versionPage, setVersionPage] = useState(1);
  const query = useContextDetail(contextId);
  const testingDraft = params.get('draft') === '1';
  const draft = useContextDraft(contextId, { enabled: testingDraft });
  const versions = useContextVersions(contextId, versionPage);
  const requested = params.get('version');
  const number = requested === null ? query.data?.context.latestVersion : /^[1-9]\d*$/.test(requested) && Number.isSafeInteger(Number(requested)) ? Number(requested) : null;
  // Turn the initial latest selection into an explicit URL identity. Metadata
  // refetches may update the Latest strip, but never replace the viewed bytes.
  useEffect(() => {
    if (requested === null && Number.isSafeInteger(number) && number > 0) {
      setParams((current) => { const next = new URLSearchParams(current); next.set('version', String(number)); return next; }, { replace: true });
    }
  }, [requested, number, setParams]);
  const version = useContextInspection(contextId, number);
  const file = useContextInspectionFile(contextId, number, path, version.data?.version.id, version.data?.version.digest);
  // A hand-off from the capture review: reproduce the example just saved against its recorded version.
  const reproduce = params.get('reproduce');
  const reference = Number(params.get('reference'));
  const testSeed = reproduce ? { caseId: reproduce, ...(Number.isSafeInteger(reference) && reference > 0 ? { reference } : {}) } : undefined;
  // The application's production baseline: the production label, else its one live runtime binding.
  const bindings = useContextBindings(contextId);
  const productionVersion = productionBaseline(query.data?.context.labels, bindings.data?.results);
  const consumers = useContextConsumers(contextId, { page: consumerPage, windowDays });
  const scopeKey = `${access.identity}:agent_context:${contextId}`;
  const scoped = (result) => ({ ...result, scopeKey });
  const edit = (viewed) => navigate(`${paths.dashboard.skills.contextEdit(contextId)}?from=${viewed.number}`);
  const selectedVersion = version.data?.version;
  const versionRows = versions.data?.results || [];
  const allChoices = selectedVersion && !versionRows.some((row) => row.id === selectedVersion.id) ? [selectedVersion, ...versionRows] : versionRows;
  const api = publicApiOrigin();
  const selectTab = (tab) => setParams((current) => { const next = new URLSearchParams(current); next.set('tab', tab); return next; });
  const deliveryState = version.isError ? 'error' : version.isLoading ? 'loading' : version.data && !version.data.distributionBlocked ? 'ready' : 'unavailable';
  return <Stack spacing={1.5} sx={{ p: { xs: 2, md: 3 }, minWidth: 0 }}>
    {requested !== null && number === null && <Alert severity="error">Choose a positive, explicit release number. No latest-version substitution was made.</Alert>}
    {!query.data?.context.latestVersion && query.data && !query.data.ownDraft && <Button variant="contained" onClick={() => navigate(paths.dashboard.skills.contextEdit(contextId))}>Start unpublished draft</Button>}
    <ContextDetailView initialTab={params.get('tab')} scopeKey={scopeKey} query={scoped(query)} versionQuery={scoped(version)} fileQuery={scoped(file)} selectedPath={path} onSelectPath={selectPath}
      versions={allChoices} onSelectVersion={(value) => { selectPath('AGENTS.md'); setParams((current) => { const next = new URLSearchParams(current); next.set('version', String(value)); return next; }); }}
      onEditViewed={edit} onPreserveDraft={edit} onResumeDraft={() => navigate(paths.dashboard.skills.contextEdit(contextId))} onReviewDraft={contextReviewReady() ? () => navigate(paths.dashboard.skills.contextReview(contextId)) : undefined} state={params.get('state')}
      sections={{
        tests: <ContextTestsPanel contextId={contextId} version={selectedVersion} versions={allChoices} skills={contextSkillChoices(version.data?.descriptor)} productionVersion={productionVersion} seed={testSeed}
          canWrite={!query.isError && query.data?.releaseState.permissions.canPublish === true} state={params.get('state')} draft={testingDraft ? draft.data : undefined}
          source={{ testingDraft, draftAvailable: Boolean(query.data?.ownDraft), releaseAvailable: Boolean(selectedVersion), isLoading: testingDraft && draft.isLoading, isError: testingDraft && draft.isError, onRetry: draft.refetch,
            onSelect: (useDraft) => setParams((current) => { const next = new URLSearchParams(current); if (useDraft) next.set('draft', '1'); else next.delete('draft'); return next; }) }} />,
        versions: <Stack spacing={2}><ContextLabelRequestPanel contextId={contextId} version={selectedVersion} /><Alert severity="info">Choose an exact release above. Editing does not move production.</Alert><Stack direction="row" spacing={1}><Button disabled={versionPage <= 1} onClick={() => setVersionPage((page) => page - 1)}>Previous releases</Button><Button disabled={!versions.data || versionPage >= versions.data.totalPages} onClick={() => setVersionPage((page) => page + 1)}>Older releases</Button></Stack></Stack>,
        consumers: <Stack spacing={1.5}><ContextTargetRegistration contextId={contextId} canRegister={!query.isError && query.data?.releaseState.permissions.canPublish === true} /><ContextConsumersView scopeKey={scopeKey} state={consumers.isLoading ? 'loading' : consumers.isError ? 'error' : consumers.data ? 'ready' : 'unavailable'} data={consumers.data} windowDays={windowDays} onWindowChange={(days) => { setWindowDays(days); setConsumerPage(1); }} onPageChange={setConsumerPage} onRetry={consumers.refetch} versionNumbers={Object.fromEntries(allChoices.map((row) => [row.id, row.number]))} /></Stack>,
        // J6: incident recovery choice and local/application recovery guidance.
        recovery: query.data?.context ? <J6RecoveryPanel contextId={contextId} contextName={query.data.context.name} /> : null,
        use: <Stack spacing={2}>
          <ContextUsePanel scopeKey={scopeKey} state={deliveryState} snapshot={version.data?.snapshot} origin={api.origin} originError={api.error} organizationId={access.scope.organizationId} workspaceId={access.scope.workspaceId} onRetry={version.refetch} onViewFiles={() => selectTab('context')} onOpenLinks={() => selectTab('links')} />
          {deliveryState === 'ready' && version.data?.snapshot && <Section title="Runtime access"><ContextPilotCredentials key={`${scopeKey}:${version.data.snapshot.versionId}`} contextId={contextId} snapshot={version.data.snapshot} canIssue={query.data?.releaseState.permissions.canIssueCredentials === true} /></Section>}
          {query.data?.context && version.data?.descriptor?.links?.length > 0 && <TechnicalDetails title="Migration export"><ContextMigrationExport contextName={query.data.context.name} links={version.data.descriptor.links} /></TechnicalDetails>}
        </Stack>,
      }} />
  </Stack>;
}
export default function ContextDetailController() {
  const access = useSkillV11Scope();
  const { contextId } = useParams();
  return <Detail key={`${access.identity}:${contextId}`} />;
}
