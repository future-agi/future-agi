import PropTypes from 'prop-types';
import { Box, Button, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Typography } from '@mui/material';
import PageHeader from 'src/components/page-header';
import ContentSkeleton from 'src/components/content-skeleton';
import EmptyState from 'src/components/empty-state';
import ErrorState from 'src/components/error-state';
import StatusBadge from 'src/components/status-badge';
import { DataTablePagination } from 'src/components/data-table';
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from 'src/theme/sizes';
import CapabilityNotice from '../components/CapabilityNotice';

export default function ContextCampaignList({ query, availability, enabled = false, state, onOpen, page, onPageChange, onCreate }) {
  const data = query.data;
  const off = state === 'off' || !enabled;
  const loading = state === 'loading' || availability?.isLoading;
  const denied = ['denied', 'forbidden'].includes(state);
  const failed = state === 'error' || denied || availability?.isError;
  const records = state === 'empty' ? [] : data?.results ?? [];
  return <Stack spacing={1.5} sx={{ minWidth: 0 }}>
    <PageHeader title="Repository rollouts" description="Review repository changes. Track pull requests and delivery separately." sx={{ mb: 0 }} />
    {loading ? <ContentSkeleton sx={{ '& .MuiSkeleton-root': { bgcolor: 'action.selected' } }} variant="rows" rows={5} label="Checking rollout availability" />
      : failed ? <ErrorState title={denied ? 'You cannot view these rollouts' : "Couldn't check rollout availability"} onRetry={denied ? undefined : availability?.refetch || query.refetch} />
        : <>
          {off ? <CapabilityNotice title="Repository rollouts are off" description="New repository changes can't be started in this workspace. Existing rollout records stay readable." details="A workspace administrator can check whether repository rollouts can be enabled." />
            : !onCreate && <Typography variant="s1" color="text.secondary">Rollouts start from a repository inventory. None has been prepared for this workspace yet.</Typography>}
          {query.isFetching && data && <StatusBadge status="checking" label="Updating…" />}
          {query.isLoading && !data ? <ContentSkeleton sx={{ '& .MuiSkeleton-root': { bgcolor: 'action.selected' } }} variant="rows" rows={5} label="Loading repository rollouts" />
            : query.isError ? <ErrorState title="Couldn't load existing rollouts" onRetry={query.refetch} />
              : records.length === 0 ? (!off && <EmptyState title="No repository rollouts yet" description="A rollout keeps one fixed inventory for review. Nothing is added automatically." />)
                : <Box sx={{ minWidth: 0 }}>
                  <TableContainer><Table size="small" aria-label="Repository rollouts">
                    <TableHead><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}><TableCell>Rollout</TableCell><TableCell>Targets</TableCell><TableCell>State</TableCell><TableCell>Review</TableCell></TableRow></TableHead>
                    <TableBody>{records.map((campaign) => <TableRow hover key={campaign.id} sx={{ height: ROW_HEIGHT.default }}>
                      <TableCell><Typography variant="s1">{campaign.name}</Typography><Typography component="div" variant="s3" color="text.secondary">Revision {campaign.revision}{campaign.parentCampaignId ? ' · Follows an earlier rollout' : ''}</Typography></TableCell>
                      <TableCell>{campaign.targetCount}</TableCell><TableCell><StatusBadge status={campaign.status} /></TableCell>
                      <TableCell><Button size="small" onClick={() => onOpen(campaign.id)} aria-label={`Review ${campaign.name}, revision ${campaign.revision}`}>{off ? 'View rollout' : 'Review rollout'}</Button></TableCell>
                    </TableRow>)}</TableBody>
                  </Table></TableContainer>
                  <DataTablePagination page={page - 1} pageSize={25} total={data.count} onPageChange={(next) => onPageChange(next + 1)} pageSizeOptions={[25]} disabled={query.isFetching} />
                </Box>}
          {!off && onCreate && <Button variant="contained" onClick={onCreate} sx={{ alignSelf: 'flex-start' }}>New rollout</Button>}
        </>}
  </Stack>;
}
ContextCampaignList.propTypes = {
  query: PropTypes.object.isRequired, availability: PropTypes.object, enabled: PropTypes.bool, state: PropTypes.string,
  onOpen: PropTypes.func.isRequired, page: PropTypes.number.isRequired,
  onPageChange: PropTypes.func.isRequired, onCreate: PropTypes.func,
};
