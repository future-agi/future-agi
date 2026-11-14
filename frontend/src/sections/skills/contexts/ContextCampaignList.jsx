import PropTypes from 'prop-types';
import { Box, Button, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Typography } from '@mui/material';
import PageHeader from 'src/components/page-header';
import ContentSkeleton from 'src/components/content-skeleton';
import EmptyState from 'src/components/empty-state';
import ErrorState from 'src/components/error-state';
import StatusBadge from 'src/components/status-badge';
import { DataTablePagination } from 'src/components/data-table';
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from 'src/theme/sizes';
import RolloutInstructions from './RolloutInstructions';
import TechnicalDetails from '../components/TechnicalDetails';
import CapabilityNotice from '../components/CapabilityNotice';

export default function ContextCampaignList({ query, availability, enabled = false, state, onOpen, page, onPageChange, onCreate }) {
  const data = query.data;
  const off = state === 'off' || !enabled;
  const loading = state === 'loading' || availability?.isLoading;
  const denied = ['denied', 'forbidden'].includes(state) || query.error?.statusCode === 403 || availability?.error?.statusCode === 403;
  const failed = state === 'error' || denied || availability?.isError;
  const records = state === 'empty' ? [] : data?.results ?? [];
  return <Stack spacing={1.5} sx={{ minWidth: 0 }}>
    <PageHeader title="Repository rollouts" description="Roll an agent context out to many repositories at once." sx={{ mb: 0 }} />
    {loading ? <ContentSkeleton sx={{ '& .MuiSkeleton-root': { bgcolor: 'action.selected' } }} variant="rows" rows={5} label="Checking rollout availability" />
      : denied ? <CapabilityNotice title="Rollout access" description="You don't have access to repository rollouts. Ask a workspace admin." />
        : failed ? <ErrorState detail="Nothing was changed. Existing rollouts are unaffected." title="Couldn't load rollouts." onRetry={availability?.refetch || query.refetch} />
        : <>
          {off && <Stack spacing={1}><CapabilityNotice title="Repository rollouts are off" description="New repository changes can't be started. Existing rollout records stay readable. Rollouts are turned on in your deployment settings. Ask a workspace admin to check the setting." details="Set SKILLS_CONTEXT_CAMPAIGNS_ENABLED in the deployment environment. This is not a workspace toggle; a qualified local executor is still required." /><Button sx={{ alignSelf: 'flex-start' }} href="/dashboard/skills/contexts" variant="outlined" color="inherit">Back to agent contexts</Button></Stack>}
          {query.isFetching && data && <StatusBadge status="checking" label="Updating…" sx={{ alignSelf: 'flex-start' }} />}
          {query.isLoading && !data ? <ContentSkeleton sx={{ '& .MuiSkeleton-root': { bgcolor: 'action.selected' } }} variant="rows" rows={5} label="Loading repository rollouts" />
            : query.isError ? <ErrorState detail="Nothing was changed. Existing rollouts are unaffected." title="Couldn't load rollouts." onRetry={query.refetch} />
              : records.length === 0 ? (!off && <Stack spacing={1.5}><EmptyState title="No repository rollouts yet" description="Start by reading the repositories you want to update." /><RolloutInstructions inventory /></Stack>)
                : <Box sx={{ minWidth: 0 }}>
                  <TableContainer><Table size="small" aria-label="Repository rollouts">
                    <TableHead sx={{ display: { xs: 'none', sm: 'table-header-group' } }}><TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}><TableCell>Rollout</TableCell><TableCell>Targets</TableCell><TableCell>State</TableCell><TableCell>Review</TableCell></TableRow></TableHead>
                    <TableBody>{records.map((campaign) => <TableRow hover key={campaign.id} sx={{ height: { xs: 'auto', sm: ROW_HEIGHT.default }, display: { xs: 'grid', sm: 'table-row' }, gridTemplateColumns: 'minmax(0, 1fr) auto', py: { xs: 1, sm: 0 }, '& > td': { borderBottom: { xs: 0, sm: undefined } } }}>
                      <TableCell sx={{ gridColumn: '1 / -1', overflowWrap: 'anywhere' }}><Typography variant="s1">{campaign.name}</Typography>{campaign.parentCampaignId && <Typography component="div" variant="s3" color="text.secondary">Follows an earlier rollout</Typography>}<TechnicalDetails title="Rollout details"><Typography variant="s3">Revision {campaign.revision}</Typography></TechnicalDetails></TableCell>
                      <TableCell><Typography variant="s1">{campaign.targetCount}<Box component="span" sx={{ display: { xs: 'inline', sm: 'none' } }}> targets</Box></Typography></TableCell><TableCell><StatusBadge status={campaign.status} /></TableCell>
                      <TableCell sx={{ gridColumn: '1 / -1' }}><Button size="small" variant="outlined" onClick={() => onOpen(campaign.id)} aria-label={`Review ${campaign.name}, revision ${campaign.revision}`}>Open</Button></TableCell>
                    </TableRow>)}</TableBody>
                  </Table></TableContainer>
                  <DataTablePagination page={page - 1} pageSize={25} total={data.count} onPageChange={(next) => onPageChange(next + 1)} pageSizeOptions={[25]} disabled={query.isFetching} />
                </Box>}
          {!off && onCreate && <Button color="primary" variant="contained" onClick={onCreate} sx={{ alignSelf: 'flex-start' }}>New rollout</Button>}
        </>}
  </Stack>;
}
ContextCampaignList.propTypes = {
  query: PropTypes.object.isRequired, availability: PropTypes.object, enabled: PropTypes.bool, state: PropTypes.string,
  onOpen: PropTypes.func.isRequired, page: PropTypes.number.isRequired,
  onPageChange: PropTypes.func.isRequired, onCreate: PropTypes.func,
};
