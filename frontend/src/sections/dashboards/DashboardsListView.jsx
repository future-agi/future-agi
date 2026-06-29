import React, { useState, useMemo } from "react";
import PropTypes from "prop-types";
import {
  Avatar,
  AvatarGroup,
  Box,
  Button,
  Checkbox,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  IconButton,
  Menu,
  MenuItem,
  Stack,
  TextField,
  Tooltip,
  Typography,
  useTheme,
} from "@mui/material";
import { useNavigate } from "react-router-dom";
import { paths } from "src/routes/paths";
import { useAuthContext } from "src/auth/hooks";
import {
  useDashboardList,
  useCreateDashboard,
  useDeleteDashboard,
} from "src/hooks/useDashboards";
import Iconify from "src/components/iconify";
import FormSearchField from "src/components/FormSearchField/FormSearchField";
import SvgColor from "src/components/svg-color";
import EmptyLayout from "src/components/EmptyLayout/EmptyLayout";
import { ConfirmDialog } from "src/components/custom-dialog";
import { useSnackbar } from "src/components/snackbar";
import { formatDistanceToNowStrict, format } from "date-fns";

const AVATAR_COLORS = [
  "#7C4DFF",
  "#FF6B6B",
  "#5BE49B",
  "#FFB547",
  "#36B5FF",
  "#FF85C0",
  "#00BFA6",
  "#8C9EFF",
];

function getAvatarColor(name) {
  let hash = 0;
  for (let i = 0; i < (name || "").length; i++) {
    hash = name.charCodeAt(i) + ((hash << 5) - hash);
  }
  return AVATAR_COLORS[Math.abs(hash) % AVATAR_COLORS.length];
}

function getInitials(name) {
  if (!name) return "?";
  const parts = name.trim().split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return name.slice(0, 2).toUpperCase();
}

function timeAgo(date) {
  if (!date) return "";
  try {
    return formatDistanceToNowStrict(new Date(date), { addSuffix: true });
  } catch {
    return "";
  }
}

function getDashboardViewers(db) {
  const users = [];
  const seen = new Set();
  const addUser = (u, time) => {
    if (!u || !u.email || seen.has(u.email)) return;
    seen.add(u.email);
    users.push({ ...u, displayName: u.name || u.email, time });
  };
  addUser(db.updated_by, db.updated_at);
  addUser(db.created_by, db.created_at);
  return users;
}

