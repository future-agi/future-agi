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
  Skeleton,
  IconButton,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { LoadingScreen } from "src/components/loading-screen";
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
import GatewayAdminOnly from "../components/GatewayAdminOnly";
import { useGatewayAdmin } from "../hooks/useGatewayAdmin";
import BudgetEnforcementCard from "./BudgetEnforcementCard";
import { budgetRows, orgBudgetPeriod, orgSpendPeriods } from "./budgetLevels";
import { useBudgetSpend } from "./hooks/useBudgetSpend";
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

function actionLabel(row) {
  if (row.action === "throttle") {
    return `throttle ${row.throttleRpm ?? 1}/min`;
  }
  return row.action;
}

function actionColor(action) {
  if (action === "block") return "error";
  if (action === "throttle") return "warning";
  return "info";
}

function notInForceText(row) {
  if (!row.overriddenBy) return null;
  return row.laterSave
    ? `Not in force: the later ${row.overriddenBy} save is enforced instead.`
    : `Not in force: the ${row.overriddenBy} budget is enforced instead.`;
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

// One budget level: an org-level bar shows the organization's real spend
// for the level's period (request logs, GET /agentcc/spend-summary/); a
// team, user, key or tag level is counted by the gateway only.
const BudgetRow = ({ row, spend, onRemove, canEdit }) => {
  const navigate = useNavigate();
  const isOrg = row.group === "org";
  const spent = isOrg ? spend.data?.[row.period]?.total_spend : undefined;
  const spentKnown = typeof spent === "number";
  const limitText = row.limit > 0 ? formatCost(row.limit) : "No limit";
  let spendText = `Limit ${limitText}`;
  if (isOrg) {
    const spentText = spentKnown
      ? formatCost(spent)
      : spend.isError
        ? "Spend unavailable"
        : "\u2026";
    spendText = `${spentText} / ${limitText}`;
  }
  const pct =
    spentKnown && row.limit > 0 ? Math.min((spent / row.limit) * 100, 100) : 0;
  const notInForce = notInForceText(row);

  return (
    <Box data-testid={`budget-row-${row.id}`}>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="center"
        mb={0.5}
      >
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography
            variant="body2"
            fontWeight={500}
            sx={
              isOrg
                ? {
                    cursor: "pointer",
                    "&:hover": {
                      textDecoration: "underline",
                      color: "primary.main",
                    },
                  }
                : undefined
            }
            onClick={
              isOrg
                ? () =>
                    navigate(
                      `/dashboard/gateway/analytics?tab=cost&budget=${encodeURIComponent(row.level)}`,
                    )
                : undefined
            }
          >
            {row.label}
          </Typography>
          <Chip
            label={actionLabel(row)}
            color={actionColor(row.action)}
            size="small"
            variant="outlined"
          />
          <Typography variant="caption" color="text.secondary">
            {row.period}
          </Typography>
        </Stack>
        <Stack direction="row" spacing={1} alignItems="center">
          <Typography variant="body2" color="text.secondary">
            {spendText}
          </Typography>
          {row.removable && (
            <GatewayAdminOnly allowed={canEdit}>
              <IconButton
                size="small"
                color="error"
                onClick={() => onRemove(row.level)}
                title="Remove budget"
                aria-label={`Remove budget ${row.level}`}
                disabled={!canEdit}
              >
                <Iconify icon="mdi:delete-outline" width={16} />
              </IconButton>
            </GatewayAdminOnly>
          )}
        </Stack>
      </Stack>
      {isOrg ? (
        <LinearProgress
          variant="determinate"
          value={pct}
          color={pct >= 90 ? "error" : pct >= 70 ? "warning" : "primary"}
          sx={{ height: 8, borderRadius: 1 }}
        />
      ) : (
        <Typography variant="caption" color="text.secondary">
          Spend for this level is counted by the gateway; this page shows
          organization spend only. Edit it in Org Config &gt; Budgets.
        </Typography>
      )}
      {notInForce && (
        <Typography
          variant="caption"
          color="warning.main"
          display="block"
          mt={0.5}
        >
          {notInForce}
        </Typography>
      )}
    </Box>
  );
};

const BudgetDashboardTab = ({ config, overview, gatewayId }) => {
  const totalSpend = Number(val(overview?.total_cost) ?? 0);
  const totalRequests = Number(val(overview?.total_requests) ?? 0);
  const removeBudget = useRemoveBudget();
  const { canEdit } = useGatewayAdmin();
  const [removedLevels, setRemovedLevels] = useState(new Set());
  const storedBudgets = config?.budgets;

  const rows = useMemo(
    () => budgetRows(storedBudgets).filter((r) => !removedLevels.has(r.level)),
    [storedBudgets, removedLevels],
  );
  const periods = useMemo(() => orgSpendPeriods(rows), [rows]);
  const spend = useBudgetSpend(periods);

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
      <BudgetEnforcementCard
        gatewayId={gatewayId}
        budgets={storedBudgets}
        sectionsEnforced={config?.org_sections_enforced}
      />

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
              <Typography variant="h4">{rows.length}</Typography>
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
          <Typography variant="h6" mb={0.5}>
            Budget Utilization
          </Typography>
          <Typography
            variant="caption"
            color="text.secondary"
            display="block"
            mb={2}
          >
            Organization spend for each budget&apos;s period, from the request
            logs. The gateway checks each limit against its own count of this
            spend, which can be lower than the bar after a gateway restart or a
            change of the budget&apos;s period.
          </Typography>
          {rows.length > 0 ? (
            <Stack spacing={2}>
              {rows.map((row) => (
                <BudgetRow
                  key={row.id}
                  row={row}
                  spend={spend}
                  onRemove={handleRemoveBudget}
                  canEdit={canEdit}
                />
              ))}
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

const ConfigLine = ({ label, children }) => (
  <Stack direction="row" spacing={2}>
    <Typography variant="body2" color="text.secondary" sx={{ minWidth: 140 }}>
      {label}
    </Typography>
    <Typography variant="body2">{children}</Typography>
  </Stack>
);

const BudgetConfigTab = ({ config }) => {
  const rows = budgetRows(config?.budgets);

  return (
    <Stack spacing={2}>
      {rows.length > 0 ? (
        rows.map((row) => (
          <Card key={row.id}>
            <CardContent>
              <Stack
                direction="row"
                justifyContent="space-between"
                alignItems="center"
                mb={1.5}
              >
                <Typography variant="h6">{row.label}</Typography>
                <Chip
                  label={actionLabel(row)}
                  color={actionColor(row.action)}
                  size="small"
                  variant="outlined"
                />
              </Stack>
              <Stack spacing={0.75}>
                <ConfigLine label="Limit">
                  {row.limit > 0 ? formatCost(row.limit) : "No limit"}
                </ConfigLine>
                <ConfigLine label="Period">{row.period}</ConfigLine>
                {row.alertThreshold != null && (
                  <ConfigLine label="Alert Threshold">
                    {`${row.alertThreshold}%`}
                  </ConfigLine>
                )}
                {row.action === "throttle" && (
                  <ConfigLine label="Throttle">
                    {`${row.throttleRpm ?? 1} requests a minute per gateway replica past the limit`}
                  </ConfigLine>
                )}
                {row.entry?.description && (
                  <ConfigLine label="Description">
                    {row.entry.description}
                  </ConfigLine>
                )}
                {notInForceText(row) && (
                  <Typography variant="caption" color="warning.main">
                    {notInForceText(row)}
                  </Typography>
                )}
              </Stack>
            </CardContent>
          </Card>
        ))
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
  // Shown only when the gateway reports its auth switch; the console's
  // config read carries none on a shipped gateway.
  const authEnabled = config?.auth?.enabled;
  const authReported = typeof authEnabled === "boolean";

  return (
    <Stack spacing={2}>
      <Card>
        <CardContent>
          <Typography variant="h6" mb={2}>
            Authentication
          </Typography>
          <Stack spacing={1}>
            {authReported && (
              <Stack direction="row" spacing={2}>
                <Typography
                  variant="body2"
                  color="text.secondary"
                  sx={{ minWidth: 120 }}
                >
                  Auth Enabled
                </Typography>
                <Chip
                  label={authEnabled ? "Yes" : "No"}
                  color={authEnabled ? "success" : "default"}
                  size="small"
                />
              </Stack>
            )}
            <Typography variant="body2" color="text.secondary">
              The gateway identifies each request by the API key it sends: the
              key&apos;s organization selects the org config (providers,
              budgets, guardrails) the request runs under, and the key&apos;s
              owner is the user a per-user budget falls back to when the request
              names no user.
            </Typography>
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
    return <LoadingScreen variant="orbit" sx={{ minHeight: "50vh" }} />;
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
  const { canEdit, disabledReason } = useGatewayAdmin();
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
            disabled: !canEdit,
            tooltip: disabledReason || undefined,
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
          config={config}
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
        period={orgBudgetPeriod(config?.budgets)}
      />
    </Box>
  );
};

export default BudgetRBACSection;
