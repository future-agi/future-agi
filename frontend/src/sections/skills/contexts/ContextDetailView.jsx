import { useState } from 'react';
import PropTypes from 'prop-types';
import { Link as RouterLink, useSearchParams } from 'react-router-dom';
import { Box, Button, Link, MenuItem, Skeleton, Stack, Tab, TextField, Typography, useMediaQuery } from '@mui/material';
import { useTheme } from '@mui/material/styles';
import ContentSkeleton from 'src/components/content-skeleton';
import ErrorState from 'src/components/error-state';
import EmptyState from 'src/components/empty-state';
import StatusBadge from 'src/components/status-badge';
import { CustomTabs, SegmentedTabs } from 'src/components/tabs/tabs';
import { paths } from 'src/routes/paths';
import ObjectHeader from '../components/ObjectHeader';
import IdentityChip from '../components/IdentityChip';
import { reviewComposerUrl } from '../utils/review-links';
import { useSkillsAccess } from '../utils/access';
import ActionReason, { reasonProps } from '../components/ActionReason';
import FileTree from '../components/FileTree';
import SkillMarkdownPreview from '../components/SkillMarkdownPreview';
import ContextIdentityStrip from './ContextIdentityStrip';
import ContextLinksPanel from './ContextLinksPanel';

// URL owns section identity; version/file loading never replaces the object shell.
// Below sm the header actions share the title row (they wrap under it only when they cannot fit), so Edit adds no row.
const PHONE_TITLE_ROW = { '@media (max-width:599.95px)': { '& > .MuiStack-root:has(h1)': { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', columnGap: 1 }, '& > .MuiStack-root:last-of-type': { flex: '1 1 0', minWidth: 'min-content', justifyContent: 'flex-end' } } };
const TABS = [['context', 'Instructions'], ['links', 'Linked skills'], ['tests', 'Tests'], ['versions', 'Versions'], ['consumers', 'Consumers'], ['recovery', 'Rollback'], ['use', 'Install']];
function Detail({ scopeKey, query, versionQuery, fileQuery, selectedPath = 'AGENTS.md', onSelectPath, onSelectVersion, versions = [], onEditViewed, onResumeDraft, onReviewDraft, onPreserveDraft, onStartDraft, sections = {}, state, initialTab }) {
  const [params, setParams] = useSearchParams();
  const { isViewer, canWrite, ready } = useSkillsAccess();
  const theme = useTheme();
  const phone = useMediaQuery(theme.breakpoints.down('sm'));
  const [fileMode, setFileMode] = useState(() => { try { return localStorage.getItem('context-file-view') === 'source' ? 'source' : 'preview'; } catch { return 'preview'; } });
  const unpublished = query.data?.context?.latestVersion == null && !params.get('version');
  const tabs = TABS.filter(([value]) => unpublished ? value === 'context' : ['context', 'links'].includes(value) || sections[value]);
  const requestedTab = params.get('tab') || initialTab || 'context';
  const available = tabs.some(([value]) => value === requestedTab);
  const tab = available ? requestedTab : 'context';
  const selectTab = (value) => setParams((current) => { const next = new URLSearchParams(current); next.set('tab', value); return next; });
  const scoped = [query, versionQuery, fileQuery].every((item) => item.scopeKey === scopeKey);
  if (!scoped || (!query.data && query.isLoading)) return <ContentSkeleton variant="page" />;
  if (state === 'denied' || [401, 403].includes(query.error?.response?.status ?? query.error?.statusCode ?? query.error?.status ?? query.error?.httpStatus)) return <EmptyState icon="solar:lock-keyhole-linear" title="You can't open this context" description="It may belong to another workspace, or your access changed." action={<Button component={RouterLink} to={paths.dashboard.skills.contexts} variant="outlined">Back to agent contexts</Button>} />;
  const missing = (query.error?.response?.status ?? query.error?.statusCode ?? query.error?.status ?? query.error?.httpStatus) === 404 || (!query.error && !query.data?.context);
  if (missing) return <EmptyState title="Agent context not found" description="It may have been archived or belong to another workspace." action={<Button component={RouterLink} to={paths.dashboard.skills.contexts} variant="outlined">Back to Agent contexts</Button>} />;
  if (query.error) return <ErrorState title="Couldn't load this context" detail="Check your connection and try again." onRetry={query.refetch} />;
  const context = query.data?.context;
  if (!context) return <ErrorState title="Context not found" detail="Choose an authorized context in this workspace." />;
  const viewed = versionQuery.error || state === 'error' ? null : versionQuery.data?.version;
  const noRelease = context.latestVersion == null && !params.get('version');
  const descriptor = versionQuery.data?.descriptor;
  const manifest = versionQuery.data?.files || [];
  const selected = manifest.find((file) => file.path === selectedPath);
  const content = fileQuery.data?.path === selectedPath && fileQuery.data?.versionId === viewed?.id ? fileQuery.data.text : null;
  const openFile = (path) => { if (manifest.some((file) => file.path === path)) onSelectPath?.(path); };
  // The tab body owns the primary on these two, so the header never competes with it.
  const bodyPrimary = ['tests', 'recovery'].includes(tab);
  const cannotEdit = !canWrite || query.data.releaseState?.permissions?.canPublish === false;
  const editReason = isViewer ? "Viewers can't edit. Ask a workspace admin for edit access." : ready && cannotEdit ? "You don't have permission to edit this agent context. Ask a workspace admin for edit access." : '';
  const production = context.labels?.find((label) => label.name === 'production')?.version;
  const latest = query.data.latest || versions.find((row) => row.number === context.latestVersion);
  const latestReady = Boolean(latest?.id && latest?.digest && latest?.number != null);
  const newerPublished = production != null && latestReady && latest.number > production;
  // First release or a newer publication: one header action on every tab but Versions, where the row action is the control.
  const releasable = latestReady && (production == null || latest.number > production);
  const headerRelease = releasable && tab !== 'versions';
  const editContained = !headerRelease && !bodyPrimary;
  const releaseError = Boolean(versionQuery.error) || state === 'error';
  // Contexts edit from the viewed version (not from latest), so say so whenever the two differ.
  const behindLatest = !noRelease && viewed && context.latestVersion != null && viewed.number !== context.latestVersion;
  const originNote = behindLatest ? `Your draft starts from v${viewed.number} and won't include v${context.latestVersion}'s changes.` : '';
  const editFrom = (version) => (query.data.ownDraft ? onPreserveDraft?.(version) : onEditViewed(version));
  const editBlocked = cannotEdit || !onEditViewed || (Boolean(query.data.ownDraft) && !onPreserveDraft) || versionQuery.isLoading || Boolean(versionQuery.error);
  // Only offer the latest version when it carries the same fields the viewed version hands to the editor.
  const canEditLatest = behindLatest && latestReady && latest.number === context.latestVersion && viewed.id && viewed.digest && viewed.number != null;
  return <Stack spacing={{ xs: 0.5, sm: 1.5 }} sx={{ minWidth: 0 }}>
    <ObjectHeader sx={PHONE_TITLE_ROW} crumbs={[{ label: 'Agent contexts', to: paths.dashboard.skills.contexts }, { label: context.name }]} title={context.name} purpose={phone ? undefined : (descriptor?.description || context.description)}
      actions={<Stack direction="row" useFlexGap spacing={1} sx={{ flexWrap: 'wrap', justifyContent: { xs: 'flex-end', sm: 'flex-start' } }}>
        {query.data.ownDraft && <Button color="primary" variant={editContained ? 'contained' : 'outlined'} disabled={cannotEdit || !onResumeDraft} onClick={onResumeDraft}>Resume your draft</Button>}
        {query.data.ownDraft && onReviewDraft && <Button variant="outlined" onClick={onReviewDraft}>Review saved draft</Button>}
        {noRelease && !query.data.ownDraft && <Button color="primary" variant="contained" disabled={cannotEdit || !onStartDraft} onClick={onStartDraft}>Start draft</Button>}
        {!noRelease && <Button color="primary" variant={!query.data.ownDraft && editContained ? 'contained' : 'outlined'} {...reasonProps("context-edit-reason", editReason)} disabled={editBlocked || !viewed} onClick={() => editFrom(viewed)}>Edit viewed {viewed ? `v${viewed.number}` : 'version'}</Button>}
        {headerRelease && <Button component={RouterLink} color="primary" variant={bodyPrimary ? 'outlined' : 'contained'} to={reviewComposerUrl({ kind: 'agent_context', subjectId: context.id, name: context.name, candidateId: latest.id, number: latest.number, digest: latest.digest, label: 'production' })}>Request release review for v{latest.number}</Button>}
      </Stack>}
      identity={<Stack spacing={1}>
        {phone && (descriptor?.description || context.description) && <Typography variant="s3" color="text.secondary">{descriptor?.description || context.description}</Typography>}
        <ActionReason id="context-edit-reason" reason={editReason} sx={{ flexBasis: 'auto' }} />
        {originNote && !cannotEdit && <Stack direction="row" useFlexGap spacing={1} sx={{ flexWrap: 'wrap', alignItems: 'center' }}><Typography variant="s3" color="text.secondary">{originNote}</Typography>{canEditLatest && <Button variant="text" size="small" disabled={editBlocked} sx={{ ml: -1 }} onClick={() => editFrom(latest)}>Edit latest v{latest.number}</Button>}</Stack>}
        {query.data.ownDraft && viewed && <Typography variant="s3" color="text.secondary">Editing {viewed ? `v${viewed.number}` : 'a release'} first saves and suspends your current draft.</Typography>}
        <ContextIdentityStrip viewed={viewed} latestVersion={context.latestVersion} productionVersion={context.labels?.find((label) => label.name === 'production')?.version} draft={query.data.ownDraft}
        releaseControl={noRelease ? <Typography variant="s3">No releases yet</Typography> : <TextField select size="small" value={viewed?.number || ''} disabled={!onSelectVersion || versionQuery.isLoading || releaseError} SelectProps={{ displayEmpty: true, inputProps: { 'aria-label': 'Viewed release' } }} onChange={(event) => onSelectVersion(Number(event.target.value))} sx={{ minWidth: 88 }}>
          {!viewed && <MenuItem value="">{releaseError ? 'Release unavailable' : <Skeleton width={48} aria-label="Loading release" />}</MenuItem>}{versions.map((version) => <MenuItem key={version.id} value={version.number}>v{version.number}</MenuItem>)}
        </TextField>}
        extra={<>
          {sections.consumers && <Link component={RouterLink} to={`?${new URLSearchParams({ ...Object.fromEntries(params), tab: 'consumers' })}`} variant="s3" underline="always">Delivery: see Consumers</Link>}
          {(query.isFetching || versionQuery.isFetching) && !versionQuery.isLoading && <StatusBadge status="checking" label="Updating…" />}
        </>} />
      </Stack>}
      tabs={<CustomTabs value={tab} variant="scrollable" scrollButtons="auto" allowScrollButtonsMobile onChange={(_, value) => selectTab(value)} aria-label="Context detail sections">
        {tabs.map(([value, label]) => <Tab key={value} value={value} label={label} id={`context-tab-${value}`} aria-controls={`context-panel-${value}`} />)}
      </CustomTabs>} />
    {newerPublished && <Typography variant="s3" color="text.secondary">v{latest.number} published · Production is still v{production}.</Typography>}
    {!available && <Typography variant="s3" color="text.secondary">That section isn&apos;t available here. Showing Instructions.</Typography>}
    <Box role="tabpanel" id={`context-panel-${tab}`} aria-labelledby={`context-tab-${tab}`} sx={{ minWidth: 0 }}>
      {versionQuery.isLoading || state === 'loading' ? <ContentSkeleton variant="panel" /> : versionQuery.error || state === 'error' ? <ErrorState title={`Couldn't load ${params.get('version') ? `v${params.get('version')}` : 'this release'}`} detail="The release request failed. Check your connection and try again." onRetry={versionQuery.refetch} /> : noRelease || state === 'empty' ? <EmptyState title="No published files" description="Open your draft to review and publish it." action={onReviewDraft && query.data.ownDraft ? <Button variant="outlined" onClick={onReviewDraft}>Review saved draft</Button> : undefined} /> : <>
        {tab === 'context' && <Stack direction={{ xs: 'column', md: 'row' }} spacing={2} sx={{ minWidth: 0, minHeight: { md: 480 } }}>
          <Box sx={{ width: { xs: '100%', md: 240 }, flexShrink: 0, minWidth: 0 }}><FileTree files={manifest} pinned={['AGENTS.md']} generatedFolders={['.futureagi']} selectedPath={selectedPath} onSelect={openFile} readOnly /></Box>
          <Stack spacing={1.5} sx={{ flex: 1, minWidth: 0 }}>
            <Typography variant="m3" sx={{ overflowWrap: 'anywhere' }}>{selectedPath}</Typography>
            {selected?.origin?.kind === 'linked' && <Stack direction="row" useFlexGap spacing={1} sx={{ flexWrap: 'wrap' }}><Typography variant="s3" color="text.secondary">Read-only linked content · locked source version</Typography><IdentityChip value={selected.origin.versionId} kind="id" label="Source version" /></Stack>}
            {selected?.origin?.kind === 'generated' && <Typography variant="s3" color="text.secondary">Generated file · read-only. Records the update policies and locked skill versions.</Typography>}
            {/\.(md|markdown)$/i.test(selectedPath) && <SegmentedTabs value={fileMode} onChange={(_, value) => { setFileMode(value); try { localStorage.setItem('context-file-view', value); } catch { /* Viewer preference is optional. */ } }} aria-label="Context file view"><Tab value="source" label="Source" /><Tab value="preview" label="Preview" /></SegmentedTabs>}
            {fileQuery.isLoading ? <ContentSkeleton variant="panel" /> : fileQuery.error ? <ErrorState title="File unavailable" onRetry={fileQuery.refetch} /> : content == null ? <Typography variant="s3">Select a retained file. No content from another release is shown.</Typography> : fileMode === 'preview' && /\.(md|markdown)$/i.test(selectedPath) ? <SkillMarkdownPreview literal content={content} currentPath={selectedPath} onOpenPath={openFile} /> : <Box component="pre" tabIndex={0} aria-label={`${selectedPath} read-only source`} sx={{ m: 0, p: 1.5, bgcolor: 'background.paper', border: 1, borderColor: 'divider', borderRadius: 1, typography: 's1', fontFamily: 'monospace', whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{content}</Box>}
          </Stack>
        </Stack>}
        {tab === 'links' && <ContextLinksPanel scopeKey={`${scopeKey}:${viewed?.id}`} readOnly session={{ contextSpec: { links: (descriptor?.links || []).map((link) => ({ skillId: link.skillId, selector: link.requested })) }, resolvedLock: { links: descriptor?.links || [] } }} />}
        {sections[tab]}
      </>}
    </Box>
  </Stack>;
}
Detail.propTypes = { scopeKey: PropTypes.string.isRequired, query: PropTypes.object.isRequired, versionQuery: PropTypes.object.isRequired, fileQuery: PropTypes.object.isRequired, selectedPath: PropTypes.string, onSelectPath: PropTypes.func, onSelectVersion: PropTypes.func, versions: PropTypes.array, onEditViewed: PropTypes.func, onResumeDraft: PropTypes.func, onReviewDraft: PropTypes.func, onPreserveDraft: PropTypes.func, onStartDraft: PropTypes.func, sections: PropTypes.object, state: PropTypes.string, initialTab: PropTypes.string };
export default function ContextDetailView(props) { return <Detail key={props.scopeKey} {...props} />; }
ContextDetailView.propTypes = Detail.propTypes;