function ViewerAvatars({ db }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const viewers = getDashboardViewers(db);
  if (!viewers.length) return null;

  const shown = viewers.slice(0, 3);
  const extra = viewers.length - 3;

  return (
    <Tooltip
      placement="bottom-start"
      arrow
      componentsProps={{
        tooltip: {
          sx: {
            bgcolor: isDark ? "#1a1a2e" : "#fff",
            borderRadius: 2,
            p: 2,
            minWidth: 240,
            boxShadow: isDark
              ? "0 4px 20px rgba(0,0,0,0.5)"
              : "0 4px 20px rgba(0,0,0,0.12)",
            border: isDark ? "none" : "1px solid",
            borderColor: isDark ? "transparent" : "divider",
          },
        },
        arrow: {
          sx: {
            color: isDark ? "#1a1a2e" : "#fff",
            "&::before": {
              border: isDark ? "none" : "1px solid",
              borderColor: isDark ? "transparent" : "divider",
            },
          },
        },
      }}
      title={
        <Box>
          {/* Created by */}
          {db.created_by && (
            <Stack
              direction="row"
              alignItems="center"
              gap={1.5}
              sx={{ mb: 1.5 }}
            >
              <Avatar
                sx={{
                  width: 28,
                  height: 28,
                  fontSize: "11px",
                  fontWeight: 700,
                  bgcolor: getAvatarColor(
                    db.created_by.name || db.created_by.email,
                  ),
                }}
              >
                {getInitials(db.created_by.name || db.created_by.email)}
              </Avatar>
              <Box>
                <Typography
                  variant="body2"
                  sx={{
                    fontWeight: 500,
                    color: isDark ? "#fff" : "text.primary",
                    lineHeight: 1.3,
                  }}
                >
                  Created by {db.created_by.name || db.created_by.email}
                </Typography>
                <Typography
                  variant="caption"
                  sx={{
                    color: isDark ? "rgba(255,255,255,0.45)" : "text.secondary",
                  }}
                >
                  {db.created_at
                    ? format(
                        new Date(db.created_at),
                        "MMM d, yyyy \u00b7 h:mm a",
                      )
                    : ""}
                </Typography>
              </Box>
            </Stack>
          )}

          {/* Divider */}
          <Box
            sx={{
              borderTop: "1px solid",
              borderColor: isDark ? "rgba(255,255,255,0.1)" : "divider",
              mb: 1.5,
            }}
          />

          {/* Recently Viewed By */}
          <Typography
            variant="caption"
            sx={{
              color: isDark ? "rgba(255,255,255,0.5)" : "text.disabled",
              fontWeight: 600,
              mb: 1.5,
              display: "block",
            }}
          >
            Recently Viewed By:
          </Typography>
          <Stack gap={1.5}>
            {viewers.map((v, i) => (
              <Stack key={i} direction="row" alignItems="center" gap={1.5}>
                <Avatar
                  sx={{
                    width: 28,
                    height: 28,
                    fontSize: "11px",
                    fontWeight: 700,
                    bgcolor: getAvatarColor(v.displayName),
                  }}
                >
                  {getInitials(v.displayName)}
                </Avatar>
                <Typography
                  variant="body2"
                  sx={{
                    flex: 1,
                    fontWeight: 500,
                    color: isDark ? "#fff" : "text.primary",
                  }}
                >
                  {v.displayName}
                </Typography>
                <Typography
                  variant="caption"
                  sx={{
                    color: isDark ? "rgba(255,255,255,0.45)" : "text.secondary",
                    whiteSpace: "nowrap",
                  }}
                >
                  {timeAgo(v.time)}
                </Typography>
              </Stack>
            ))}
          </Stack>

          {/* Last edited footer */}
          {db.updated_by && (
            <Box
              sx={{
                borderTop: "1px solid",
                borderColor: isDark ? "rgba(255,255,255,0.1)" : "divider",
                mt: 1.5,
                pt: 1.5,
              }}
            >
              <Typography
                variant="caption"
                sx={{
                  color: isDark ? "rgba(255,255,255,0.5)" : "text.secondary",
                  display: "block",
                }}
              >
                Last edited by {db.updated_by.name || db.updated_by.email}
              </Typography>
              <Typography
                variant="caption"
                sx={{
                  color: isDark ? "rgba(255,255,255,0.5)" : "text.secondary",
                }}
              >
                {db.updated_at
                  ? format(new Date(db.updated_at), "MMM d, yyyy \u00b7 h:mm a")
                  : ""}
              </Typography>
            </Box>
          )}
        </Box>
      }
    >
      <Stack
        direction="row"
        alignItems="center"
        gap={0.5}
        onClick={(e) => e.stopPropagation()}
        sx={{ cursor: "default" }}
      >
        <AvatarGroup
          max={3}
          sx={{
            "& .MuiAvatar-root": {
              width: 26,
              height: 26,
              fontSize: "11px",
              fontWeight: 700,
              borderWidth: 2,
            },
          }}
        >
          {shown.map((v, i) => (
            <Avatar key={i} sx={{ bgcolor: getAvatarColor(v.displayName) }}>
              {getInitials(v.displayName)}
            </Avatar>
          ))}
        </AvatarGroup>
        {extra > 0 && (
          <Typography variant="caption" color="text.secondary">
            + {extra}
          </Typography>
        )}
      </Stack>
    </Tooltip>
  );
}

ViewerAvatars.propTypes = {
  db: PropTypes.shape({
    created_by: PropTypes.shape({
      name: PropTypes.string,
      email: PropTypes.string,
    }),
    created_at: PropTypes.string,
    updated_by: PropTypes.shape({
      name: PropTypes.string,
      email: PropTypes.string,
    }),
    updated_at: PropTypes.string,
  }).isRequired,
};

