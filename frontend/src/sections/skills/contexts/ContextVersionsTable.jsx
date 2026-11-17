import { useState } from 'react';
import PropTypes from 'prop-types';
import { useSearchParams } from 'react-router-dom';
import { Box, Button, Checkbox, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Typography } from '@mui/material';
import ContentSkeleton from 'src/components/content-skeleton';
import { DataTablePagination } from 'src/components/data-table';
import ErrorState from 'src/components/error-state';
import EmptyState from 'src/components/empty-state';
import StatusBadge from 'src/components/status-badge';
import { fDateTime, relativeTime } from 'src/utils/format-time';
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from 'src/theme/sizes';
import VersionLabel from '../components/VersionLabel';
import RowActions from '../components/RowActions';
import { rowLinkProps } from '../components/responsiveTable';
import ContextLabelRequestPanel from './ContextLabelRequestPanel';
import ContextVersionCompare from './ContextVersionCompare';

// Intent: inspect publication history and request only a forward production move.
// Hierarchy: same table as skill Versions: version chip (with a "Viewing" tag on the current row),
// note, author, time, labels, files, then the row's release control (a ghost button) and the row's ⋯ menu.
// Row actions follow the shared pattern (SKILL-TRACES §16 UXF-12): the whole row opens the version (View),
// and View and Compare sit in the trailing ⋯; the release request is the one inline action. On a phone the
// ⋯ becomes a chevron (Compare stays reachable through the row's checkbox).
// A compare checkbox column picks two versions; "Compare vA and vB" opens the in-tab comparison (?compare=A,B).
// Palette/depth/surfaces: flat table, neutral text and shared semantic status badges.
// Typography/spacing: m3/s1/s3 and shared row heights; 12px related groups.
// Below sm each version is a stacked card (copied from skill Versions, not imported): line 1 the version
// and its menu, line 2 the note, line 3 "author · time · files", line 4 labels, line 5 the release action.
// The actions cell uses display: contents on phones so the release control and the menu take their own grid slots.
const cell = (column, extra) => [`& tbody td[data-column="${column}"]`, extra];
const tableSx = (theme) => ({
  "& tbody td": { overflowWrap: 'normal', wordBreak: 'normal' },
  '& td[data-column="meta"]': { display: 'none' },
  [theme.breakpoints.down('sm')]: {
    display: 'block',
    '& thead': { position: 'absolute', width: '1px', height: '1px', p: 0, m: '-1px', overflow: 'hidden', clip: 'rect(0 0 0 0)', whiteSpace: 'nowrap', border: 0 },
    '& tbody': { display: 'block' },
    '& tbody tr': { display: 'grid', gridTemplateColumns: 'auto minmax(0, 1fr) auto', alignItems: 'start', rowGap: 0.5, height: 'auto', py: 1.5, borderBottom: `1px solid ${theme.palette.divider}`, '&:last-of-type': { borderBottom: 0 } },
    '& tbody td': { display: 'block', minWidth: 0, border: 0, p: 0, whiteSpace: 'normal' },
    ...Object.fromEntries([
      cell('select', { gridColumn: '1', gridRow: '1 / span 4', alignSelf: 'start', '& .MuiCheckbox-root': { p: 0.5, ml: -0.5 } }),
      cell('number', { gridColumn: '2', gridRow: '1', alignSelf: 'center' }),
      cell('actions', { display: 'contents', '& [data-slot="menu"]': { gridColumn: '3', gridRow: '1', justifySelf: 'end', alignSelf: 'center', '& .MuiIconButton-root': { mr: -1 } }, '& [data-slot="release"]': { gridColumn: '2 / -1', gridRow: '5', justifySelf: 'start', ml: -1 } }),
      cell('note', { gridColumn: '2 / -1', gridRow: '2', '& .MuiTypography-root': { whiteSpace: 'normal', overflowWrap: 'anywhere' } }),
      cell('meta', { display: 'block', gridColumn: '2 / -1', gridRow: '3', ...theme.typography.s3, color: 'text.secondary' }),
      cell('labels', { gridColumn: '2 / -1', gridRow: '4', '&[data-empty="true"]': { display: 'none' } }),
      cell('author', { display: 'none' }),
      cell('published', { display: 'none' }),
      cell('files', { display: 'none' }),
    ]),
  },
});
const COLUMNS = [['number', 'Version'], ['note', 'Note'], ['author', 'Author'], ['published', 'Published'], ['labels', 'Labels'], ['files', 'Files'], ['actions', 'Actions']];
// ?compare=1,2: exactly two distinct positive version numbers, ascending. Anything else is ignored.
const parseCompare = (value) => {
  const match = /^([1-9]\d{0,8}),([1-9]\d{0,8})$/.exec(value || '');
  if (!match || match[1] === match[2]) return null;
  return [Number(match[1]), Number(match[2])].sort((a, b) => a - b);
};
const labelName = (label) => (typeof label === 'string' ? label : label.name);

