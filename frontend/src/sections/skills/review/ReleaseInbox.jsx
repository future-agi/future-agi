import { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  IconButton,
  ListItemIcon,
  Menu,
  MenuItem,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
  Tab,
  useMediaQuery,
} from "@mui/material";
import ContentSkeleton from 'src/components/content-skeleton';
import ErrorState from 'src/components/error-state';
import { visuallyHidden } from '@mui/utils';
import CapabilityNotice from '../components/CapabilityNotice';
import { CustomTabs } from 'src/components/tabs/tabs';
import When from '../components/When';
import { releaseTitle, counted } from './reviewPresentation';
import { reviewPersonName } from '../utils/review-display';
import PageHeader from "src/components/page-header";
import ListToolbar from "src/components/list-toolbar";
import EmptyState from "src/components/empty-state";
import Iconify from "src/components/iconify";
import StatusBadge from "src/components/status-badge";
import { DataTablePagination } from "src/components/data-table";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import { ReviewState } from "./ReleaseReviewBody";
import { OperationFeedback, useReviewedOperation } from "./ReleaseRequestPanel";

function InboxRows({
  data,
  status,
  onStatusChange,
  onPageChange,
  onOpen,
  onSeen,
  authored,
}) {
  const mobile = useMediaQuery((theme) => theme.breakpoints.down('sm'));
  const [menu, setMenu] = useState(null);
  const operation = useReviewedOperation();
  const row = data.results.find((item) => item.id === menu?.id);
  const rows = [...data.results].sort((a, b) => Number(b.status === 'pending') - Number(a.status === 'pending'));
  return (
    <Stack spacing={1}>
      <Typography variant="s1" role="status">
        {authored ? 'Requests you authored. Open a request to inspect its status and decision.' : data.pendingCount == null ? 'Assigned requests. Open a request to inspect its status and decision.' : `${counted(data.pendingCount, 'request')} waiting for your decision`}
      </Typography>
      <ListToolbar>
        <TextField
          select
          size="small"
          label="Request status"
          value={status}
          InputLabelProps={{ shrink: true }}
          SelectProps={{ displayEmpty: true }}
          onChange={(event) => onStatusChange(event.target.value)}
        >
          <MenuItem value="">All</MenuItem>
          {[
            ["pending", "Pending"],
            ["approved", "Approved"],
            ["changes_requested", "Changes requested"],
            ["released", "Released"],
            ["superseded", "Superseded"],
          ].map(([value, label]) => (
            <MenuItem key={value} value={value}>
              {label}
            </MenuItem>
          ))}
        </TextField>
      </ListToolbar>
      <OperationFeedback operation={operation} />
      {!data.results.length ? (
        <EmptyState
          variant="no-results"
          title={authored ? 'No requests sent yet' : 'No reviews waiting for you'}
          description={authored ? 'Requests you send appear here.' : 'Requests assigned to you appear here.'}
        />
      ) : (
        <>
          {mobile ? <Stack aria-label="Assigned release reviews">{rows.map((item) => <Stack key={item.id} direction="row" gap={1} alignItems="center" sx={{ py: 1.5, borderBottom: 1, borderColor: 'divider' }}>
            <Button color="inherit" aria-label={item.name} onClick={() => onOpen(item)} sx={{ p: 0, minWidth: 0, flex: 1, textAlign: 'left', justifyContent: 'flex-start', whiteSpace: 'normal', height: 'auto' }}>
              <Stack spacing={0.5} sx={{ minWidth: 0 }}>
                <Stack direction="row" gap={1} alignItems="center">{item.unread && item.status === 'pending' && <><Box component="span" aria-hidden="true" sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: 'primary.main', flexShrink: 0, mr: 1 }} /><Box component="span" sx={visuallyHidden}>Unread</Box></>}<Typography variant="s1" sx={{ fontWeight: item.unread ? 600 : 400, overflowWrap: 'anywhere' }}>{item.name}</Typography></Stack>
                {item.transition && <Typography variant="s1">{releaseTitle(item.transition)}</Typography>}
                {item.status === 'pending' && <Typography variant="s3" color="text.secondary">Waiting on {item.recipients?.map((person) => reviewPersonName(person.name)).join(', ') || 'the assigned reviewer'}</Typography>}
                <Typography variant="s3" color="text.secondary">Requested by {reviewPersonName(item.authorName)} · {item.subjectKind === 'agent_context' ? 'Agent context' : 'Skill'} · <When value={item.updatedAt} fallback="Time not reported" /></Typography>
              </Stack>
            </Button>
            {item.status === 'pending' && <Button size="small" variant="outlined" onClick={() => onOpen(item)}>Review</Button>}
            <Stack alignItems="flex-end" spacing={0.5}><StatusBadge status={item.status === 'pending' ? 'in_review' : item.status === 'changes_requested' ? 'needs_changes' : item.status} /><IconButton size="small" aria-label={`Actions for ${item.name}`} onClick={(event) => setMenu({ id: item.id, anchor: event.currentTarget })}><Iconify icon="solar:menu-dots-bold" width={16} /></IconButton></Stack>
          </Stack>)}</Stack> : <TableContainer sx={{ maxWidth: 1 }}>
            <Table size="small" aria-label="Assigned release reviews">
              <TableHead>
                <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
                  <TableCell>Release</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell>Updated</TableCell>
                  <TableCell align="right">Actions</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {rows.map((item) => (
                  <TableRow
                    key={item.id}
                    hover
                    sx={{ minHeight: ROW_HEIGHT.default }}
                  >
                    <TableCell sx={{ width: { xs: '70%', sm: 'auto' } }}>
                      <Button
                        color="inherit"
                        aria-label={item.name}
                        onClick={() => onOpen(item)}
                        sx={{ maxWidth: 1, p: 0, justifyContent: 'flex-start' }}
                      >
                        {item.unread && item.status === 'pending' && <><Box component="span" aria-hidden="true" sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: 'primary.main', flexShrink: 0, mr: 1 }} /><Box component="span" sx={visuallyHidden}>Unread</Box></>}
                        <Typography
                          variant="s1"
                          noWrap
                          sx={{
                            maxWidth: { xs: "16ch", sm: "40ch" },
                            fontWeight: item.unread ? 600 : 400,
                          }}
                        >
                          {item.name}{item.transition && ` · ${releaseTitle(item.transition)}`}
                        </Typography>
                      </Button>
                      <Typography variant="s3" display="block">
                        Requested by {reviewPersonName(item.authorName)} · {item.subjectKind === "agent_context"
                          ? "Agent context"
                          : "Skill"}

                      </Typography>
                      {item.status === 'pending' && <Typography variant="s3" color="text.secondary">Waiting on {item.recipients?.map((person) => reviewPersonName(person.name)).join(', ') || 'the assigned reviewer'}</Typography>}
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={item.status === "pending" ? "in_review" : item.status === "changes_requested" ? "needs_changes" : item.status} label={item.status === "changes_requested" ? "Changes requested" : undefined} />
                    </TableCell>
                    <TableCell sx={{ color: 'text.secondary', width: { xs: '70%', sm: 'auto' }, pt: { xs: 0, sm: 1 } }}><When value={item.updatedAt} fallback="Time not reported" /></TableCell>
                    <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                      {item.status === 'pending' && <Button size="small" variant="outlined" onClick={() => onOpen(item)}>Review</Button>}
                      <IconButton
                        size="small"
                        aria-label={`Actions for ${item.name}`}
                        onClick={(event) =>
                          setMenu({ id: item.id, anchor: event.currentTarget })
                        }
                      >
                        <Iconify icon="solar:menu-dots-bold" width={16} />
                      </IconButton>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>}
          <DataTablePagination
            page={data.page - 1}
            pageSize={data.pageSize}
            total={data.count}
            onPageChange={(page) => onPageChange(page + 1)}
            pageSizeOptions={[data.pageSize]}
          />
        </>
      )}
      <Menu
        open={Boolean(row)}
        anchorEl={menu?.anchor}
        onClose={() => setMenu(null)}
      >
        <MenuItem
          onClick={() => {
            onOpen(row);
            setMenu(null);
          }}
        >
          <ListItemIcon>
            <Iconify icon="solar:eye-linear" width={16} />
          </ListItemIcon>
          Open exact request
        </MenuItem>
        <MenuItem
          disabled={
            !onSeen || !row?.unread || operation.busy || operation.uncertain
          }
          onClick={async () => {
            const current = row;
            setMenu(null);
            await operation.run(onSeen, {
              requestId: current.id,
              revision: current.revision,
            });
          }}
        >
          <ListItemIcon>
            <Iconify icon="solar:check-read-linear" width={16} />
          </ListItemIcon>
          Mark this revision seen
        </MenuItem>
      </Menu>
    </Stack>
  );
}
InboxRows.propTypes = {
  data: PropTypes.object.isRequired,
  status: PropTypes.string.isRequired,
  onStatusChange: PropTypes.func.isRequired,
  onPageChange: PropTypes.func.isRequired,
  onOpen: PropTypes.func.isRequired,
  onSeen: PropTypes.func,
  authored: PropTypes.bool,
};