export default function DashboardsListView() {
  const theme = useTheme();
  const navigate = useNavigate();
  const { enqueueSnackbar } = useSnackbar();
  const { user } = useAuthContext();

  const { data: dashboards = [], isLoading } = useDashboardList();
  const createMutation = useCreateDashboard();
  const deleteMutation = useDeleteDashboard();

  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [creatorFilter, setCreatorFilter] = useState([]);
  const [creatorMenuAnchor, setCreatorMenuAnchor] = useState(null);

  const creators = useMemo(() => {
    const map = new Map();
    dashboards.forEach((d) => {
      const u = d.created_by;
      if (u?.email && !map.has(u.email)) {
        map.set(u.email, u.name || u.email);
      }
    });
    return Array.from(map, ([email, name]) => ({ email, name }));
  }, [dashboards]);

  const filteredDashboards = useMemo(() => {
    let list = dashboards;
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      list = list.filter((d) => d.name?.toLowerCase().includes(q));
    }
    if (creatorFilter.length > 0) {
      list = list.filter((d) => creatorFilter.includes(d.created_by?.email));
    }
    return list;
  }, [dashboards, searchQuery, creatorFilter]);

  const handleCreate = async () => {
    const name = newName.trim() || "Untitled";

    try {
      const res = await createMutation.mutateAsync({
        name,
        description: newDescription.trim(),
      });
      const id = res.data?.result?.id;
      if (id) navigate(paths.dashboard.dashboards.detail(id));
      setNewName("");
      setNewDescription("");
    } catch {
      enqueueSnackbar("Failed to create dashboard", { variant: "error" });
    }
  };

  const [deleteTarget, setDeleteTarget] = useState(null);

  const handleDelete = (e, db) => {
    e.stopPropagation();
    setDeleteTarget(db);
  };

  const confirmDelete = () => {
    if (!deleteTarget) return;
    deleteMutation.mutate(deleteTarget.id, {
      onSuccess: () =>
        enqueueSnackbar("Dashboard deleted", { variant: "success" }),
      onError: () => enqueueSnackbar("Failed to delete", { variant: "error" }),
    });
    setDeleteTarget(null);
  };

  if (isLoading) {
    return (
      <Box
        sx={{
          display: "flex",
          justifyContent: "center",
          alignItems: "center",
          height: "60vh",
        }}
      >
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Box
      sx={{
        padding: theme.spacing(2),
        display: "flex",
        flex: 1,
        flexDirection: "column",
        gap: theme.spacing(2),
        bgcolor: "background.paper",
        height: "100%",
      }}
    >
      {/* Header */}
      <Stack gap={theme.spacing(0.5)}>
        <Typography
          color="text.primary"
          typography="m2"
          fontWeight="fontWeightSemiBold"
        >
          Dashboard
        </Typography>
        <Typography
          typography="s1"
          color="text.primary"
          fontWeight="fontWeightRegular"
        >
          Create dashboard to monitor
        </Typography>
      </Stack>

      {/* Search + Actions row */}
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Stack direction="row" gap={1} alignItems="center">
          <FormSearchField
            size="small"
            placeholder="Search"
            searchQuery={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            sx={{
              minWidth: "250px",
              "& .MuiOutlinedInput-root": { height: "30px" },
            }}
          />
          <Button
            size="small"
            variant={creatorFilter.length > 0 ? "contained" : "outlined"}
            onClick={(e) => setCreatorMenuAnchor(e.currentTarget)}
            startIcon={<Iconify icon="mdi:account-outline" width={18} />}
            endIcon={<Iconify icon="mdi:chevron-down" width={16} />}
            sx={{
              height: 38,
              borderColor: "divider",
              color: creatorFilter.length > 0 ? undefined : "text.secondary",
              textTransform: "none",
              fontSize: "13px",
              whiteSpace: "nowrap",
            }}
          >
            {creatorFilter.length === 0
              ? "Created by anyone"
              : creatorFilter.length === 1
                ? creators.find((c) => c.email === creatorFilter[0])?.name ||
                  creatorFilter[0]
                : `${creatorFilter.length} creators`}
          </Button>
          <Menu
            anchorEl={creatorMenuAnchor}
            open={Boolean(creatorMenuAnchor)}
            onClose={() => setCreatorMenuAnchor(null)}
            slotProps={{
              paper: {
                sx: { minWidth: 220, mt: 0.5 },
              },
            }}
          >
            <MenuItem onClick={() => setCreatorFilter([])} sx={{ py: 0.5 }}>
              <Checkbox
                size="small"
                checked={creatorFilter.length === 0}
                sx={{ mr: 0.5 }}
              />
              <Stack direction="row" alignItems="center" gap={1}>
                <Iconify icon="mdi:account-group-outline" width={18} />
                <Typography variant="body2">Anyone</Typography>
              </Stack>
            </MenuItem>
            <Divider />
            {creators.map((c) => {
              const checked = creatorFilter.includes(c.email);
              return (
                <MenuItem
                  key={c.email}
                  onClick={() => {
                    setCreatorFilter((prev) =>
                      checked
                        ? prev.filter((e) => e !== c.email)
                        : [...prev, c.email],
                    );
                  }}
                  sx={{ py: 0.5 }}
                >
                  <Checkbox size="small" checked={checked} sx={{ mr: 0.5 }} />
                  <Stack direction="row" alignItems="center" gap={1}>
                    <Avatar
                      sx={{
                        width: 22,
                        height: 22,
                        fontSize: "10px",
                        fontWeight: 700,
                        bgcolor: getAvatarColor(c.name),
                      }}
                    >
                      {getInitials(c.name)}
                    </Avatar>
                    <Typography variant="body2">{c.name}</Typography>
                  </Stack>
                </MenuItem>
              );
            })}
          </Menu>
        </Stack>
        <Stack direction="row" gap={1}>
          <Button
            variant="outlined"
            size="small"
            sx={{
              borderRadius: "4px",
              height: "30px",
              px: "4px",
              width: "105px",
            }}
            onClick={() => {
              window.open(
                "https://docs.futureagi.com/docs/observe/features/dashboard",
                "_blank",
              );
            }}
          >
            <SvgColor
              src="/assets/icons/agent/docs.svg"
              sx={{ height: 16, width: 16, mr: 1 }}
            />
            <Typography typography="s2" fontWeight="fontWeightMedium">
              View Docs
            </Typography>
          </Button>
          <Button
            variant="contained"
            color="primary"
            startIcon={
              createMutation.isPending ? (
                <CircularProgress size={16} color="inherit" />
              ) : (
                <Iconify icon="mdi:plus" />
              )
            }
            onClick={handleCreate}
            disabled={createMutation.isPending}
            sx={{ height: "38px" }}
          >
            {createMutation.isPending ? "Creating..." : "Create Dashboard"}
          </Button>
        </Stack>
      </Stack>

      {/* Dashboard list */}
      <Box sx={{ flex: 1 }}>
        {filteredDashboards.length === 0 ? (
          searchQuery ? (
            <Stack alignItems="center" gap={1} sx={{ py: 8 }}>
              <Iconify
                icon="mdi:magnify"
                width={48}
                sx={{ color: "text.disabled" }}
              />
              <Typography variant="body2" color="text.secondary">
                No dashboards match your search
              </Typography>
            </Stack>
          ) : (
            <EmptyLayout
              title="Create your first dashboard"
              description="Build custom dashboards to visualize traces, evaluations, and simulation metrics in one place."
              link="https://docs.futureagi.com"
              linkText="Learn more"
              icon="/assets/icons/navbar/ic_dashboard.svg"
            />
          )
        ) : (
          <Stack spacing={1}>
            {filteredDashboards.map((db) => (
              <Stack
                key={db.id}
                direction="row"
                alignItems="center"
                onClick={() =>
                  navigate(paths.dashboard.dashboards.detail(db.id))
                }
                sx={{
                  px: 2,
                  py: 1.25,
                  cursor: "pointer",
                  borderRadius: 1.5,
                  border: (t) =>
                    `1px solid ${
                      t.palette.mode === "dark"
                        ? "rgba(255,255,255,0.08)"
                        : "rgba(0,0,0,0.08)"
                    }`,
                  transition: "all 0.15s",
                  "&:hover": {
                    bgcolor: (t) =>
                      t.palette.mode === "dark"
                        ? "rgba(255,255,255,0.04)"
                        : "rgba(0,0,0,0.02)",
                    borderColor: (t) =>
                      t.palette.mode === "dark"
                        ? "rgba(255,255,255,0.16)"
                        : "rgba(0,0,0,0.16)",
                    "& .row-actions": { opacity: 1 },
                  },
                }}
              >
                <Iconify
                  icon="mdi:view-dashboard-outline"
                  width={18}
                  sx={{ color: "primary.main", mr: 1.5, flexShrink: 0 }}
                />

                <Typography
                  variant="body2"
                  fontWeight={600}
                  noWrap
                  sx={{ flex: 1, mr: 2, minWidth: 0 }}
                >
                  {db.name}
                </Typography>

                <Typography
                  variant="caption"
                  color="text.disabled"
                  sx={{ mr: 1.5, whiteSpace: "nowrap", flexShrink: 0 }}
                >
                  {db.widget_count || 0} widget
                  {db.widget_count !== 1 ? "s" : ""}
                </Typography>

                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ mr: 2, whiteSpace: "nowrap", flexShrink: 0 }}
                >
                  {timeAgo(db.updated_at || db.created_at)}
                </Typography>

                <Box sx={{ mr: 1, flexShrink: 0 }}>
                  <ViewerAvatars db={db} />
                </Box>

                {db.created_by?.email === user?.email && (
                  <IconButton
                    className="row-actions"
                    size="small"
                    onClick={(e) => handleDelete(e, db)}
                    sx={{
                      opacity: 0,
                      transition: "opacity 0.15s",
                      flexShrink: 0,
                    }}
                  >
                    <Iconify icon="mdi:delete-outline" width={18} />
                  </IconButton>
                )}
              </Stack>
            ))}
          </Stack>
        )}
      </Box>

      {/* Create dialog */}
      <Dialog
        open={createOpen}
        onClose={() => {
          setCreateOpen(false);
          setNewName("");
          setNewDescription("");
        }}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle sx={{ pb: 0 }}>
          <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="center"
          >
            <Typography variant="h6" fontWeight="fontWeightSemiBold">
              Create Custom Dashboard
            </Typography>
            <IconButton onClick={() => setCreateOpen(false)} size="small">
              <Iconify icon="mdi:close" />
            </IconButton>
          </Stack>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            {
              "Enter the details for your new dashboard. You'll be able to add widgets after creation."
            }
          </Typography>
        </DialogTitle>
        <DialogContent sx={{ pt: "16px !important" }}>
          <Stack spacing={2}>
            <Box>
              <Typography
                variant="body2"
                fontWeight="fontWeightSemiBold"
                sx={{ mb: 0.5 }}
              >
                Dashboard name
                <Typography component="span" color="error.main">
                  *
                </Typography>
              </Typography>
              <TextField
                placeholder="Latency across tracing"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                fullWidth
                size="small"
              />
            </Box>
            <Box>
              <Typography
                variant="body2"
                fontWeight="fontWeightSemiBold"
                sx={{ mb: 0.5 }}
              >
                Add description
                <Typography component="span" color="error.main">
                  *
                </Typography>
              </Typography>
              <TextField
                placeholder="Tracks latency, error rate, and token usage for the QA agent over time"
                value={newDescription}
                onChange={(e) => setNewDescription(e.target.value)}
                multiline
                rows={2}
                fullWidth
                size="small"
              />
            </Box>
          </Stack>
        </DialogContent>
        <DialogActions sx={{ px: 3, pb: 2 }}>
          <Button
            onClick={() => setCreateOpen(false)}
            sx={{ color: "text.primary" }}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            onClick={handleCreate}
            disabled={!newName.trim() || createMutation.isPending}
          >
            {createMutation.isPending ? "Creating..." : "Create"}
          </Button>
        </DialogActions>
      </Dialog>

      <ConfirmDialog
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        title="Delete Dashboard"
        content={`Are you sure you want to delete "${deleteTarget?.name}"? This action cannot be undone.`}
        action={
          <Button
            variant="contained"
            color="error"
            size="small"
            onClick={confirmDelete}
          >
            Delete
          </Button>
        }
      />
    </Box>
  );
}
