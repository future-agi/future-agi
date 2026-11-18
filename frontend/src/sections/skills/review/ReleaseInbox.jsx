import { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  ButtonBase,
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
import { releaseTitle, WAITING_FOR_APPROVAL } from './reviewPresentation';
import { reviewPersonName } from '../utils/review-display';
import PageHeader from "src/components/page-header";
import ListToolbar from "src/components/list-toolbar";
import EmptyState from "src/components/empty-state";
import StatusBadge from "src/components/status-badge";
import RowActions, { RowActionsCell } from "../components/RowActions";
import { rowLinkProps } from "../components/responsiveTable";
import { INBOX_OPEN_REQUEST, RELEASE_REQUEST } from "../utils/terms";
import { DataTablePagination } from "src/components/data-table";
import { ROW_HEIGHT, TABLE_HEADER_HEIGHT } from "src/theme/sizes";
import { skillsRouteSx } from "../components/routeGutter";
import { ReviewState } from "./ReleaseReviewBody";
import { OperationFeedback, useReviewedOperation } from "./ReleaseRequestPanel";

// Someone denied Reviews cannot ask from here, so they copy the request for an admin (the Install tab's pattern).
function RequestReviewerAccess() {
  const [outcome, setOutcome] = useState(null);
  const request = async () => {
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Unavailable');
      await navigator.clipboard.writeText(`Please give me reviewer access to Skills reviews: ${window.location.href}`);
      setOutcome('copied');
    } catch { setOutcome('error'); }
  };
  return <Stack direction="row" spacing={1} alignItems="center" useFlexGap sx={{ flexWrap: 'wrap' }}>
    <Button variant="outlined" size="small" onClick={request}>Copy request for admin</Button>
    {outcome === 'copied' && <Typography variant="s3" role="status">Copied the request for an admin.</Typography>}
    {outcome === 'error' && <Typography variant="s3" role="status">Could not copy. Ask a workspace admin for reviewer access.</Typography>}
  </Stack>;
}

