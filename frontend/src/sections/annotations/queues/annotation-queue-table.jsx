import PropTypes from "prop-types";
import { useState, useMemo, useCallback, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { useAuthContext } from "src/auth/hooks";
import { PERMISSIONS, RolePermission } from "src/utils/rolePermissionMapping";
import {
  Avatar,
  AvatarGroup,
  Box,
  IconButton,
  LinearProgress,
  MenuItem,
  Popover,
  Skeleton,
  Stack,
  Tooltip,
  Typography,
} from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import Iconify from "src/components/iconify";
import SvgColor from "src/components/svg-color";
import StatusBadge from "./components/status-badge";
import { fToNow } from "src/utils/format-time";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import { paths } from "src/routes/paths";
import "src/styles/clean-data-table.css";
import { QUEUE_ROLES, hasQueueRole, queueRoleList } from "./constants";

// Skeleton cell renderer shown during loading
const SkeletonCell = () => (
  <Box sx={{ display: "flex", alignItems: "center", height: "100%", px: 1 }}>
    <Skeleton variant="rounded" width="100%" height={20} />
  </Box>
);

// Placeholder rows shown while data is loading
const SKELETON_ROWS = Array.from({ length: 5 }, (_, i) => ({
  id: `skeleton-${i}`,
  _skeleton: true,
}));

export function getInitials(name, email) {
  if (name) {
    const parts = name.trim().split(/\s+/);
    if (parts.length >= 2)
      return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
    return parts[0][0].toUpperCase();
  }
  if (email) return email[0].toUpperCase();
  return "?";
}

// ---------------------------------------------------------------------------
// Cell renderers
// ---------------------------------------------------------------------------
NameCellRenderer.propTypes = {
  data: PropTypes.object,
};

function NameCellRenderer({ data }) {
  if (!data) return null;
  return (
    <Box
      sx={{
        py: 1,
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        gap: 0.5,
        height: "100%",
      }}
    >
      <Typography variant="body2" fontWeight={600} noWrap>
        {data.name}
      </Typography>
      {data.description && (
        <Typography
          variant="caption"
          color="text.secondary"
          noWrap
          sx={{ maxWidth: 300 }}
        >
          {data.description}
        </Typography>
      )}
    </Box>
  );
}

StatusCellRenderer.propTypes = {
  data: PropTypes.object,
};

function StatusCellRenderer({ data }) {
  if (!data) return null;
  return (
    <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
      <StatusBadge status={data.status} />
    </Box>
  );
}

ProgressCellRenderer.propTypes = {
  data: PropTypes.object,
};

function ProgressCellRenderer({ data }) {
  if (!data) return null;
  const total = data.item_count ?? 0;
  const done = data.completed_count ?? 0;
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;

  if (total <= 0) {
    return (
      <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
        <Typography variant="body2" color="text.secondary">
          No items
        </Typography>
      </Box>
    );
  }

  return (
    <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
      <Stack spacing={0.5} sx={{ minWidth: 100 }}>
        <Typography variant="caption">
          {done}/{total} ({pct}%)
        </Typography>
        <LinearProgress
          variant="determinate"
          value={pct}
          sx={{
            height: 4,
            borderRadius: 2,
            backgroundColor: "action.disabled", // background color
            "& .MuiLinearProgress-bar": {
              backgroundColor: "success.main", // progress bar color
            },
          }}
        />
      </Stack>
    </Box>
  );
}

LabelsCellRenderer.propTypes = {
  data: PropTypes.object,
};

function LabelsCellRenderer({ data }) {
  if (!data) return null;
  const count = data.label_count ?? data.labels?.length ?? 0;
  return (
    <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
      <Typography variant="body2">{count} labels</Typography>
    </Box>
  );
}

const MEMBER_ROLE_GROUPS = [
  { role: QUEUE_ROLES.MANAGER, label: "Managers" },
  { role: QUEUE_ROLES.ANNOTATOR, label: "Annotators" },
  { role: QUEUE_ROLES.REVIEWER, label: "Reviewers" },
];

function roleTitle(role) {
  if (!role) return "Annotators";
  return role.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function memberDisplayName(member) {
  if (member.name && member.email) return `${member.name} (${member.email})`;
  return member.name || member.email || "Unnamed";
}

function groupQueueMembers(members) {
  const knownGroups = MEMBER_ROLE_GROUPS.map((group) => ({
    ...group,
    members: members.filter((member) => hasQueueRole(member, group.role)),
  })).filter((group) => group.members.length > 0);

  const knownRoles = new Set(MEMBER_ROLE_GROUPS.map((group) => group.role));
  const unknownGroups = Object.entries(
    members
      .flatMap((member) =>
        queueRoleList(member)
          .filter((role) => !knownRoles.has(role))
          .map((role) => ({ role, member })),
      )
      .reduce((acc, { role, member }) => {
        acc[role] = [...(acc[role] || []), member];
        return acc;
      }, {}),
  ).map(([role, groupMembers]) => ({
    role,
    label: roleTitle(role),
    members: groupMembers,
  }));

  return [...knownGroups, ...unknownGroups];
}

MembersCellRenderer.propTypes = {
  data: PropTypes.object,
};

function MembersCellRenderer({ data }) {
  if (!data) return null;
  const members = data.annotators || [];

  if (members.length === 0) {
    return (
      <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
        <Typography variant="body2" color="text.secondary">
          —
        </Typography>
      </Box>
    );
  }

  return (
    <Box
      data-testid={`queue-members-${data.id}`}
      sx={{ display: "flex", alignItems: "center", height: "100%" }}
    >
      <Tooltip
        title={
          <Stack spacing={1} sx={{ py: 0.5 }}>
            {groupQueueMembers(members).map((group) => (
              <Stack key={group.role} spacing={0.25}>
                <Typography
                  variant="caption"
                  color="text.secondary"
                  fontWeight={700}
                >
                  {group.label}
                </Typography>
                {group.members.map((member) => (
                  <Typography
                    key={member.id || member.user_id}
                    variant="caption"
                  >
                    {memberDisplayName(member)}
                  </Typography>
                ))}
              </Stack>
            ))}
          </Stack>
        }
        arrow
        placement="top"
      >
        <AvatarGroup
          max={4}
          sx={{
            "& .MuiAvatar-root": {
              width: 28,
              height: 28,
              fontSize: 12,
              fontWeight: 600,
              border: "2px solid",
              borderColor: "background.paper",
            },
            // Surplus avatar (+N)
            "& .MuiAvatar-root:first-of-type": {
              color: "grey.800",
            },
            // All other avatars
            "& .MuiAvatar-root:not(:first-of-type)": {
              bgcolor: "secondary.main",
              color: "secondary.contrastText",
            },
          }}
        >
          {members.map((a) => (
            <Avatar key={a.id || a.user_id} aria-label={memberDisplayName(a)}>
              {getInitials(a.name, a.email)}
            </Avatar>
          ))}
        </AvatarGroup>
      </Tooltip>
    </Box>
  );
}

CreatedCellRenderer.propTypes = {
  data: PropTypes.object,
};

function CreatedCellRenderer({ data }) {
  if (!data) return null;
  const date = data.created_at;
  return (
    <Box sx={{ display: "flex", alignItems: "center", height: "100%" }}>
      <Typography variant="body2" color="text.secondary">
        {date ? fToNow(date) : "—"}
      </Typography>
    </Box>
  );
}

// ---------------------------------------------------------------------------
// Actions cell – uses context to trigger the popover menu in the parent
// ---------------------------------------------------------------------------
ActionsCellRenderer.propTypes = {
  data: PropTypes.object,
  context: PropTypes.object,
};

function ActionsCellRenderer({ data, context }) {
  if (!data) return null;
  const currentUserId = context?.currentUserId;
  const annotators = data.annotators || [];
  const myEntry = annotators.find(
    (a) => String(a.user_id) === String(currentUserId),
  );
  const isQueueManager = hasQueueRole(myEntry, QUEUE_ROLES.MANAGER);
  // Show menu only for queue managers
  if (!isQueueManager) return null;
  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        height: "100%",
      }}
    >
      <IconButton
        size="small"
        onClick={(e) => {
          e.stopPropagation();
          context?.onOpenMenu(e, data);
        }}
      >
        <Iconify icon="eva:more-vertical-fill" />
      </IconButton>
    </Box>
  );
}

