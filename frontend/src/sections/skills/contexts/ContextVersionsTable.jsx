import { useState } from 'react';
import PropTypes from 'prop-types';
import { useSearchParams } from 'react-router-dom';
import { IconButton, ListItemIcon, Menu, MenuItem, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Typography } from '@mui/material';
import ContentSkeleton from 'src/components/content-skeleton';
import { DataTablePagination } from 'src/components/data-table';
import ErrorState from 'src/components/error-state';
import EmptyState from 'src/components/empty-state';
import Iconify from 'src/components/iconify';
import StatusBadge from 'src/components/status-badge';
import { fDateTime, relativeTime } from 'src/utils/format-time';
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from 'src/theme/sizes';
import VersionLabel from '../components/VersionLabel';
import ContextLabelRequestPanel from './ContextLabelRequestPanel';

// Intent: inspect publication history and request only a forward production move.
// Hierarchy: same table as skill Versions: version chip (with a "Viewing" tag on the current row),
// note, author, time, labels, files, then the one release control and the row menu.
// Palette/depth/surfaces: flat table, neutral text and shared semantic status badges.
// Typography/spacing: m3/s1/s3 and shared row heights; 12px related groups.
// Below sm each version is a stacked card (copied from skill Versions, not imported): line 1 the version
// and its menu, line 2 the note, line 3 "author · time · files", line 4 labels, line 5 the release action.
const cell = (column, extra) => [`& tbody td[data-column="${column}"]`, extra];
const tableSx = (theme) => ({
  "& tbody td": { overflowWrap: 'normal', wordBreak: 'normal' },
  '& td[data-column="meta"]': { display: 'none' },
  [theme.breakpoints.down('sm')]: {
    display: 'block',
    '& thead': { position: 'absolute', width: '1px', height: '1px', p: 0, m: '-1px', overflow: 'hidden', clip: 'rect(0 0 0 0)', whiteSpace: 'nowrap', border: 0 },
    '& tbody': { display: 'block' },
    '& tbody tr': { display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) auto', alignItems: 'start', rowGap: 0.5, height: 'auto', py: 1.5, borderBottom: `1px solid ${theme.palette.divider}`, '&:last-of-type': { borderBottom: 0 } },
    '& tbody td': { display: 'block', minWidth: 0, border: 0, p: 0, whiteSpace: 'normal' },
    ...Object.fromEntries([
      cell('number', { gridColumn: '1', gridRow: '1', alignSelf: 'center' }),
      cell('actions', { gridColumn: '2', gridRow: '1', justifySelf: 'end', alignSelf: 'center', '& .MuiIconButton-root': { mr: -1 } }),
      cell('note', { gridColumn: '1 / -1', gridRow: '2', '& .MuiTypography-root': { whiteSpace: 'normal', overflowWrap: 'anywhere' } }),
      cell('meta', { display: 'block', gridColumn: '1 / -1', gridRow: '3', ...theme.typography.s3, color: 'text.secondary' }),
      cell('labels', { gridColumn: '1 / -1', gridRow: '4', '&[data-empty="true"]': { display: 'none' } }),
      cell('release', { gridColumn: '1 / -1', gridRow: '5', '&[data-empty="true"]': { display: 'none' } }),
      cell('author', { display: 'none' }),
      cell('published', { display: 'none' }),
      cell('files', { display: 'none' }),
    ]),
  },
});
const COLUMNS = [['number', 'Version'], ['note', 'Note'], ['author', 'Author'], ['published', 'Published'], ['labels', 'Labels'], ['files', 'Files'], ['release', 'Release'], ['actions', 'Actions']];
const labelName = (label) => (typeof label === 'string' ? label : label.name);

