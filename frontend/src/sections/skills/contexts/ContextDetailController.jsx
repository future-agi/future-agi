import { Suspense, lazy, useEffect, useState } from 'react';
import PropTypes from 'prop-types';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Alert, Stack } from '@mui/material';
import ContentSkeleton from 'src/components/content-skeleton';
import { useSkillV11Scope } from 'src/api/skills/testing';
import { useContextDetail, useContextDraft } from 'src/api/skills/context-authoring';
import { useContextBindings } from 'src/api/skills/context-consumers';
import ContextVersionsTable from './ContextVersionsTable';
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
import { useSkillsAccess } from '../utils/access';
import ContextRuntimeView from '../runtime/ContextRuntimeView';
import CheckTracing from '../runtime/CheckTracing';
import { useSkillTracesEnabled } from '../runtime/skillTracesFlag';

// These panels design their own ?state= previews; a panel state never replaces the whole detail (header actions stay live).
const PANEL_STATE_TABS = ['tests', 'consumers', 'recovery'];
const PREVIEW_STATES = ['loading', 'error', 'empty', 'off', 'denied'];
import { publicApiOrigin } from '../utils/origin';

// The fetch view (and its chart library) loads only when the flagged Activity tab opens.
const UsageTab = lazy(() => import('../detail/UsageTab'));

