import PropTypes from "prop-types";
import { useMemo, useState } from "react";
import {
  Box,
  Button,
  Checkbox,
  Chip,
  CircularProgress,
  List,
  ListItemButton,
  ListItemText,
  Popover,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";

function memberId(member) {
  return member?.user_id || member?.id;
}

function memberName(member) {
  return member?.name || member?.email || "Unknown";
}

export default function ItemAssignmentPanel({
  item,
  annotators = [],
  currentUserId,
  canAnnotate = false,
  canManageAssignments = false,
  onAssign,
  isPending = false,
}) {
  const [anchorEl, setAnchorEl] = useState(null);
  const [search, setSearch] = useState("");
  const [selectedIds, setSelectedIds] = useState(new Set());

  const assignedUsers = item?.assigned_users || [];
  const hasAssignments = assignedUsers.length > 0;
  const isAssignedToMe = assignedUsers.some(
    (user) => String(user.id) === String(currentUserId),
  );
  const currentMember = annotators.find(
    (member) => String(memberId(member)) === String(currentUserId),
  );
  const canSelfAssign =
    Boolean(onAssign) &&
    canAnnotate &&
    Boolean(currentMember) &&
    !isAssignedToMe &&
    !hasAssignments;
  const canAssignToOthers = Boolean(onAssign) && canManageAssignments;

  const assignmentLabel = hasAssignments
    ? assignedUsers.map(memberName).join(", ")
    : "No annotator assigned";

  const filteredAnnotators = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return annotators;
    return annotators.filter((member) =>
      [member.name, member.email].some((value) =>
        String(value || "")
          .toLowerCase()
          .includes(needle),
      ),
    );
  }, [annotators, search]);

  const openPicker = (event) => {
    setSelectedIds(new Set(assignedUsers.map((user) => String(user.id))));
    setSearch("");
    setAnchorEl(event.currentTarget);
  };

  const closePicker = () => {
    setAnchorEl(null);
    setSearch("");
  };

  const assignToMe = () => {
    if (!currentUserId || !item?.id) return;
    onAssign?.({
      itemIds: [item.id],
      userIds: [String(currentUserId)],
      action: "add",
    });
  };

  const applySelection = () => {
    onAssign?.({
      itemIds: [item.id],
      userIds: Array.from(selectedIds),
      action: "set",
    });
    closePicker();
  };

  const toggleMember = (id) => {
    const value = String(id);
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(value)) next.delete(value);
      else next.add(value);
      return next;
    });
  };

  return (
    <Box
      sx={{
        m: 1,
        mb: 0,
        px: 1,
        py: 0.75,
        border: 1,
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.default",
      }}
    >
      <Stack direction="row" spacing={0.75} alignItems="center">
        <Chip
          size="small"
          label={hasAssignments ? "Assigned" : "Unassigned"}
          color={hasAssignments ? "default" : "warning"}
          variant={hasAssignments ? "outlined" : "soft"}
          sx={{ height: 22 }}
        />
        <Tooltip title={assignmentLabel}>
          <Typography
            variant="caption"
            noWrap
            sx={{ minWidth: 0, flex: 1, color: "text.secondary" }}
          >
            {assignmentLabel}
          </Typography>
        </Tooltip>
        {isPending && <CircularProgress size={14} />}
        {canSelfAssign && (
          <Button
            size="small"
            variant="contained"
            onClick={assignToMe}
            disabled={isPending}
            startIcon={<Iconify icon="solar:user-check-rounded-bold" />}
            sx={{ minHeight: 26, px: 1 }}
          >
            Assign to me
          </Button>
        )}
        {canAssignToOthers && (
          <Button
            size="small"
            variant="outlined"
            onClick={openPicker}
            disabled={isPending}
            startIcon={<Iconify icon="solar:users-group-rounded-bold" />}
            sx={{ minHeight: 26, px: 1 }}
          >
            Assign
          </Button>
        )}
      </Stack>

      <Popover
        open={Boolean(anchorEl)}
        anchorEl={anchorEl}
        onClose={closePicker}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
        slotProps={{ paper: { sx: { width: 280 } } }}
      >
        <Box sx={{ p: 1 }}>
          <TextField
            size="small"
            placeholder="Search annotators..."
            fullWidth
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            autoFocus
          />
        </Box>
        <List dense disablePadding sx={{ maxHeight: 260, overflow: "auto" }}>
          {filteredAnnotators.map((member) => {
            const id = String(memberId(member));
            const checked = selectedIds.has(id);
            return (
              <ListItemButton
                key={id}
                dense
                onClick={() => toggleMember(id)}
                sx={{ px: 1, py: 0.5 }}
              >
                <Checkbox
                  size="small"
                  checked={checked}
                  sx={{ p: 0.25, mr: 1 }}
                />
                <ListItemText
                  primary={memberName(member)}
                  secondary={member.name ? member.email : undefined}
                  primaryTypographyProps={{ variant: "body2", noWrap: true }}
                  secondaryTypographyProps={{
                    variant: "caption",
                    noWrap: true,
                  }}
                />
              </ListItemButton>
            );
          })}
          {filteredAnnotators.length === 0 && (
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", textAlign: "center", px: 1, py: 2 }}
            >
              No annotators found
            </Typography>
          )}
        </List>
        <Stack
          direction="row"
          spacing={1}
          sx={{ p: 1, borderTop: 1, borderColor: "divider" }}
        >
          <Button
            size="small"
            fullWidth
            variant="outlined"
            onClick={closePicker}
          >
            Cancel
          </Button>
          <Button
            size="small"
            fullWidth
            variant="contained"
            onClick={applySelection}
            disabled={isPending}
          >
            Apply
          </Button>
        </Stack>
      </Popover>
    </Box>
  );
}

ItemAssignmentPanel.propTypes = {
  item: PropTypes.object,
  annotators: PropTypes.array,
  currentUserId: PropTypes.string,
  canAnnotate: PropTypes.bool,
  canManageAssignments: PropTypes.bool,
  onAssign: PropTypes.func,
  isPending: PropTypes.bool,
};
