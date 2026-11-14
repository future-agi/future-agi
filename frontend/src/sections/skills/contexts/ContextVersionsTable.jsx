import PropTypes from 'prop-types';
import { Button, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TablePagination, TableRow, Typography } from '@mui/material';
import ContentSkeleton from 'src/components/content-skeleton';
import ErrorState from 'src/components/error-state';
import EmptyState from 'src/components/empty-state';
import StatusBadge from 'src/components/status-badge';
import { relativeTime } from 'src/utils/format-time';
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from 'src/theme/sizes';
import VersionLabel from '../components/VersionLabel';
import ContextLabelRequestPanel from './ContextLabelRequestPanel';

// Intent: inspect publication history and request only a forward production move.
// Hierarchy: version and production role first, note/author/time next, IDs disclosed.
// Palette/depth/surfaces: flat table, neutral text and shared semantic status badges.
// Typography/spacing: m3/s1/s3 and shared row heights; 12px related groups.
export default function ContextVersionsTable({ contextId, query, productionVersion, latestVersion, viewedNumber, page, onPageChange, onSelectVersion }) {
  if (!query.data && query.isLoading) return <ContentSkeleton variant="rows" />;
  if (query.isError) return <ErrorState title="Couldn't load versions." onRetry={query.refetch} />;
  if (!query.data?.results?.length) return <EmptyState title="No published versions" description="Review your saved draft before publishing." />;
  return <Stack spacing={1.5}>
    <TableContainer><Table size="small" aria-label="Context versions"><TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>{['Version', 'Note', 'Author', 'Published', 'Labels', 'Actions'].map((label) => <TableCell key={label}>{label}</TableCell>)}</TableRow></TableHead>
      <TableBody>{query.data.results.map((version) => <TableRow key={version.id} hover sx={{ height: ROW_HEIGHT.default, verticalAlign: 'middle' }}>
        <TableCell><Stack direction="row" spacing={1} alignItems="center"><Typography component="span" variant="s1" sx={{ fontWeight: 500 }}><VersionLabel number={version.number} digest={version.digest} /></Typography>{version.number === viewedNumber ? <Typography variant="s3" color="text.secondary">Viewing</Typography> : <Button variant="text" size="small" color="inherit" aria-label={`View v${version.number}`} onClick={() => onSelectVersion(version.number)}>View</Button>}</Stack></TableCell>
        <TableCell>{version.note || '—'}</TableCell><TableCell>{version.createdBy?.name || 'Not reported'}</TableCell><TableCell>{version.createdAt ? relativeTime(version.createdAt) : 'Not reported'}</TableCell>
        <TableCell><Stack spacing={0.5} alignItems="flex-start">{(version.labels || []).map((label) => { const name = typeof label === 'string' ? label : label.name; return name === 'production' ? <StatusBadge key={name} status="released" label="Production" /> : <Typography key={name} variant="s3">{`${name.charAt(0).toUpperCase()}${name.slice(1)}`}</Typography>; })}</Stack></TableCell>
        <TableCell>{version.number !== productionVersion && version.number === (viewedNumber ?? latestVersion) && (productionVersion == null || version.number > productionVersion) && <ContextLabelRequestPanel contextId={contextId} version={version} />}</TableCell>
      </TableRow>)}</TableBody></Table></TableContainer>
    <TablePagination component="div" count={query.data.count} page={page - 1} rowsPerPage={query.data.pageSize} rowsPerPageOptions={[]} onPageChange={(_, value) => onPageChange(value + 1)} />
  </Stack>;
}
ContextVersionsTable.propTypes = { contextId: PropTypes.string.isRequired, query: PropTypes.object.isRequired, productionVersion: PropTypes.number, latestVersion: PropTypes.number, viewedNumber: PropTypes.number, page: PropTypes.number.isRequired, onPageChange: PropTypes.func.isRequired, onSelectVersion: PropTypes.func.isRequired };