// ---------------------------------------------------------------------------
// Main table component
// ---------------------------------------------------------------------------
AnnotationQueueTable.propTypes = {
  data: PropTypes.array,
  loading: PropTypes.bool,
  page: PropTypes.number.isRequired,
  rowsPerPage: PropTypes.number.isRequired,
  totalCount: PropTypes.number.isRequired,
  onPageChange: PropTypes.func,
  onRowsPerPageChange: PropTypes.func,
  onEdit: PropTypes.func,
  onDuplicate: PropTypes.func,
  onArchive: PropTypes.func,
  onStatusChange: PropTypes.func,
};

export default function AnnotationQueueTable({
  data = [],
  loading,
  page,
  rowsPerPage,
  totalCount,
  onPageChange,
  onRowsPerPageChange,
  onEdit,
  onDuplicate,
  onArchive,
  onStatusChange,
}) {
  const navigate = useNavigate();
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.noHeaderBorder);
  const { user, role } = useAuthContext();
  const currentUserId = user?.id || user?.pk;
  const canWrite = RolePermission.DATASETS[PERMISSIONS.CREATE][role];
  const gridRef = useRef(null);
  const [menuAnchor, setMenuAnchor] = useState(null);
  const [menuQueueId, setMenuQueueId] = useState(null);

  // Always resolve menuQueue from current data so status changes are reflected
  const menuQueue = useMemo(
    () => data.find((q) => q.id === menuQueueId) || null,
    [data, menuQueueId],
  );

  const handleOpenMenu = useCallback((event, queue) => {
    setMenuAnchor(event.currentTarget);
    setMenuQueueId(queue.id);
  }, []);

  const handleCloseMenu = () => {
    setMenuAnchor(null);
    setMenuQueueId(null);
  };

  const handleAction = (action) => {
    const queue = menuQueue;
    handleCloseMenu();
    if (action === "edit") onEdit?.(queue);
    else if (action === "duplicate") onDuplicate?.(queue);
    else if (action === "archive") onArchive?.(queue);
  };

  const getStatusActions = (queue) => {
    const transitions = {
      draft: [
        { value: "active", label: "Activate", icon: "eva:play-circle-fill" },
      ],
      active: [
        { value: "paused", label: "Pause", icon: "eva:pause-circle-fill" },
      ],
      paused: [
        { value: "active", label: "Resume", icon: "eva:play-circle-fill" },
      ],
      completed: [
        { value: "active", label: "Re-open", icon: "eva:refresh-fill" },
        {
          value: "paused",
          label: "Pause to Edit",
          icon: "eva:pause-circle-fill",
        },
      ],
    };
    return transitions[queue?.status] || [];
  };

  const columnDefs = useMemo(() => {
    // When loading, use skeleton cell renderer for all columns
    const _skeletonRenderer = loading ? { cellRenderer: SkeletonCell } : {};
    return [
      {
        field: "name",
        headerName: "Name",
        flex: 2,
        minWidth: 220,
        cellRenderer: loading ? SkeletonCell : NameCellRenderer,
      },
      {
        field: "status",
        headerName: "Status",
        flex: 1,
        minWidth: 120,
        cellRenderer: loading ? SkeletonCell : StatusCellRenderer,
      },
      {
        field: "progress",
        headerName: "Progress",
        flex: 1.2,
        minWidth: 150,
        cellRenderer: loading ? SkeletonCell : ProgressCellRenderer,
      },
      {
        field: "labels",
        headerName: "Labels",
        flex: 1,
        minWidth: 100,
        cellRenderer: loading ? SkeletonCell : LabelsCellRenderer,
      },
      {
        field: "annotators",
        headerName: "Members",
        flex: 1,
        minWidth: 130,
        cellRenderer: loading ? SkeletonCell : MembersCellRenderer,
      },
      {
        field: "created_at",
        headerName: "Created",
        flex: 1.2,
        minWidth: 140,
        cellRenderer: loading ? SkeletonCell : CreatedCellRenderer,
      },
      {
        field: "actions",
        headerName: "",
        width: 60,
        maxWidth: 60,
        cellRenderer: loading ? SkeletonCell : ActionsCellRenderer,
        sortable: false,
        resizable: false,
      },
    ];
  }, [loading]);

  const defaultColDef = useMemo(
    () => ({
      lockVisible: true,
      filter: false,
      sortable: false,
      resizable: false,
      suppressHeaderMenuButton: true,
      suppressHeaderContextMenu: true,
    }),
    [],
  );

  const gridContext = useMemo(
    () => ({ onOpenMenu: handleOpenMenu, currentUserId, canWrite }),
    [handleOpenMenu, currentUserId, canWrite],
  );

  const onCellClicked = useCallback(
    (event) => {
      if (!event?.data) return;
      if (event.column?.getColId() === "actions") return;
      navigate(paths.dashboard.annotations.queueDetail(event.data.id));
    },
    [navigate],
  );

  const getRowId = useCallback((params) => params.data?.id, []);

  // Compute paginated slice — the parent manages pagination state, and
  // the API returns already-paginated data in `data`, so just pass it through.
  const CustomNoRowsOverlay = useCallback(
    () => (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
        }}
      >
        <Typography variant="body2" color="text.secondary">
          No queues match your filters
        </Typography>
      </Box>
    ),
    [],
  );

  return (
    <>
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          flex: 1,
          overflow: "hidden",
        }}
      >
        <Box sx={{ flex: 1 }}>
          <AgGridReact
            ref={gridRef}
            theme={agTheme}
            rowData={loading ? SKELETON_ROWS : data}
            columnDefs={columnDefs}
            defaultColDef={defaultColDef}
            context={gridContext}
            rowHeight={64}
            headerHeight={42}
            pagination={false}
            animateRows={false}
            suppressRowClickSelection
            rowStyle={{ cursor: loading ? "default" : "pointer" }}
            onCellClicked={loading ? undefined : onCellClicked}
            getRowId={getRowId}
            noRowsOverlayComponent={CustomNoRowsOverlay}
          />
        </Box>

        {/* Pagination — sticky at the bottom */}
        <Box
          sx={{
            display: "flex",
            justifyContent: "flex-end",
            alignItems: "center",
            gap: 2,
            py: 1,
            px: 2,
            fontSize: 14,
            color: "text.secondary",
            flexShrink: 0,
            borderTop: "1px solid",
            borderColor: "divider",
          }}
        >
          <Box component="span">
            Rows per page:
            <Box
              component="select"
              value={rowsPerPage}
              onChange={(e) =>
                onRowsPerPageChange?.(parseInt(e.target.value, 10))
              }
              sx={{
                ml: 1,
                border: "none",
                background: "transparent",
                color: "text.primary",
                fontSize: 14,
                cursor: "pointer",
                outline: "none",
              }}
            >
              {[10, 25, 50].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </Box>
          </Box>
          <Typography variant="body2" color="text.secondary">
            {totalCount > 0
              ? `${page * rowsPerPage + 1}–${Math.min((page + 1) * rowsPerPage, totalCount)} of ${totalCount}`
              : "0 of 0"}
          </Typography>
          <IconButton
            size="small"
            disabled={page === 0}
            onClick={() => onPageChange?.(page - 1)}
          >
            <Iconify icon="eva:chevron-left-fill" width={20} />
          </IconButton>
          <IconButton
            size="small"
            disabled={(page + 1) * rowsPerPage >= totalCount}
            onClick={() => onPageChange?.(page + 1)}
          >
            <Iconify icon="eva:chevron-right-fill" width={20} />
          </IconButton>
        </Box>
      </Box>

      {/* Actions popover menu */}
      <Popover
        open={Boolean(menuAnchor)}
        anchorEl={menuAnchor}
        onClose={handleCloseMenu}
        anchorOrigin={{ vertical: "top", horizontal: "right" }}
        transformOrigin={{ vertical: "top", horizontal: "right" }}
      >
        <Box sx={{ py: 0.5 }}>
          <MenuItem onClick={() => handleAction("edit")}>
            <SvgColor
              src="/assets/icons/ic_edit.svg"
              sx={{ width: 18, height: 18, mr: 1 }}
            />
            Edit
          </MenuItem>
          <MenuItem onClick={() => handleAction("duplicate")}>
            <SvgColor
              src="/assets/icons/ic_duplicate.svg"
              sx={{ width: 18, height: 18, mr: 1 }}
            />
            Duplicate
          </MenuItem>

          {getStatusActions(menuQueue).map((sa) => (
            <MenuItem
              key={sa.value}
              onClick={() => {
                const queue = menuQueue;
                setMenuAnchor(null);
                onStatusChange?.(queue, sa.value);
              }}
            >
              <Iconify icon={sa.icon} width={18} sx={{ mr: 1 }} />
              {sa.label}
            </MenuItem>
          ))}

          <MenuItem
            onClick={() => handleAction("archive")}
            sx={{ color: "warning.main" }}
          >
            <Iconify icon="solar:archive-down-bold" width={18} sx={{ mr: 1 }} />
            Archive
          </MenuItem>
        </Box>
      </Popover>
    </>
  );
}
