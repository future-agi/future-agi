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
  Tooltip,
  useMediaQuery,
} from "@mui/material";
import ContentSkeleton from 'src/components/content-skeleton';
import ErrorState from 'src/components/error-state';
import { relativeReviewTime } from '../utils/review-display';
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
  return (
    <Stack spacing={1}>
      <Typography variant="s1" role="status">
        {authored ? 'Requests you authored. Open a request to inspect its status and decision.' : `${data.unreadCount} ${data.unreadCount === 1 ? 'request' : 'requests'} waiting for you`}
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
          <MenuItem value="">All statuses</MenuItem>
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
          {mobile ? <Stack aria-label="Assigned release reviews">{data.results.map((item) => <Stack key={item.id} direction="row" gap={1} alignItems="center" sx={{ py: 1.5, borderBottom: 1, borderColor: 'divider' }}>
            <Button color="inherit" onClick={() => onOpen(item)} sx={{ p: 0, minWidth: 0, flex: 1, textAlign: 'left', justifyContent: 'flex-start' }}>
              <Stack spacing={0.5} sx={{ minWidth: 0 }}>
                <Stack direction="row" gap={1} alignItems="center">{item.unread && <Box aria-hidden="true" sx={{ width: 6, height: 6, flexShrink: 0, borderRadius: '50%', bgcolor: 'text.primary' }} />}<Typography variant="s1" sx={{ fontWeight: item.unread ? 600 : 400, overflowWrap: 'anywhere' }}>{item.name}</Typography></Stack>
                <Typography variant="s3" color="text.secondary">{item.subjectKind === 'agent_context' ? 'Agent context' : 'Skill'} · {relativeReviewTime(item.updatedAt) || 'Update time unavailable'}</Typography>
              </Stack>
            </Button>
            <Stack alignItems="flex-end" spacing={0.5}><StatusBadge status={item.status === 'pending' ? 'in_review' : item.status === 'changes_requested' ? 'needs_changes' : item.status} /><IconButton size="small" aria-label={`Actions for ${item.name}`} onClick={(event) => setMenu({ id: item.id, anchor: event.currentTarget })}><Iconify icon="solar:menu-dots-bold" width={16} /></IconButton></Stack>
          </Stack>)}</Stack> : <TableContainer sx={{ maxWidth: 1 }}>
            <Table size="small" aria-label="Assigned release reviews">
              <TableHead>
                <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
                  <TableCell>Release</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell>Revision</TableCell>
                  <TableCell>Updated</TableCell>
                  <TableCell align="right">Actions</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {data.results.map((item) => (
                  <TableRow
                    key={item.id}
                    hover
                    sx={{ minHeight: ROW_HEIGHT.default }}
                  >
                    <TableCell sx={{ width: { xs: '70%', sm: 'auto' } }}>
                      <Button
                        color="inherit"
                        onClick={() => onOpen(item)}
                        sx={{ maxWidth: 1, p: 0, justifyContent: 'flex-start' }}
                      >
                        {item.unread && <Box component="span" aria-hidden="true" sx={{ width: 6, height: 6, flexShrink: 0, borderRadius: '50%', bgcolor: 'text.primary', mr: 1 }} />}
                        <Typography
                          variant="s1"
                          noWrap
                          sx={{
                            maxWidth: { xs: "16ch", sm: "40ch" },
                            fontWeight: item.unread ? 600 : 400,
                          }}
                        >
                          {item.name}
                        </Typography>
                      </Button>
                      <Typography variant="s3" display="block">
                        {item.subjectKind === "agent_context"
                          ? "Agent context"
                          : "Skill"}{" "}

                      </Typography>
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={item.status === "pending" ? "in_review" : item.status === "changes_requested" ? "needs_changes" : item.status} label={item.status === "changes_requested" ? "Changes requested" : undefined} />
                    </TableCell>
                    <TableCell sx={{ display: { xs: 'none !important', sm: 'table-cell !important' } }}>{item.revision}</TableCell>
                    <TableCell sx={{ color: 'text.secondary', width: { xs: '70%', sm: 'auto' }, pt: { xs: 0, sm: 1 } }}><Tooltip title={item.updatedAt || ''}><Typography component="span" variant="s3">{relativeReviewTime(item.updatedAt) || 'Update time unavailable'}</Typography></Tooltip></TableCell>
                    <TableCell align="right">
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
          {onToggleAuthored && <Button variant="outlined" onClick={onToggleAuthored}>{authored ? 'Assigned to me' : 'My requests'}</Button>}
          {onCompose && <Button variant="contained" color="primary" onClick={onCompose}>Request review</Button>}
        </>}
      />
      {state === 'loading' ? <ContentSkeleton variant="rows" rows={5} label="Loading reviews" /> : state === 'error' ? <ErrorState title="Couldn't load reviews." onRetry={onRetry} /> : state === 'empty' || (state === 'ready' && data && !data.results.length) ? <EmptyState title={authored ? 'No requests sent yet' : 'No reviews waiting for you'} description={authored ? 'Requests you send appear here.' : 'Requests assigned to you appear here.'} secondaryAction={!authored && onToggleAuthored ? <Button onClick={onToggleAuthored}>View my requests</Button> : undefined} /> : scopeKey && state === "ready" && data ? (
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
