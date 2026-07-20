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
import { fDate, fDateTime, fToNowStrict } from "src/utils/format-time";

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

const DASHBOARD_LIST_COLUMNS =
  "minmax(220px, 1fr) 96px 112px minmax(160px, 220px) 88px 32px";
const DASHBOARD_LIST_CONTENT_COLUMNS =
  "minmax(220px, 1fr) 96px 112px minmax(160px, 220px)";

const VISUALLY_HIDDEN_SX = {
  border: 0,
  clip: "rect(0 0 0 0)",
  height: 1,
  margin: -1,
  overflow: "hidden",
  padding: 0,
  position: "absolute",
  whiteSpace: "nowrap",
  width: 1,
};

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
    return fToNowStrict(date);
  } catch {
    return "";
  }
}

function getDashboardCreatorName(db) {
  return db?.created_by?.name || "";
}

function getDashboardCreatorLabel(db) {
  return getDashboardCreatorName(db) || "Unknown creator";
}

function formatDashboardListDate(date) {
  if (!date) return "—";
  try {
    return fDate(date) || "—";
  } catch {
    return "—";
  }
}

function formatDashboardTooltipDate(date) {
  if (!date) return "";
  try {
    return fDateTime(date) || "";
  } catch {
    return "";
  }
}

function formatDashboardWidgetCount(count) {
  const numericCount = Number(count || 0);
  const safeCount = Number.isFinite(numericCount) ? numericCount : 0;

  return `${safeCount} widget${safeCount === 1 ? "" : "s"}`;
}

function getDashboardViewers(db) {
  const users = [];
  const seen = new Set();
  const addUser = (u, time) => {
    if (!u || !u.email || seen.has(u.email)) return;
    seen.add(u.email);
    users.push({ ...u, displayName: u.name || "Unknown user", time });
  };
  addUser(db.updated_by, db.updated_at);
  addUser(db.created_by, db.created_at);
  return users;
}

function getDashboardPeopleSummary(db) {
  const count = getDashboardViewers(db).length;
  if (!count) return "No people";

  return `${count} ${count === 1 ? "person" : "people"}`;
}

function ViewerAvatars({ db, dashboardName }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const viewers = getDashboardViewers(db);
  const creatorLabel = getDashboardCreatorLabel(db);
  if (!viewers.length) {
    return (
      <Typography variant="caption" color="text.secondary">
        —
      </Typography>
    );
  }

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
                  bgcolor: getAvatarColor(creatorLabel),
                }}
              >
                {getInitials(creatorLabel)}
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
                  Created by {creatorLabel}
                </Typography>
                <Typography
                  variant="caption"
                  sx={{
                    color: isDark ? "rgba(255,255,255,0.45)" : "text.secondary",
                  }}
                >
                  {formatDashboardTooltipDate(db.created_at)}
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
                Last edited by {db.updated_by.name || "Unknown user"}
              </Typography>
              <Typography
                variant="caption"
                sx={{
                  color: isDark ? "rgba(255,255,255,0.5)" : "text.secondary",
                }}
              >
                {formatDashboardTooltipDate(db.updated_at)}
              </Typography>
            </Box>
          )}
        </Box>
      }
    >
      <Box
        component="button"
        type="button"
        aria-label={`People for ${dashboardName}: ${getDashboardPeopleSummary(
          db,
        )}`}
        onClick={(e) => e.stopPropagation()}
        sx={{
          all: "unset",
          display: "inline-flex",
          alignItems: "center",
          gap: 0.5,
          cursor: "default",
          borderRadius: 1,
          minWidth: 0,
          "&:focus-visible": {
            outline: (t) => `2px solid ${t.palette.primary.main}`,
            outlineOffset: 2,
          },
        }}
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
      </Box>
    </Tooltip>
  );
}

