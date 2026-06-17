/* eslint-disable react/prop-types */
import React, { useState, useMemo, useCallback } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Box,
  Typography,
  Stack,
  Tab,
  Tabs,
  Card,
  CardContent,
  Grid,
  LinearProgress,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Chip,
  CircularProgress,
  Skeleton,
  IconButton,
} from "@mui/material";
import Iconify from "src/components/iconify";
import SectionHeader from "../components/SectionHeader";
import { GATEWAY_ICONS } from "../constants/gatewayIcons";
import { enqueueSnackbar } from "notistack";
import { useQuery } from "@tanstack/react-query";
import axiosInstance, { endpoints } from "src/utils/axios";
import {
  useGatewayConfig,
  useRemoveBudget,
} from "../providers/hooks/useGatewayConfig";
import { useAnalyticsOverview } from "../analytics/hooks/useAnalyticsOverview";
import { useGatewayContext } from "../context/useGatewayContext";

import SetBudgetDialog from "./SetBudgetDialog";
import { val } from "../utils/analyticsHelpers";
import { formatCost } from "../utils/formatters";

const TAB_SLUGS = ["dashboard", "config", "access", "audit"];

function tabSlugToIndex(slug) {
  const idx = TAB_SLUGS.indexOf(slug);
  return idx >= 0 ? idx : 0;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// Config-level keys inside the budgets JSON blob — these are flags/settings, NOT budget levels
const BUDGET_CONFIG_KEYS = new Set([
  "enabled",
  "default_period",
  "defaultPeriod",
  "org_period",
  "orgPeriod",
  "warn_threshold",
  "warnThreshold",
]);

// Friendly labels for budget level keys
const BUDGET_LEVEL_LABELS = {
  org_limit: "Organization",
  orgLimit: "Organization",
  hard_limit: "Hard Limit",
  hardLimit: "Hard Limit",
  per_key: "Per API Key",
  perKey: "Per API Key",
  per_user: "Per User",
  perUser: "Per User",
  per_model: "Per Model",
  perModel: "Per Model",
};

function getBudgetLevelLabel(level) {
  return BUDGET_LEVEL_LABELS[level] || level;
}

function getBudgetValue(budget, snakeKey, camelKey = snakeKey) {
  return budget?.[snakeKey] ?? budget?.[camelKey];
}

function getBudgetAction(budget) {
  return (
    budget?.action ||
    budget?.action_mode ||
    budget?.on_exceed ||
    budget?.onExceed ||
    "warn"
  );
}

function extractBudgets(config) {
  const budgets = config?.budgets || config?.budget || {};
  if (typeof budgets !== "object") return [];
  if (Array.isArray(budgets)) return budgets;

  // Filter out config flags, only keep actual budget levels.
  // Budget values are either numbers (the limit) or objects (full config).
  // Strings/booleans are config flags or derived metadata, not budgets.
  return Object.entries(budgets)
    .filter(
      ([key, val]) =>
        !BUDGET_CONFIG_KEYS.has(key) &&
        (typeof val === "number" || (typeof val === "object" && val !== null)),
    )
    .map(([level, cfg]) => ({
      level,
      ...(typeof cfg === "object" ? cfg : { limit: cfg }),
    }));
}

function extractRBAC(config) {
  const rbac = config?.rbac || config?.roles || {};
  if (Array.isArray(rbac)) return rbac;
  if (typeof rbac === "object") {
    return Object.entries(rbac).map(([role, cfg]) => ({
      role,
      ...(typeof cfg === "object" ? cfg : { description: String(cfg) }),
    }));
  }
  return [];
}

// ---------------------------------------------------------------------------
// Budget Dashboard Tab
// ---------------------------------------------------------------------------

const BudgetDashboardTab = ({ budgets: rawBudgets, overview, gatewayId }) => {
  const navigate = useNavigate();
  const totalSpend = Number(val(overview?.total_cost) ?? 0);
  const totalRequests = Number(val(overview?.total_requests) ?? 0);
  const removeBudget = useRemoveBudget();
  const [removedLevels, setRemovedLevels] = useState(new Set());

  const budgets = useMemo(
    () => rawBudgets.filter((b) => !removedLevels.has(b.level || b.name)),
    [rawBudgets, removedLevels],
  );

  const handleRemoveBudget = (level) => {
    // Optimistic: hide immediately
    setRemovedLevels((prev) => new Set([...prev, level]));
    removeBudget.mutate(
      { gatewayId, level },
      {
        onSuccess: () => {
          enqueueSnackbar(`Budget "${level}" removed`, { variant: "success" });
        },
        onError: () => {
          // Rollback: show it again
          setRemovedLevels((prev) => {
            const next = new Set(prev);
            next.delete(level);
            return next;
          });
          enqueueSnackbar("Failed to remove budget", { variant: "error" });
        },
      },
    );
  };

  return (
    <Stack spacing={3}>
      {/* Summary cards */}
      <Grid container spacing={2}>
        <Grid item xs={6} sm={3}>
          <Card>
            <CardContent sx={{ textAlign: "center" }}>
              <Typography variant="caption" color="text.secondary">
                Total Spend
              </Typography>
              <Typography variant="h4" color="primary.main">
                {formatCost(totalSpend)}
              </Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={6} sm={3}>
          <Card>
            <CardContent sx={{ textAlign: "center" }}>
              <Typography variant="caption" color="text.secondary">
                Budget Levels
              </Typography>
              <Typography variant="h4">{budgets.length}</Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={6} sm={3}>
          <Card>
            <CardContent sx={{ textAlign: "center" }}>
              <Typography variant="caption" color="text.secondary">
                Requests
              </Typography>
              <Typography variant="h4">
                {totalRequests.toLocaleString()}
              </Typography>
            </CardContent>
          </Card>
        </Grid>
        <Grid item xs={6} sm={3}>
          <Card>
            <CardContent sx={{ textAlign: "center" }}>
              <Typography variant="caption" color="text.secondary">
                Avg Cost/Request
              </Typography>
              <Typography variant="h4">
                {totalRequests > 0
                  ? formatCost(totalSpend / totalRequests)
                  : "$0.00"}
              </Typography>
            </CardContent>
          </Card>
        </Grid>
      </Grid>

      {/* Budget utilization */}
      <Card>
        <CardContent>
          <Typography variant="h6" mb={2}>
            Budget Utilization
          </Typography>
          {budgets.length > 0 ? (
            <Stack spacing={2}>
              {budgets.map((b, idx) => {
                const limit = Number(
                  getBudgetValue(b, "limit") ??
                    getBudgetValue(b, "max") ??
                    getBudgetValue(b, "hard_cap", "hardCap") ??
                    0,
                );
                const spent = Number(getBudgetValue(b, "spent") ?? 0);
                const pct =
                  limit > 0 ? Math.min((spent / limit) * 100, 100) : 0;
                return (
                  <Box key={b.level || b.name || idx}>
                    <Stack
                      direction="row"
                      justifyContent="space-between"
                      alignItems="center"
                      mb={0.5}
                    >
                      <Typography
                        variant="body2"
                        fontWeight={500}
                        sx={{
                          cursor: "pointer",
                          "&:hover": {
                            textDecoration: "underline",
                            color: "primary.main",
                          },
                        }}
                        onClick={() =>
                          navigate(
                            `/dashboard/gateway/analytics?tab=cost&budget=${encodeURIComponent(b.level || b.name || "")}`,
                          )
                        }
                      >
                        {getBudgetLevelLabel(b.level || b.name) ||
                          `Budget ${idx + 1}`}
                      </Typography>
                      <Stack direction="row" spacing={1} alignItems="center">
                        <Typography variant="body2" color="text.secondary">
                          {formatCost(spent)} /{" "}
                          {limit > 0 ? formatCost(limit) : "No limit"}
                        </Typography>
                        <IconButton
                          size="small"
                          color="error"
                          onClick={() => handleRemoveBudget(b.level || b.name)}
                          title="Remove budget"
                        >
                          <Iconify icon="mdi:delete-outline" width={16} />
                        </IconButton>
                      </Stack>
                    </Stack>
                    <LinearProgress
                      variant="determinate"
                      value={pct}
                      color={
                        pct >= 90 ? "error" : pct >= 70 ? "warning" : "primary"
                      }
                      sx={{ height: 8, borderRadius: 1 }}
                    />
                  </Box>
                );
              })}
            </Stack>
          ) : (
            <Typography variant="body2" color="text.secondary">
              No budget limits configured. Click &quot;Set Budget&quot; above to
              add spending limits.
            </Typography>
          )}
        </CardContent>
      </Card>
    </Stack>
  );
};

// ---------------------------------------------------------------------------
// Budget Config Tab
// ---------------------------------------------------------------------------

const BudgetConfigTab = ({ config }) => {
  const entries = extractBudgets(config);

  return (
    <Stack spacing={2}>
      {entries.length > 0 ? (
        entries.map((budget, idx) => {
          const level = budget.level || budget.name || `Budget ${idx + 1}`;
          const hardCap =
            getBudgetValue(budget, "hard_cap", "hardCap") ??
            getBudgetValue(budget, "max");
          const alertThreshold = getBudgetValue(
            budget,
            "alert_threshold",
            "alertThreshold",
          );
          const resetDay = getBudgetValue(budget, "reset_day", "resetDay");
          return (
            <Card key={level}>
              <CardContent>
                <Stack
                  direction="row"
                  justifyContent="space-between"
                  alignItems="center"
                  mb={1.5}
                >
                  <Typography variant="h6">
                    {getBudgetLevelLabel(level)}
                  </Typography>
                  <Chip
                    label={getBudgetAction(budget)}
                    color={
                      getBudgetAction(budget) === "block"
                        ? "error"
                        : getBudgetAction(budget) === "throttle"
                          ? "warning"
                          : "info"
                    }
                    size="small"
                    variant="outlined"
                  />
                </Stack>
                <Stack spacing={0.75}>
                  {budget.limit != null && (
                    <Stack direction="row" spacing={2}>
                      <Typography
                        variant="body2"
                        color="text.secondary"
                        sx={{ minWidth: 140 }}
                      >
                        Limit
                      </Typography>
                      <Typography
                        variant="body2"
                        sx={{ fontFamily: "monospace" }}
                      >
                        {formatCost(budget.limit)}
                      </Typography>
                    </Stack>
                  )}
                  {hardCap != null && (
                    <Stack direction="row" spacing={2}>
                      <Typography
                        variant="body2"
                        color="text.secondary"
                        sx={{ minWidth: 140 }}
                      >
                        Hard Cap
                      </Typography>
                      <Typography
                        variant="body2"
                        sx={{ fontFamily: "monospace" }}
                      >
                        {formatCost(hardCap)}
                      </Typography>
                    </Stack>
                  )}
                  {alertThreshold != null && (
                    <Stack direction="row" spacing={2}>
                      <Typography
                        variant="body2"
                        color="text.secondary"
                        sx={{ minWidth: 140 }}
                      >
                        Alert Threshold
                      </Typography>
                      <Typography variant="body2">{alertThreshold}%</Typography>
                    </Stack>
                  )}
                  {budget.period && (
                    <Stack direction="row" spacing={2}>
                      <Typography
                        variant="body2"
                        color="text.secondary"
                        sx={{ minWidth: 140 }}
                      >
                        Period
                      </Typography>
                      <Typography variant="body2">{budget.period}</Typography>
                    </Stack>
                  )}
                  {resetDay != null && (
                    <Stack direction="row" spacing={2}>
                      <Typography
                        variant="body2"
                        color="text.secondary"
                        sx={{ minWidth: 140 }}
                      >
                        Reset Day
                      </Typography>
                      <Typography variant="body2">{resetDay}</Typography>
                    </Stack>
                  )}
                  {budget.description && (
                    <Stack direction="row" spacing={2}>
                      <Typography
                        variant="body2"
                        color="text.secondary"
                        sx={{ minWidth: 140 }}
                      >
                        Description
                      </Typography>
                      <Typography variant="body2">
                        {budget.description}
                      </Typography>
                    </Stack>
                  )}
                </Stack>
              </CardContent>
            </Card>
          );
        })
      ) : (
        <Card sx={{ p: 4 }}>
          <Typography variant="body2" color="text.secondary" textAlign="center">
            No budget configuration found. Set a budget from the Dashboard tab.
          </Typography>
        </Card>
      )}
    </Stack>
  );
};

// ---------------------------------------------------------------------------
// RBAC Tab
// ---------------------------------------------------------------------------

const RBACTab = ({ roles, config }) => {
  const authConfig = config?.auth || {};

  return (
    <Stack spacing={2}>
      <Card>
        <CardContent>
          <Typography variant="h6" mb={2}>
            Authentication
          </Typography>
          <Stack spacing={1}>
            <Stack direction="row" spacing={2}>
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ minWidth: 120 }}
              >
                Auth Enabled
              </Typography>
              <Chip
                label={authConfig.enabled ? "Yes" : "No"}
                color={authConfig.enabled ? "success" : "default"}
                size="small"
              />
            </Stack>
          </Stack>
        </CardContent>
      </Card>

      <Card>
        <CardContent>
          <Typography variant="h6" mb={2}>
            Roles & Permissions
          </Typography>
          {roles.length > 0 ? (
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Role</TableCell>
                    <TableCell>Permissions</TableCell>
                    <TableCell>Description</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {roles.map((r, idx) => (
                    <TableRow key={r.role || idx}>
                      <TableCell>
                        <Typography variant="body2" fontWeight={500}>
                          {r.role}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        <Stack direction="row" flexWrap="wrap" gap={0.5}>
                          {(r.permissions || []).map((p) => (
                            <Chip
                              key={p}
                              label={p}
                              size="small"
                              variant="outlined"
                            />
                          ))}
                        </Stack>
                      </TableCell>
                      <TableCell>
                        <Typography variant="body2" color="text.secondary">
                          {r.description || "\u2014"}
                        </Typography>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          ) : (
            <Typography variant="body2" color="text.secondary">
              No RBAC roles configured. The gateway uses API key-based
              authentication.
            </Typography>
          )}
        </CardContent>
      </Card>
    </Stack>
  );
};

// ---------------------------------------------------------------------------
// Audit Log Tab
// ---------------------------------------------------------------------------

const AuditLogTab = ({ gatewayId }) => {
  const { data: logs, isLoading } = useQuery({
    queryKey: ["agentcc-audit-logs", gatewayId],
    queryFn: async () => {
      const { data } = await axiosInstance.get(
        `${endpoints.gateway.requestLogs}?gateway_id=${gatewayId}&limit=50`,
      );
      return data?.result?.results ?? data?.results ?? data?.result ?? [];
    },
    enabled: Boolean(gatewayId),
    staleTime: 30000,
  });

  const logList = Array.isArray(logs) ? logs : logs?.results || [];

  if (isLoading) {
    return (
      <Box display="flex" justifyContent="center" py={4}>
        <CircularProgress />
      </Box>
    );
  }

  return (
    <Card>
      <CardContent>
        <Typography variant="h6" mb={2}>
          Recent Activity
        </Typography>
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Time</TableCell>
                <TableCell>Action</TableCell>
                <TableCell>Model</TableCell>
                <TableCell>API Key</TableCell>
                <TableCell>Cost</TableCell>
                <TableCell>Status</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {logList.slice(0, 25).map((log) => {
                const isError = log.is_error ?? Number(log.status_code) >= 400;

                return (
                  <TableRow key={log.id}>
                    <TableCell>
                      <Typography variant="body2" fontSize="0.75rem">
                        {log.started_at
                          ? new Date(log.started_at).toLocaleString()
                          : "\u2014"}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2">
                        {isError ? "Error" : "Request"}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2">
                        {log.model || "\u2014"}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Typography
                        variant="body2"
                        sx={{ fontFamily: "monospace", fontSize: "0.7rem" }}
                      >
                        {log.api_key_id?.substring(0, 12) || "\u2014"}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Typography variant="body2">
                        {formatCost(log.cost)}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Chip
                        label={log.status_code || "\u2014"}
                        color={isError ? "error" : "success"}
                        size="small"
                      />
                    </TableCell>
                  </TableRow>
                );
              })}
              {logList.length === 0 && (
                <TableRow>
                  <TableCell colSpan={6} align="center">
                    <Typography variant="body2" color="text.secondary" py={4}>
                      No activity logs found
                    </Typography>
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </TableContainer>
      </CardContent>
    </Card>
  );
};

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

const BudgetRBACSection = () => {
  const { tab: tabSlug } = useParams();
  const navigate = useNavigate();
  const tab = tabSlugToIndex(tabSlug);

  const handleTabChange = useCallback(
    (_, newIndex) => {
      if (newIndex === 0) {
        navigate("/dashboard/gateway/budgets", { replace: true });
      } else {
        navigate(`/dashboard/gateway/budgets/${TAB_SLUGS[newIndex]}`, {
          replace: true,
        });
      }
    },
    [navigate],
  );

  const [budgetDialogOpen, setBudgetDialogOpen] = useState(false);
  const { gatewayId, isLoading: gwLoading } = useGatewayContext();

  const { data: config, isLoading: configLoading } =
    useGatewayConfig(gatewayId);

  const now = useMemo(() => new Date().toISOString(), []);
  const monthAgo = useMemo(() => {
    const d = new Date();
    d.setDate(d.getDate() - 30);
    return d.toISOString();
  }, []);

  const { data: overview } = useAnalyticsOverview({
    start: monthAgo,
    end: now,
    gatewayId,
  });

  const budgets = useMemo(() => extractBudgets(config), [config]);
  const roles = useMemo(() => extractRBAC(config), [config]);

  if (gwLoading || configLoading) {
    return (
      <Box p={3}>
        <Stack direction="row" justifyContent="space-between" mb={3}>
          <Skeleton width={200} height={40} />
          <Skeleton width={110} height={36} variant="rounded" />
        </Stack>
        <Skeleton width="50%" height={40} sx={{ mb: 3 }} />
        <Grid container spacing={2}>
          {[...Array(4)].map((_, i) => (
            <Grid item xs={6} sm={3} key={i}>
              <Skeleton variant="rounded" height={90} />
            </Grid>
          ))}
        </Grid>
      </Box>
    );
  }

  return (
    <Box p={3}>
      <SectionHeader
        icon={GATEWAY_ICONS.budgets}
        title="Budgets"
        subtitle="Set spending limits and track cost utilization"
        actions={[
          {
            label: "Set Budget",
            variant: "contained",
            size: "small",
            icon: "mdi:plus",
            onClick: () => setBudgetDialogOpen(true),
          },
        ]}
      />

      <Tabs
        value={tab}
        onChange={handleTabChange}
        sx={{ mb: 3, borderBottom: 1, borderColor: "divider" }}
      >
        <Tab label="Budget Dashboard" />
        <Tab label="Budget Config" />
        <Tab label="Roles & Access (Read-Only)" />
        <Tab label="Recent Activity" />
      </Tabs>

      {tab === 0 && (
        <BudgetDashboardTab
          budgets={budgets}
          overview={overview}
          gatewayId={gatewayId}
        />
      )}
      {tab === 1 && <BudgetConfigTab config={config} />}
      {tab === 2 && <RBACTab roles={roles} config={config} />}
      {tab === 3 && <AuditLogTab gatewayId={gatewayId} />}

      <SetBudgetDialog
        open={budgetDialogOpen}
        onClose={() => setBudgetDialogOpen(false)}
        gatewayId={gatewayId}
      />
    </Box>
  );
};

export default BudgetRBACSection;