// Check tracing below the context install instructions (SKILL-TRACES §7.4 "Install",
// UX-14): waits for the next `context.load`. Viewers see it disabled with the reason (§7.7).
// Mounted only with `skillTraces` on.
function ContextCheckTracing({ contextId, name }) {
  const { isViewer } = useSkillsAccess();
  return <CheckTracing kind="agent_context" id={contextId} name={name} canCheck={!isViewer} />;
}
ContextCheckTracing.propTypes = { contextId: PropTypes.string.isRequired, name: PropTypes.string.isRequired };

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
  const [installViewedId, setInstallViewedId] = useState(null);
  const traces = useSkillTracesEnabled();
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
  const productionNumber = query.data?.context.labels?.find((label) => label.name === 'production')?.version;
  const useViewed = Boolean(selectedVersion?.id && installViewedId === selectedVersion.id);
  const installNumber = useViewed || productionNumber == null ? number : productionNumber;
  const install = useContextInspection(contextId, installNumber);
  const versionRows = versions.data?.results || [];
  const allChoices = selectedVersion && !versionRows.some((row) => row.id === selectedVersion.id) ? [selectedVersion, ...versionRows] : versionRows;
  const api = publicApiOrigin();
  const inspectInstall = (tab) => {
    selectPath('AGENTS.md');
    setParams((current) => { const next = new URLSearchParams(current); next.set('tab', tab); next.set('version', String(installNumber)); return next; });
  };
  const deliveryState = install.isError ? 'error' : install.isLoading ? 'loading' : install.data && !install.data.distributionBlocked ? 'ready' : 'unavailable';
  // Opening another tab from inside the Runtime tab drops the move-card parameters.
  const openTab = (tab) => setParams((current) => { const next = new URLSearchParams(current); next.set('tab', tab); next.delete('move'); next.delete('window'); return next; });
  const contextName = query.data?.context?.name;
  // Flagged Stream A sections (§7.4, §16 UXF-07). "Roll back to vN" on the release card opens
  // the existing Rollback tab, which keeps the reviewed rollback request; nothing moves here.
  const tracesSections = traces && contextName ? {
    runtime: <ContextRuntimeView context={{ id: contextId, name: contextName }} versionId={selectedVersion?.id} onRollback={() => openTab('recovery')} onCheckTracing={() => openTab('use')} />,
    usage: <Suspense fallback={<ContentSkeleton variant="panel" label="Loading usage" />}><UsageTab kind="agent_context" skill={{ id: contextId, name: contextName, latestVersion: query.data?.context?.latestVersion ?? undefined }} onInstall={() => openTab('use')} /></Suspense>,
  } : {};
  return <Stack spacing={1.5} sx={{ p: { xs: 2, md: 3 }, minWidth: 0 }}>
    {requested !== null && number === null && <Alert severity="error">Choose a positive, explicit release number. No latest-version substitution was made.</Alert>}
    <ContextDetailView initialTab={params.get('tab')} scopeKey={scopeKey} query={scoped(query)} versionQuery={scoped(version)} fileQuery={scoped(file)} selectedPath={path} onSelectPath={selectPath}
      versions={allChoices} onSelectVersion={(value) => { selectPath('AGENTS.md'); setParams((current) => { const next = new URLSearchParams(current); next.set('version', String(value)); return next; }); }}
      onStartDraft={() => navigate(paths.dashboard.skills.contextEdit(contextId))} onEditViewed={edit} onPreserveDraft={edit} onResumeDraft={() => navigate(paths.dashboard.skills.contextEdit(contextId))} onReviewDraft={contextReviewReady() ? () => navigate(paths.dashboard.skills.contextReview(contextId)) : undefined} state={PANEL_STATE_TABS.includes(params.get('tab')) ? undefined : params.get('state')}
      sections={{
        ...tracesSections,
        tests: <ContextTestsPanel contextName={query.data?.context?.name} contextId={contextId} version={selectedVersion} versions={allChoices} skills={contextSkillChoices(version.data?.descriptor)} productionVersion={productionVersion} seed={testSeed} runHandoff={params.get('tests') === 'run'}
          canWrite={!query.isError && query.data?.releaseState.permissions.canPublish === true} state={params.get('state')} draft={testingDraft ? draft.data : undefined}
          source={{ testingDraft, draftAvailable: Boolean(query.data?.ownDraft), releaseAvailable: Boolean(selectedVersion), isLoading: testingDraft && draft.isLoading, isError: testingDraft && draft.isError, onRetry: draft.refetch,
            onSelect: (useDraft) => setParams((current) => { const next = new URLSearchParams(current); if (useDraft) next.set('draft', '1'); else next.delete('draft'); return next; }) }} />,
        versions: <ContextVersionsTable viewedNumber={selectedVersion?.number} latestVersion={query.data?.context.latestVersion} contextId={contextId} query={versions} productionVersion={query.data?.context.labels?.find((label) => label.name === 'production')?.version} page={versionPage} onPageChange={setVersionPage} onSelectVersion={(value) => { selectPath('AGENTS.md'); setParams((current) => { const next = new URLSearchParams(current); next.set('version', String(value)); return next; }); }} />,
        consumers: <Stack spacing={1.5}><ContextConsumersView registrationAction={<ContextTargetRegistration contextId={contextId} canRegister={!query.isError && query.data?.releaseState.permissions.canPublish === true} />} scopeKey={scopeKey} state={params.get('tab') === 'consumers' && PREVIEW_STATES.includes(params.get('state')) ? params.get('state') : consumers.isLoading ? 'loading' : consumers.isError ? 'error' : consumers.data ? 'ready' : 'unavailable'} data={consumers.data} windowDays={windowDays} onWindowChange={(days) => { setWindowDays(days); setConsumerPage(1); }} onPageChange={setConsumerPage} onRetry={consumers.refetch} versionNumbers={Object.fromEntries(allChoices.map((row) => [row.id, row.number]))} /></Stack>,
        // J6: incident recovery choice and local/application recovery guidance.
        recovery: query.data?.context ? <J6RecoveryPanel contextId={contextId} contextName={query.data.context.name} /> : null,
        use: <Stack spacing={2}>
          <ContextUsePanel scopeKey={scopeKey} state={deliveryState} snapshot={install.data?.snapshot} productionVersion={productionNumber} viewingVersion={number} useViewed={useViewed} onUseViewed={(enabled) => setInstallViewedId(enabled ? selectedVersion?.id : null)} origin={api.origin} originError={api.error} organizationId={access.scope.organizationId} workspaceId={access.scope.workspaceId} onRetry={install.refetch} onViewFiles={() => inspectInstall('context')} onOpenLinks={() => inspectInstall('links')} runtimeAccess={deliveryState === 'ready' && install.data?.snapshot && <ContextPilotCredentials key={`${scopeKey}:${install.data.snapshot.versionId}`} contextId={contextId} snapshot={install.data.snapshot} canIssue={query.data?.releaseState.permissions.canIssueCredentials === true} />} />
          {query.data?.context && version.data?.descriptor?.links?.length > 0 && <ContextMigrationExport contextName={query.data.context.name} links={version.data.descriptor.links} />}
          {traces && contextName && <ContextCheckTracing contextId={contextId} name={contextName} />}
        </Stack>,
      }} />
  </Stack>;
}
export default function ContextDetailController() {
  const access = useSkillV11Scope();
  const { contextId } = useParams();
  return <Detail key={`${access.identity}:${contextId}`} />;
}