ViewerAvatars.propTypes = {
  dashboardName: PropTypes.string.isRequired,
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
        const name = typeof u.name === "string" ? u.name.trim() : u.name;
        map.set(u.email, name || null);
      }
    });

    const entries = Array.from(map, ([email, name]) => ({ email, name }));
    const unnamedCount = entries.filter((creator) => !creator.name).length;
    let unnamedIndex = 0;

    return entries.map((creator) => {
      if (creator.name) return creator;

      unnamedIndex += 1;
      return {
        ...creator,
        name:
          unnamedCount > 1
            ? `Unknown creator ${unnamedIndex}`
            : "Unknown creator",
      };
    });
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

  const openDashboard = (dashboardId) => {
    navigate(paths.dashboard.dashboards.detail(dashboardId));
  };

  const handleDashboardLinkClick = (event, dashboardId) => {
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.altKey ||
      event.ctrlKey ||
      event.shiftKey
    ) {
      return;
    }

    event.preventDefault();
    openDashboard(dashboardId);
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
        minWidth: 0,
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
      <Stack
        direction={{ xs: "column", sm: "row" }}
        justifyContent="space-between"
        alignItems={{ xs: "stretch", sm: "center" }}
        gap={1}
        sx={{ minWidth: 0 }}
      >
        <Stack
          direction={{ xs: "column", sm: "row" }}
          gap={1}
          alignItems={{ xs: "stretch", sm: "center" }}
          sx={{ minWidth: 0, flex: 1 }}
        >
          <FormSearchField
            size="small"
            placeholder="Search"
            searchQuery={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            sx={{
              minWidth: 0,
              width: { xs: "100%", sm: "250px" },
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
              width: { xs: "100%", sm: "auto" },
              justifyContent: { xs: "space-between", sm: "center" },
            }}
          >
            {creatorFilter.length === 0
              ? "Created by anyone"
              : creatorFilter.length === 1
                ? creators.find((c) => c.email === creatorFilter[0])?.name ||
                  "Unknown creator"
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
        <Stack
          direction={{ xs: "column", sm: "row" }}
          gap={1}
          sx={{ width: { xs: "100%", sm: "auto" } }}
        >
          <Button
            variant="outlined"
            size="small"
            sx={{
              borderRadius: "4px",
              height: "30px",
              px: "4px",
              width: { xs: "100%", sm: "105px" },
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
            sx={{ height: "38px", width: { xs: "100%", sm: "auto" } }}
          >
            {createMutation.isPending ? "Creating..." : "Create Dashboard"}
          </Button>
        </Stack>
      </Stack>

      {/* Dashboard list */}
      <Box sx={{ flex: 1, minWidth: 0 }}>
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
          <Stack spacing={1} sx={{ minWidth: 0 }}>
            <Box
              sx={{
                display: { xs: "none", md: "grid" },
                gridTemplateColumns: DASHBOARD_LIST_COLUMNS,
                columnGap: 1.5,
                alignItems: "center",
                px: 2,
                color: "text.disabled",
              }}
            >
              <Typography
                variant="caption"
                fontWeight={600}
                sx={{ minWidth: 0 }}
              >
                Name
              </Typography>
              <Typography variant="caption" fontWeight={600}>
                Widgets
              </Typography>
              <Typography variant="caption" fontWeight={600}>
                Last updated
              </Typography>
              <Typography variant="caption" fontWeight={600}>
                Created by
              </Typography>
              <Typography variant="caption" fontWeight={600}>
                People
              </Typography>
              <Box />
            </Box>
            {filteredDashboards.map((db) => {
              const creatorName = getDashboardCreatorName(db);
              const creatorLabel = getDashboardCreatorLabel(db);
              const dashboardDate = db.updated_at || db.created_at;
              const dashboardDateText = formatDashboardListDate(dashboardDate);
              const widgetCountText = formatDashboardWidgetCount(
                db.widget_count,
              );
              const peopleSummary = getDashboardPeopleSummary(db);
              const rowNameId = `dashboard-row-${db.id}-name`;
              const rowDescriptionId = `dashboard-row-${db.id}-description`;

              return (
                <Box
                  key={db.id}
                  sx={{
                    display: "grid",
                    gridTemplateColumns: {
                      xs: "minmax(0, 1fr) 32px",
                      md: DASHBOARD_LIST_COLUMNS,
                    },
                    columnGap: 1.5,
                    rowGap: { xs: 1, md: 0 },
                    alignItems: "center",
                    px: 2,
                    py: 1.25,
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
                    "&:focus-within .row-actions": { opacity: 1 },
                    "@media (hover: none)": {
                      "& .row-actions": { opacity: 1 },
                    },
                  }}
                >
                  <Box
                    component="a"
                    href={paths.dashboard.dashboards.detail(db.id)}
                    aria-labelledby={rowNameId}
                    aria-describedby={rowDescriptionId}
                    onClick={(event) => handleDashboardLinkClick(event, db.id)}
                    sx={{
                      display: "grid",
                      gridTemplateColumns: {
                        xs: "minmax(0, 1fr)",
                        md: DASHBOARD_LIST_CONTENT_COLUMNS,
                      },
                      columnGap: 1.5,
                      rowGap: { xs: 0.75, md: 0 },
                      alignItems: "center",
                      color: "inherit",
                      cursor: "pointer",
                      minWidth: 0,
                      gridColumn: { xs: "1 / 2", md: "1 / 5" },
                      width: "100%",
                      textAlign: "left",
                      textDecoration: "none",
                      borderRadius: 1,
                      "&:focus-visible": {
                        outline: (t) => `2px solid ${t.palette.primary.main}`,
                        outlineOffset: 2,
                      },
                    }}
                  >
                    <Stack
                      direction="row"
                      alignItems={{ xs: "flex-start", md: "center" }}
                      gap={1.5}
                      sx={{ minWidth: 0 }}
                    >
                      <Iconify
                        icon="mdi:view-dashboard-outline"
                        width={18}
                        sx={{ color: "primary.main", flexShrink: 0 }}
                      />

                      <Typography
                        id={rowNameId}
                        variant="body2"
                        fontWeight={600}
                        sx={{
                          minWidth: 0,
                          overflow: { xs: "visible", md: "hidden" },
                          overflowWrap: "anywhere",
                          textOverflow: { xs: "clip", md: "ellipsis" },
                          whiteSpace: { xs: "normal", md: "nowrap" },
                        }}
                      >
                        {db.name}
                      </Typography>
                    </Stack>

                    <Typography
                      variant="caption"
                      color="text.disabled"
                      sx={{ whiteSpace: "nowrap" }}
                    >
                      <Box
                        component="span"
                        sx={{
                          display: { xs: "inline", md: "none" },
                          fontWeight: 600,
                        }}
                      >
                        Widgets:{" "}
                      </Box>
                      {widgetCountText}
                    </Typography>

                    <Tooltip
                      title={formatDashboardTooltipDate(dashboardDate)}
                      arrow
                    >
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        sx={{ whiteSpace: "nowrap" }}
                      >
                        <Box
                          component="span"
                          sx={{
                            display: { xs: "inline", md: "none" },
                            color: "text.disabled",
                            fontWeight: 600,
                          }}
                        >
                          Updated:{" "}
                        </Box>
                        {dashboardDateText}
                      </Typography>
                    </Tooltip>

                    <Stack
                      direction="row"
                      alignItems="center"
                      gap={1}
                      flexWrap={{ xs: "wrap", md: "nowrap" }}
                      sx={{ minWidth: 0 }}
                    >
                      <Typography
                        variant="caption"
                        color="text.disabled"
                        sx={{
                          display: { xs: "inline", md: "none" },
                          flexShrink: 0,
                          fontWeight: 600,
                        }}
                      >
                        Created by:
                      </Typography>
                      {creatorName && (
                        <Avatar
                          sx={{
                            width: 26,
                            height: 26,
                            fontSize: "11px",
                            fontWeight: 700,
                            bgcolor: getAvatarColor(creatorName),
                            flexShrink: 0,
                          }}
                        >
                          {getInitials(creatorName)}
                        </Avatar>
                      )}
                      <Typography
                        variant="caption"
                        color="text.secondary"
                        title={creatorName || undefined}
                        sx={{
                          minWidth: 0,
                          overflow: { xs: "visible", md: "hidden" },
                          overflowWrap: "anywhere",
                          textOverflow: { xs: "clip", md: "ellipsis" },
                          whiteSpace: { xs: "normal", md: "nowrap" },
                        }}
                      >
                        {creatorLabel}
                      </Typography>
                    </Stack>

                    <Box
                      id={rowDescriptionId}
                      component="span"
                      sx={VISUALLY_HIDDEN_SX}
                    >
                      {`${widgetCountText}. Last updated ${dashboardDateText}. Created by ${creatorLabel}. ${peopleSummary}.`}
                    </Box>
                  </Box>

                  <Stack
                    direction="row"
                    alignItems="center"
                    gap={1}
                    flexWrap={{ xs: "wrap", md: "nowrap" }}
                    sx={{
                      minWidth: 0,
                      gridColumn: { xs: "1 / 2", md: "5 / 6" },
                    }}
                  >
                    <Typography
                      variant="caption"
                      color="text.disabled"
                      sx={{
                        display: { xs: "inline", md: "none" },
                        flexShrink: 0,
                        fontWeight: 600,
                      }}
                    >
                      People:
                    </Typography>
                    <ViewerAvatars db={db} dashboardName={db.name} />
                  </Stack>

                  <IconButton
                    className="row-actions"
                    size="small"
                    onClick={(e) => handleDelete(e, db)}
                    aria-label={`Delete ${db.name}`}
                    sx={{
                      opacity: { xs: 1, md: 0 },
                      transition: "opacity 0.15s",
                      flexShrink: 0,
                      width: 32,
                      height: 32,
                      justifySelf: "end",
                      gridColumn: { xs: "2 / 3", md: "auto" },
                      gridRow: { xs: "1 / 2", md: "auto" },
                      "@media (hover: none)": { opacity: 1 },
                    }}
                  >
                    <Iconify icon="mdi:delete-outline" width={18} />
                  </IconButton>
                </Box>
              );
            })}
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