export default function ContextVersionsTable({ contextId, query, productionVersion, latestVersion, viewedNumber, page, onPageChange, onSelectVersion }) {
  const [, setParams] = useSearchParams();
  const [menu, setMenu] = useState(null);
  if (!query.data && query.isLoading) return <ContentSkeleton variant="rows" />;
  if (query.isError) return <ErrorState title="Couldn't load versions." onRetry={query.refetch} />;
  if (!query.data?.results?.length) return <EmptyState title="No published versions" description="Review your saved draft before publishing." />;
  const pageSize = query.data.pageSize || query.data.results.length;
  // Compare happens where the versions are tested: open the Tests tab on this version.
  const compare = (number) => setParams((current) => { const next = new URLSearchParams(current); next.set('version', String(number)); next.set('tab', 'tests'); return next; });
  const releasable = (version) => version.number !== productionVersion && version.number === (viewedNumber ?? latestVersion) && (productionVersion == null || version.number > productionVersion);
  return <Stack spacing={0}>
    <TableContainer><Table size="small" aria-label="Context versions" sx={tableSx}><TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>{COLUMNS.map(([id, label]) => <TableCell key={id} data-column={id}>{label}</TableCell>)}<TableCell data-column="meta" sx={{ display: { xs: 'table-cell', sm: 'none' } }} /></TableRow></TableHead>
      <TableBody>{query.data.results.map((version) => {
        const names = (version.labels || []).map(labelName);
        const release = releasable(version);
        const files = version.fileCount;
        return <TableRow key={version.id} hover sx={{ height: ROW_HEIGHT.twoLine, verticalAlign: 'middle', cursor: 'pointer' }} onClick={() => onSelectVersion(version.number)}>
          <TableCell data-column="number"><Stack direction="row" spacing={1} alignItems="center" onClick={(event) => event.stopPropagation()}><VersionLabel number={version.number} digest={version.digest} />{version.number === viewedNumber && <StatusBadge status="current_only" role="info" variant="badge" dot={false} label="Viewing" />}</Stack></TableCell>
          <TableCell data-column="note"><Typography variant="s1" noWrap title={version.note || ''}>{version.note || '—'}</Typography></TableCell>
          <TableCell data-column="author"><Typography variant="s1" noWrap>{version.createdBy?.name || 'Not reported'}</Typography></TableCell>
          <TableCell data-column="published"><Typography variant="s3" color="text.secondary" title={version.createdAt ? fDateTime(version.createdAt) : undefined}>{version.createdAt ? relativeTime(version.createdAt) : 'Not reported'}</Typography></TableCell>
          <TableCell data-column="labels" data-empty={names.length ? undefined : 'true'}>{names.length ? <Stack direction="row" spacing={0.5} useFlexGap sx={{ flexWrap: 'wrap' }}>{names.map((name) => (name === 'production' ? <StatusBadge key={name} status="released" label="Production" /> : <StatusBadge key={name} status="unset" label={`${name.charAt(0).toUpperCase()}${name.slice(1)}`} />))}</Stack> : '—'}</TableCell>
          <TableCell data-column="files">{files ?? '—'}</TableCell>
          <TableCell data-column="release" data-empty={release ? undefined : 'true'} onClick={(event) => event.stopPropagation()}>{release && <ContextLabelRequestPanel contextId={contextId} version={version} />}</TableCell>
          <TableCell data-column="meta">{[version.createdBy?.name || 'Not reported', version.createdAt ? relativeTime(version.createdAt) : 'Not reported', files == null ? null : `${files} ${files === 1 ? 'file' : 'files'}`].filter(Boolean).join(' · ')}</TableCell>
          <TableCell data-column="actions"><IconButton size="small" aria-label={`Actions for v${version.number}`} onClick={(event) => { event.stopPropagation(); setMenu({ anchor: event.currentTarget, version }); }}><Iconify icon="solar:menu-dots-bold" width={20} sx={{ pointerEvents: 'none' }} /></IconButton></TableCell>
        </TableRow>;
      })}</TableBody></Table></TableContainer>
    <DataTablePagination page={page - 1} pageSize={pageSize} pageSizeOptions={[pageSize]} total={query.data.count || 0} onPageChange={(value) => onPageChange(value + 1)} loading={Boolean(query.isFetching)} disabled={Boolean(query.isFetching)} />
    <Menu anchorEl={menu?.anchor} open={Boolean(menu)} onClose={() => setMenu(null)}>
      <MenuItem onClick={() => { const { number } = menu.version; setMenu(null); onSelectVersion(number); }}><ListItemIcon><Iconify icon="solar:document-text-linear" width={16} /></ListItemIcon>View</MenuItem>
      <MenuItem onClick={() => { const { number } = menu.version; setMenu(null); compare(number); }}><ListItemIcon><Iconify icon="solar:transfer-horizontal-linear" width={16} /></ListItemIcon>Compare</MenuItem>
    </Menu>
  </Stack>;
}
ContextVersionsTable.propTypes = { contextId: PropTypes.string.isRequired, query: PropTypes.object.isRequired, productionVersion: PropTypes.number, latestVersion: PropTypes.number, viewedNumber: PropTypes.number, page: PropTypes.number.isRequired, onPageChange: PropTypes.func.isRequired, onSelectVersion: PropTypes.func.isRequired };