function InboxRows({
  data,
  status,
  onStatusChange,
  onPageChange,
  onOpen,
  onSeen,
  authored,
  currentUserId,
}) {
  const mobile = useMediaQuery((theme) => theme.breakpoints.down('sm'));
  const operation = useReviewedOperation();
  const rows = [...data.results].sort((a, b) => Number(b.status === 'pending') - Number(a.status === 'pending'));
  // UXF-12: the whole row opens the request; "Open request" stays inline as a ghost button and leads the ⋯
  // menu, which also holds "Mark this revision seen" (kept reachable on a phone: phoneMenu).
  const primary = (item) => ({ label: INBOX_OPEN_REQUEST, onClick: () => onOpen(item) });
  const secondary = (item) => [{
    label: 'Mark this revision seen',
    disabled: !onSeen || !item.unread || operation.busy || operation.uncertain,
    reason: !item.unread ? 'Already seen' : undefined,
    onClick: () => operation.run(onSeen, { requestId: item.id, revision: item.revision }),
  }];
  const settled = (item) => item.status !== 'pending';
  return (
    <Stack spacing={1}>
      {/* The tab label already carries the waiting count, so no count line repeats it. */}
      {(authored || data.pendingCount == null) && <Typography variant="s1" role="status">
        {authored ? 'Requests you authored. Open a request to inspect its status and decision.' : 'Assigned requests. Open a request to inspect its status and decision.'}
      </Typography>}
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
            ["pending", WAITING_FOR_APPROVAL],
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
          title={authored ? 'No requests sent yet' : 'No requests waiting for you'}
          description={authored ? 'Requests you send appear here.' : 'Requests assigned to you appear here.'}
        />
      ) : (
        <>
          {mobile ? <Stack aria-label="Assigned release requests">{rows.map((item) => <Box key={item.id} sx={{ position: 'relative', borderBottom: 1, borderColor: 'divider' }}>
            {/* One stacked card, and the whole card opens the request: name and status, the request title, then who and when. */}
            <ButtonBase aria-label={item.name} onClick={() => onOpen(item)} sx={{ display: 'block', width: 1, textAlign: 'left', py: 1.5, pr: 5, borderRadius: 1, '&.Mui-focusVisible': { outline: '2px solid', outlineColor: 'primary.main', outlineOffset: -2 } }}>
              <Stack spacing={0.5} sx={{ minWidth: 0 }}>
                <Stack direction="row" gap={1} alignItems="center" justifyContent="space-between" sx={{ minWidth: 0 }}>
                  <Stack direction="row" alignItems="center" sx={{ minWidth: 0 }}>{item.unread && item.status === 'pending' && <><Box component="span" aria-hidden="true" sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: 'primary.main', flexShrink: 0, mr: 1 }} /><Box component="span" sx={visuallyHidden}>Unread</Box></>}<Typography variant="s1" color={settled(item) ? 'text.secondary' : undefined} sx={{ overflowWrap: 'anywhere' }}>{item.name}</Typography></Stack>
                  <Box sx={{ flexShrink: 0 }}><StatusBadge status={item.status === 'pending' ? 'in_review' : item.status === 'changes_requested' ? 'needs_changes' : item.status} label={item.status === 'changes_requested' ? 'Changes requested' : item.status === 'pending' ? WAITING_FOR_APPROVAL : undefined} /></Box>
                </Stack>
                {item.transition && <Typography variant="s1" color={settled(item) ? 'text.secondary' : undefined}>{releaseTitle(item.transition)}</Typography>}
                <Typography variant="s3" color="text.secondary">{reviewPersonName(item.authorName)} · <When value={item.updatedAt} fallback="Time not reported" /></Typography>
              </Stack>
            </ButtonBase>
            <Box sx={{ position: 'absolute', top: 10, right: 0 }}><RowActions name={item.name} primary={primary(item)} actions={secondary(item)} phoneMenu /></Box>
          </Box>)}</Stack> : <TableContainer sx={{ maxWidth: 1 }}>
            <Table size="small" aria-label="Assigned release requests">
              <TableHead>
                <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
                  <TableCell>{RELEASE_REQUEST}</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell>Updated</TableCell>
                  <TableCell align="right">Actions</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {rows.map((item) => {
                  const { sx: linkSx, ...link } = rowLinkProps(primary(item));
                  return (
                  <TableRow
                    key={item.id}
                    {...link}
                    sx={[{ minHeight: ROW_HEIGHT.default }, linkSx || {}]}
                  >
                    <TableCell sx={{ width: { xs: '70%', sm: 'auto' } }}>
                      <Box sx={{ display: 'flex', alignItems: 'center', maxWidth: 1 }}>
                        {item.unread && item.status === 'pending' && <><Box component="span" aria-hidden="true" sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: 'primary.main', flexShrink: 0, mr: 1 }} /><Box component="span" sx={visuallyHidden}>Unread</Box></>}
                        <Typography
                          variant="s1"
                          noWrap
                          color={settled(item) ? 'text.secondary' : undefined}
                          sx={{
                            maxWidth: { xs: "16ch", sm: "40ch" },
                            fontWeight: item.unread ? 600 : 400,
                          }}
                        >
                          {item.name}{item.transition && ` · ${releaseTitle(item.transition)}`}
                        </Typography>
                      </Box>
                      <Typography variant="s3" display="block">
                        Requested by {reviewPersonName(item.authorName)} · {item.subjectKind === "agent_context"
                          ? "Agent context"
                          : "Skill"}

                      </Typography>
                      {item.status === 'pending' && <Typography variant="s3" color="text.secondary">{currentUserId && item.recipients?.some((person) => person.id === currentUserId) ? 'Waiting for your approval' : `Waiting on ${item.recipients?.map((person) => reviewPersonName(person.name)).join(', ') || 'the assigned reviewer'}`}</Typography>}
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={item.status === "pending" ? "in_review" : item.status === "changes_requested" ? "needs_changes" : item.status} label={item.status === "changes_requested" ? "Changes requested" : item.status === "pending" ? WAITING_FOR_APPROVAL : undefined} />
                    </TableCell>
                    <TableCell sx={{ color: 'text.secondary', width: { xs: '70%', sm: 'auto' }, pt: { xs: 0, sm: 1 } }}><When value={item.updatedAt} fallback="Time not reported" /></TableCell>
                    <RowActionsCell name={item.name} primary={primary(item)} actions={secondary(item)} inline="primary" />
                  </TableRow>
                  );
                })}
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
  currentUserId: PropTypes.string,
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
  currentUserId,
}) {
  return (
    <Box sx={skillsRouteSx}>
      <PageHeader
        title="Reviews"
        description="Review changes before they reach production."
        sx={{ mb: 1.5, flexDirection: { xs: 'column', sm: 'row' }, gap: 1.5, '& > .MuiStack-root + .MuiStack-root': { ml: { xs: 0, sm: 2 } } }}
        actions={scopeKey && !["off", "denied", "forbidden", "unavailable"].includes(state) && <>
          {onCompose && <Button variant="contained" color="primary" onClick={onCompose}>Request release</Button>}
        </>}
      />
      {scopeKey && !['off', 'denied', 'forbidden', 'unavailable'].includes(state) && onToggleAuthored && <CustomTabs value={authored ? 'mine' : 'assigned'} onChange={(_, value) => { if ((value === 'mine') !== authored) onToggleAuthored(); }} aria-label="Review inbox views" sx={{ mb: 1.5 }}><Tab value="assigned" label={data?.pendingCount ? `Assigned to me · ${data.pendingCount} waiting` : 'Assigned to me'} /><Tab value="mine" label="My requests" /></CustomTabs>}
      {!scopeKey || ['denied', 'forbidden'].includes(state) ? <CapabilityNotice title="You don't have access to reviews." description="Ask a workspace admin for reviewer access." actions={<RequestReviewerAccess />} /> : state === 'loading' ? <ContentSkeleton variant="rows" rows={5} label="Loading reviews" /> : state === 'error' ? <ErrorState title="Couldn't load reviews." onRetry={onRetry} /> : state === 'empty' || (state === 'ready' && data && !data.results.length) ? <EmptyState title={authored ? 'No requests sent yet' : 'No requests waiting for you'} description={authored ? 'Requests you send appear here.' : 'Requests assigned to you appear here.'} secondaryAction={!authored && onToggleAuthored ? <Button onClick={onToggleAuthored}>View my requests</Button> : undefined} /> : scopeKey && state === "ready" && data ? (
        <InboxRows
          key={scopeKey}
          {...{ data, status, onStatusChange, onPageChange, onOpen, onSeen, authored, currentUserId }}
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
  currentUserId: PropTypes.string,
};