export default function ContextVersionsTable({ contextId, query, productionVersion, latestVersion, viewedNumber, page, onPageChange, onSelectVersion }) {
  const [params, setParams] = useSearchParams();
  const open = parseCompare(params.get('compare'));
  const [selected, setSelected] = useState(() => open || []);
  if (!query.data && query.isLoading) return <ContentSkeleton variant="rows" />;
  if (query.isError) return <ErrorState title="Couldn't load versions." onRetry={query.refetch} />;
  if (!query.data?.results?.length) return <EmptyState title="No published versions" description="Review your saved draft before publishing." />;
  const pageSize = query.data.pageSize || query.data.results.length;
  const sorted = [...selected].sort((a, b) => a - b);
  const compareParam = (value) => setParams((current) => { const next = new URLSearchParams(current); if (value) next.set('compare', value); else next.delete('compare'); return next; });
  // The menu's Compare preselects its row; a second tick (or a second menu pick) completes the pair.
  const preselect = (number) => setSelected((current) => (current.includes(number) ? current : current.length < 2 ? [...current, number] : [number]));
  const releasable = (version) => version.number !== productionVersion && version.number === (viewedNumber ?? latestVersion) && (productionVersion == null || version.number > productionVersion);
  return <Stack spacing={0}>
    <Stack direction="row" useFlexGap spacing={1} alignItems="center" sx={{ flexWrap: 'wrap', minHeight: 40, pb: 1 }}>
      {sorted.length === 2
        ? <><Button variant="outlined" size="small" onClick={() => compareParam(sorted.join(','))}>Compare v{sorted[0]} and v{sorted[1]}</Button><Button variant="text" size="small" onClick={() => setSelected([])}>Clear selection</Button></>
        : <Typography variant="s3" color="text.secondary">Select two versions to compare.</Typography>}
    </Stack>
    <TableContainer><Table size="small" aria-label="Context versions" sx={tableSx}><TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}><TableCell padding="checkbox" data-column="select" />{COLUMNS.map(([id, label]) => <TableCell key={id} data-column={id}>{label}</TableCell>)}<TableCell data-column="meta" sx={{ display: { xs: 'table-cell', sm: 'none' } }} /></TableRow></TableHead>
      <TableBody>{query.data.results.map((version) => {
        const names = (version.labels || []).map(labelName);
        const release = releasable(version);
        const files = version.fileCount;
        const view = { label: 'View', onClick: () => onSelectVersion(version.number) };
        const link = rowLinkProps(view);
        return <TableRow key={version.id} {...link} sx={[{ height: ROW_HEIGHT.twoLine, verticalAlign: 'middle' }, link.sx || {}]}>
          <TableCell padding="checkbox" data-column="select" onClick={(event) => event.stopPropagation()}><Checkbox size="small" checked={selected.includes(version.number)} disabled={selected.length === 2 && !selected.includes(version.number)} inputProps={{ 'aria-label': `Select v${version.number} to compare` }} onChange={(_, checked) => setSelected((current) => (checked ? [...current, version.number] : current.filter((number) => number !== version.number)))} /></TableCell>
          <TableCell data-column="number"><Stack direction="row" spacing={1} alignItems="center" onClick={(event) => event.stopPropagation()}><VersionLabel number={version.number} digest={version.digest} />{version.number === viewedNumber && <StatusBadge status="current_only" role="neutral" variant="badge" dot={false} label="Viewing" />}</Stack></TableCell>
          <TableCell data-column="note"><Typography variant="s1" noWrap title={version.note || ''}>{version.note || '—'}</Typography></TableCell>
          <TableCell data-column="author"><Typography variant="s1" noWrap>{version.createdBy?.name || 'Not reported'}</Typography></TableCell>
          <TableCell data-column="published"><Typography variant="s3" color="text.secondary" title={version.createdAt ? fDateTime(version.createdAt) : undefined}>{version.createdAt ? relativeTime(version.createdAt) : 'Not reported'}</Typography></TableCell>
          <TableCell data-column="labels" data-empty={names.length ? undefined : 'true'}>{names.length ? <Stack direction="row" spacing={0.5} useFlexGap sx={{ flexWrap: 'wrap' }}>{names.map((name) => (name === 'production' ? <StatusBadge key={name} status="released" label="Production" /> : <StatusBadge key={name} status="unset" label={`${name.charAt(0).toUpperCase()}${name.slice(1)}`} />))}</Stack> : '—'}</TableCell>
          <TableCell data-column="files">{files ?? '—'}</TableCell>
          <TableCell data-column="meta">{[version.createdBy?.name || 'Not reported', version.createdAt ? relativeTime(version.createdAt) : 'Not reported', files == null ? null : `${files} ${files === 1 ? 'file' : 'files'}`].filter(Boolean).join(' · ')}</TableCell>
          <TableCell data-column="actions" onClick={(event) => event.stopPropagation()}><Stack direction="row" spacing={0.5} alignItems="center" justifyContent="flex-end" sx={{ display: { xs: 'contents', sm: 'flex' } }}>
            {release && <Box data-slot="release"><ContextLabelRequestPanel contextId={contextId} version={version} /></Box>}
            <Box data-slot="menu"><RowActions name={`v${version.number}`} primary={view} actions={[{ label: 'Compare', onClick: () => preselect(version.number) }]} /></Box>
          </Stack></TableCell>
        </TableRow>;
      })}</TableBody></Table></TableContainer>
    <DataTablePagination page={page - 1} pageSize={pageSize} pageSizeOptions={[pageSize]} total={query.data.count || 0} onPageChange={(value) => onPageChange(value + 1)} loading={Boolean(query.isFetching)} disabled={Boolean(query.isFetching)} />
    {open && <Box sx={{ pt: 2, minWidth: 0 }}><ContextVersionCompare contextId={contextId} from={open[0]} to={open[1]} onClose={() => compareParam(null)} /></Box>}
  </Stack>;
}
ContextVersionsTable.propTypes = { contextId: PropTypes.string.isRequired, query: PropTypes.object.isRequired, productionVersion: PropTypes.number, latestVersion: PropTypes.number, viewedNumber: PropTypes.number, page: PropTypes.number.isRequired, onPageChange: PropTypes.func.isRequired, onSelectVersion: PropTypes.func.isRequired };
