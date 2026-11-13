import { useState } from 'react';
import PropTypes from 'prop-types';
import { Link as RouterLink, useSearchParams } from 'react-router-dom';
import { Box, Button, Link, MenuItem, Stack, Tab, TextField, Typography } from '@mui/material';
import ContentSkeleton from 'src/components/content-skeleton';
import ErrorState from 'src/components/error-state';
import EmptyState from 'src/components/empty-state';
import StatusBadge from 'src/components/status-badge';
import { CustomTabs, SegmentedTabs } from 'src/components/tabs/tabs';
import { paths } from 'src/routes/paths';
import ObjectHeader from '../components/ObjectHeader';
import IdentityChip from '../components/IdentityChip';
import FileTree from '../components/FileTree';
import SkillMarkdownPreview from '../components/SkillMarkdownPreview';
import ContextIdentityStrip from './ContextIdentityStrip';
import ContextLinksPanel from './ContextLinksPanel';

// URL owns section identity; version/file loading never replaces the object shell.
const TABS = [['context', 'Instructions'], ['links', 'Linked skills'], ['tests', 'Tests'], ['versions', 'Versions'], ['consumers', 'Consumers'], ['recovery', 'Recovery'], ['use', 'Use']];
function Detail({ scopeKey, query, versionQuery, fileQuery, selectedPath = 'AGENTS.md', onSelectPath, onSelectVersion, versions = [], onEditViewed, onResumeDraft, onReviewDraft, onPreserveDraft, onStartDraft, sections = {}, state, initialTab }) {
  const [params, setParams] = useSearchParams();
  const [fileMode, setFileMode] = useState(() => { try { return localStorage.getItem('context-file-view') === 'source' ? 'source' : 'preview'; } catch { return 'preview'; } });
  const tabs = TABS.filter(([value]) => ['context', 'links'].includes(value) || sections[value]);
  const requestedTab = params.get('tab') || initialTab || 'context';
  const available = tabs.some(([value]) => value === requestedTab);
  const tab = available ? requestedTab : 'context';
  const selectTab = (value) => setParams((current) => { const next = new URLSearchParams(current); next.set('tab', value); return next; });
  const scoped = [query, versionQuery, fileQuery].every((item) => item.scopeKey === scopeKey);
  if (!scoped || (!query.data && query.isLoading)) return <ContentSkeleton variant="page" />;
  if (state === 'denied' || [401, 403].includes(query.error?.response?.status ?? query.error?.statusCode ?? query.error?.status ?? query.error?.httpStatus)) return <Stack spacing={1.5}><ErrorState title="You can't open this context" detail="It may belong to another workspace, or your access changed." /><Button component={RouterLink} to={paths.dashboard.skills.contexts} variant="contained" sx={{ alignSelf: 'flex-start' }}>Back to agent contexts</Button></Stack>;
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
  const editPrimary = ['context', 'links'].includes(tab);
  return <Stack spacing={1.5} sx={{ minWidth: 0 }}>
    <ObjectHeader crumbs={[{ label: 'Agent contexts', to: paths.dashboard.skills.contexts }, { label: context.name }]} title={context.name} purpose={descriptor?.description || context.description}
      actions={<Stack spacing={0.5}>
        <Stack direction="row" useFlexGap spacing={1} sx={{ flexWrap: 'wrap' }}>
          {query.data.ownDraft && <Button variant={editPrimary ? 'contained' : 'outlined'} disabled={!onResumeDraft} onClick={onResumeDraft}>Resume your draft</Button>}
          {query.data.ownDraft && onReviewDraft && <Button variant="outlined" onClick={onReviewDraft}>Review saved draft</Button>}
          {noRelease && !query.data.ownDraft && <Button variant="contained" disabled={!onStartDraft} onClick={onStartDraft}>Start draft</Button>}
          {!noRelease && <Button variant={!query.data.ownDraft && editPrimary ? 'contained' : 'outlined'} disabled={!viewed || !onEditViewed || (Boolean(query.data.ownDraft) && !onPreserveDraft) || versionQuery.isLoading || Boolean(versionQuery.error)} onClick={() => query.data.ownDraft ? onPreserveDraft?.(viewed) : onEditViewed(viewed)}>Edit viewed {viewed ? `v${viewed.number}` : 'version'}</Button>}
        </Stack>
        {query.data.ownDraft && viewed && <Typography variant="s3" color="text.secondary">Editing {viewed ? `v${viewed.number}` : 'a release'} first saves and suspends your current draft.</Typography>}
      </Stack>}
      identity={<ContextIdentityStrip viewed={viewed} latestVersion={context.latestVersion} productionVersion={context.labels?.find((label) => label.name === 'production')?.version} draft={query.data.ownDraft}
        releaseControl={noRelease ? <Typography variant="s3">No releases yet</Typography> : <TextField select size="small" value={viewed?.number || ''} disabled={!onSelectVersion || versionQuery.isLoading || Boolean(versionQuery.error)} SelectProps={{ displayEmpty: true, inputProps: { 'aria-label': 'Viewed release' } }} onChange={(event) => onSelectVersion(Number(event.target.value))} sx={{ minWidth: 88 }}>
          {!viewed && <MenuItem value="">{versionQuery.error ? 'Release unavailable' : 'Loading release…'}</MenuItem>}{versions.map((version) => <MenuItem key={version.id} value={version.number}>v{version.number}</MenuItem>)}
        </TextField>}
        extra={<>
          {sections.consumers && <Link component="button" variant="s3" color="text.secondary" underline="hover" onClick={() => selectTab('consumers')}>Delivery: see Consumers</Link>}
          {(query.isFetching || versionQuery.isFetching) && !versionQuery.isLoading && <StatusBadge status="checking" label="Updating…" />}
        </>} />}
      tabs={<CustomTabs value={tab} variant="scrollable" scrollButtons="auto" allowScrollButtonsMobile onChange={(_, value) => selectTab(value)} aria-label="Context detail sections">
        {tabs.map(([value, label]) => <Tab key={value} value={value} label={label} id={`context-tab-${value}`} aria-controls={`context-panel-${value}`} />)}
      </CustomTabs>} />
    {!available && <Typography variant="s3" color="text.secondary">That section isn&apos;t available here. Showing Instructions.</Typography>}
    <Box role="tabpanel" id={`context-panel-${tab}`} aria-labelledby={`context-tab-${tab}`} sx={{ minWidth: 0 }}>
      {versionQuery.isLoading || state === 'loading' ? <ContentSkeleton variant="panel" /> : versionQuery.error || state === 'error' ? <ErrorState title={`Couldn't load ${params.get('version') ? `v${params.get('version')}` : 'this release'}`} detail="The release request failed. Check your connection and try again." onRetry={versionQuery.refetch} /> : noRelease || state === 'empty' ? <EmptyState title="No published files" description="Publish your draft to inspect its instructions and locked skills." /> : <>
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
