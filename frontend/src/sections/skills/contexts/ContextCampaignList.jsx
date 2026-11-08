import PropTypes from 'prop-types';
import { Alert, Box, Button, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Typography } from '@mui/material';
import PageHeader from 'src/components/page-header';
import ContentSkeleton from 'src/components/content-skeleton';
import EmptyState from 'src/components/empty-state';
import ErrorState from 'src/components/error-state';
import StatusBadge from 'src/components/status-badge';
import { DataTablePagination } from 'src/components/data-table';
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from 'src/theme/sizes';

export default function ContextCampaignList({ query, enabled = false, state, onOpen, page, onPageChange, onCreate }) {
  const data = query.data;
  return <Stack spacing={1.5} sx={{ minWidth: 0 }}>
    <PageHeader title="Repository rollouts" description="Review exact patches for a fixed repository inventory. Pull requests, merges and delivery are separate facts." />
    {!enabled && <Alert severity="info">Campaigns are unavailable until the integrated 40-target exercise and rollout qualification pass. Existing receipts remain inspectable.</Alert>}
    {state === 'loading' || query.isLoading ? <ContentSkeleton variant="rows" rows={5} label="Loading repository rollouts" />
      : state === 'error' || state === 'forbidden' || query.isError ? <ErrorState title={state === 'forbidden' ? 'You cannot view these rollouts' : 'Could not load rollouts'} onRetry={query.refetch} />
      : state === 'empty' || !data || data.count === 0 ? <EmptyState title="No repository rollouts" description="Select repository bindings and exact releases in Consumers, then create a fixed inventory. Nothing is added automatically." />
      : <Box sx={{ minWidth: 0 }}>
        <TableContainer><Table size="small" aria-label="Repository rollouts">
          <TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}><TableCell>Rollout</TableCell><TableCell>Targets</TableCell><TableCell>State</TableCell><TableCell>Review</TableCell></TableRow></TableHead>
          <TableBody>{data.results.map((campaign) => <TableRow hover key={campaign.id} sx={{ height: ROW_HEIGHT.default }}>
            <TableCell><Typography variant="s1">{campaign.name}</Typography><Typography component="div" variant="s3" color="text.secondary">Revision {campaign.revision}{campaign.parentCampaignId ? ' · Reverse campaign' : ''}</Typography></TableCell>
            <TableCell>{campaign.targetCount}</TableCell><TableCell><StatusBadge status={campaign.status} /></TableCell>
            <TableCell><Button size="small" onClick={() => onOpen(campaign.id)} aria-label={`Review ${campaign.name}`}>Review rollout</Button></TableCell>
          </TableRow>)}</TableBody>
        </Table></TableContainer>
        <DataTablePagination page={page - 1} pageSize={25} total={data.count} onPageChange={(next) => onPageChange(next + 1)} pageSizeOptions={[25]} disabled={query.isFetching} />
      </Box>}
    {onCreate && <Button variant="contained" disabled={!enabled} onClick={onCreate} sx={{ alignSelf: 'flex-start' }}>Review selected inventory</Button>}
  </Stack>;
}
ContextCampaignList.propTypes = {
  query: PropTypes.object.isRequired, enabled: PropTypes.bool, state: PropTypes.string,
  onOpen: PropTypes.func.isRequired, page: PropTypes.number.isRequired,
  onPageChange: PropTypes.func.isRequired, onCreate: PropTypes.func,
};
