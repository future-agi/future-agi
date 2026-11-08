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
} from "@mui/material";
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
}) {
  const [menu, setMenu] = useState(null);
  const operation = useReviewedOperation();
  const row = data.results.find((item) => item.id === menu?.id);
  return (
    <Stack spacing={1}>
      <Typography variant="s1" role="status">
        {data.unreadCount} unread assigned request(s)
      </Typography>
      <ListToolbar>
        <TextField
          select
          size="small"
          label="Request status"
          value={status}
          onChange={(event) => onStatusChange(event.target.value)}
        >
          <MenuItem value="">All statuses</MenuItem>
          {[
            ["pending", "Pending"],
            ["approved", "Approved"],
            ["changes_requested", "Changes requested"],
            ["released", "Released"],
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
          title="No assigned reviews"
          description="Requests appear here when an author explicitly assigns an immutable release packet to you. Unavailable private evidence is excluded by the server."
        />
      ) : (
        <>
          <TableContainer sx={{ maxWidth: 1 }}>
            <Table size="small" aria-label="Assigned release reviews">
              <TableHead>
                <TableRow sx={{ height: TABLE_HEADER_HEIGHT.default }}>
                  <TableCell>Candidate</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell>Review</TableCell>
                  <TableCell align="right">Actions</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {data.results.map((item) => (
                  <TableRow
                    key={item.id}
                    hover
                    sx={{ height: ROW_HEIGHT.default }}
                  >
                    <TableCell>
                      <Button
                        color="inherit"
                        onClick={() => onOpen(item)}
                        sx={{ maxWidth: 1 }}
                      >
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
                        · packet r{item.revision}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={item.status} />
                    </TableCell>
                    <TableCell>
                      <Typography variant="s3">
                        {item.unread ? "Unread" : "Seen — not approval"}
                      </Typography>
                    </TableCell>
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
          </TableContainer>
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
}) {
  return (
    <Box sx={{ px: { xs: 2, sm: 3 }, py: 2, minWidth: 0 }}>
      <PageHeader
        title="Skills reviews"
        description="Assigned release decisions for this workspace. Reading a request never approves it."
        sx={{ mb: 0 }}
      />
      {scopeKey && state === "ready" && data ? (
        <InboxRows
          key={scopeKey}
          {...{ data, status, onStatusChange, onPageChange, onOpen, onSeen }}
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
};