export default function ReleaseInbox({
  scopeKey,
  state = "unavailable",
  data,
  status = "",
  onStatusChange,
  onPageChange,
  onOpen,
  onSeen,
  onRetry,
  onCompose,
  onToggleAuthored,
  authored = false,
}) {
  return (
    <Box sx={{ px: { xs: 2, sm: 3 }, py: 2, minWidth: 0 }}>
      <PageHeader
        title="Reviews"
        description="Review candidates before they reach production."
        sx={{ mb: 1.5, flexDirection: { xs: 'column', sm: 'row' }, gap: 1.5, '& > .MuiStack-root + .MuiStack-root': { ml: { xs: 0, sm: 2 } } }}
        actions={scopeKey && !["off", "denied", "forbidden", "unavailable"].includes(state) && <>
          {onCompose && <Button variant="contained" color="primary" onClick={onCompose}>Request review</Button>}
        </>}
      />
      {scopeKey && !['off', 'denied', 'forbidden', 'unavailable'].includes(state) && onToggleAuthored && <CustomTabs value={authored ? 'mine' : 'assigned'} onChange={(_, value) => { if ((value === 'mine') !== authored) onToggleAuthored(); }} aria-label="Review inbox views" sx={{ mb: 1.5 }}><Tab value="assigned" label={data?.pendingCount == null ? 'Assigned to me' : `Assigned to me (${data.pendingCount})`} /><Tab value="mine" label="My requests" /></CustomTabs>}
      {!scopeKey || ['denied', 'forbidden'].includes(state) ? <CapabilityNotice title="You don't have access to reviews." description="Ask a workspace admin for reviewer access." /> : state === 'loading' ? <ContentSkeleton variant="rows" rows={5} label="Loading reviews" /> : state === 'error' ? <ErrorState title="Couldn't load reviews." onRetry={onRetry} /> : state === 'empty' || (state === 'ready' && data && !data.results.length) ? <EmptyState title={authored ? 'No requests sent yet' : 'No reviews waiting for you'} description={authored ? 'Requests you send appear here.' : 'Requests assigned to you appear here.'} secondaryAction={!authored && onToggleAuthored ? <Button onClick={onToggleAuthored}>View my requests</Button> : undefined} /> : scopeKey && state === "ready" && data ? (
        <InboxRows
          key={scopeKey}
          {...{ data, status, onStatusChange, onPageChange, onOpen, onSeen, authored }}
        />
      ) : (
        <ReviewState
          state={!scopeKey ? "forbidden" : state}
          onRetry={onRetry}
        />
      )}
    </Box>
  );
}
ReleaseInbox.propTypes = {
  scopeKey: PropTypes.string,
  state: PropTypes.string,
  data: PropTypes.object,
  status: PropTypes.string,
  onStatusChange: PropTypes.func,
  onPageChange: PropTypes.func,
  onOpen: PropTypes.func,
  onSeen: PropTypes.func,
  onRetry: PropTypes.func,
  onCompose: PropTypes.func,
  onToggleAuthored: PropTypes.func,
  authored: PropTypes.bool,
};
