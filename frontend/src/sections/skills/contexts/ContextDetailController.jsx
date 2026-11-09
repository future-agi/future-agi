import { useEffect, useState } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { Alert, Button, Stack } from '@mui/material';
import { useSkillV11Scope } from 'src/api/skills/testing';
import { useContextDetail } from 'src/api/skills/context-authoring';
import { useContextVersions, useContextInspection, useContextInspectionFile, useContextConsumers } from 'src/api/skills/context-production';
import { paths } from 'src/routes/paths';
import ContextDetailView from './ContextDetailView';
import ContextConsumersView from './ContextConsumersView';
import ContextUsePanel from './ContextUsePanel';

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
  const consumers = useContextConsumers(contextId, { page: consumerPage, windowDays });
  const scopeKey = `${access.identity}:agent_context:${contextId}`;
  const scoped = (result) => ({ ...result, scopeKey });
  const edit = (viewed) => navigate(`${paths.dashboard.skills.contextEdit(contextId)}?from=${viewed.number}`);
  const selectedVersion = version.data?.version;
  const versionRows = versions.data?.results || [];
  const allChoices = selectedVersion && !versionRows.some((row) => row.id === selectedVersion.id) ? [selectedVersion, ...versionRows] : versionRows;
  const deliveryState = version.isError ? 'error' : version.isLoading ? 'loading' : version.data && !version.data.distributionBlocked ? 'ready' : 'unavailable';
  return <Stack spacing={1.5} sx={{ p: { xs: 2, md: 3 }, minWidth: 0 }}>
    {requested !== null && number === null && <Alert severity="error">Choose a positive, explicit release number. No latest-version substitution was made.</Alert>}
    {!query.data?.context.latestVersion && query.data && !query.data.ownDraft && <Button variant="contained" onClick={() => navigate(paths.dashboard.skills.contextEdit(contextId))}>Start unpublished draft</Button>}
    <ContextDetailView scopeKey={scopeKey} query={scoped(query)} versionQuery={scoped(version)} fileQuery={scoped(file)} selectedPath={path} onSelectPath={selectPath}
      versions={allChoices} onSelectVersion={(value) => { selectPath('AGENTS.md'); setParams((current) => { const next = new URLSearchParams(current); next.set('version', String(value)); return next; }); }}
      onEditViewed={edit} onResumeDraft={() => navigate(paths.dashboard.skills.contextEdit(contextId))} state={params.get('state')}
      sections={{
        versions: <Stack spacing={1}><Alert severity="info">Choose an exact release above. Editing does not move production.</Alert><Stack direction="row" spacing={1}><Button disabled={versionPage <= 1} onClick={() => setVersionPage((page) => page - 1)}>Previous releases</Button><Button disabled={!versions.data || versionPage >= versions.data.totalPages} onClick={() => setVersionPage((page) => page + 1)}>Older releases</Button></Stack></Stack>,
        consumers: <ContextConsumersView scopeKey={scopeKey} state={consumers.isLoading ? 'loading' : consumers.isError ? 'error' : consumers.data ? 'ready' : 'unavailable'} data={consumers.data} windowDays={windowDays} onWindowChange={(days) => { setWindowDays(days); setConsumerPage(1); }} onPageChange={setConsumerPage} onRetry={consumers.refetch} />,
        use: <ContextUsePanel scopeKey={scopeKey} state={deliveryState} snapshot={version.data?.snapshot} origin={window.location.origin} organizationId={access.scope.organizationId} workspaceId={access.scope.workspaceId} onRetry={version.refetch} />,
      }} />
  </Stack>;
}
export default function ContextDetailController() {
  const access = useSkillV11Scope();
  const { contextId } = useParams();
  return <Detail key={`${access.identity}:${contextId}`} />;
}
