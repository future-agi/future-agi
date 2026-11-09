import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import IconButton from "@mui/material/IconButton";
import LinearProgress from "@mui/material/LinearProgress";
import Link from "@mui/material/Link";
import Skeleton from "@mui/material/Skeleton";
import Stack from "@mui/material/Stack";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";
import { enqueueSnackbar } from "notistack";

import SvgColor from "src/components/svg-color";
import { RouterLink } from "src/routes/components";
import { paths } from "src/routes/paths";

import { useAnalyticsOverview } from "../analytics/hooks/useAnalyticsOverview";
import {
  GatewayEmptyState,
  GatewayGrid,
  GatewayPageFrame,
  GatewayStatusChip,
} from "../components/frame";
import ConfirmDialog from "../components/ConfirmDialog";
import GatewayAdminOnly from "../components/GatewayAdminOnly";
import { useGatewayContext } from "../context/useGatewayContext";
import { useGatewayAdmin } from "../hooks/useGatewayAdmin";
import {
  useGatewayConfig,
  useRemoveBudget,
} from "../providers/hooks/useGatewayConfig";
import { Figure, SettingsPanel } from "../settings/ui";
import CostTotal from "../components/CostTotal";
import { formatCost, formatCostUpperBound } from "../utils/formatters";
import { overviewCost, val } from "../utils/analyticsHelpers";
import BudgetEnforcementCard from "./BudgetEnforcementCard";
import { budgetRows, orgBudgetPeriod, orgSpendPeriods } from "./budgetLevels";
import { useBudgetSpend } from "./hooks/useBudgetSpend";
import SetBudgetDialog from "./SetBudgetDialog";

const PERIOD_LABEL = {
  daily: "Daily",
  weekly: "Weekly",
  monthly: "Monthly",
  total: "All time",
};

// What the gateway does at a level's limit (agentcc-gateway plugins/budget):
// block with a 429, log a warning, or past the limit allow only the set
// number of requests a minute, counted on each gateway replica.
function actionChip(row) {
  if (row.action === "block") return { tone: "error", label: "Blocks" };
  if (row.action === "throttle") return { tone: "warning", label: "Throttles" };
  return { tone: "neutral", label: "Warns" };
}

function throttleText(row) {
  return `${row.throttleRpm ?? 1} requests a minute per gateway replica past the limit`;
}

// Of the Organization and Hard Limit budgets, the gateway enforces the one
// saved last (budgetLevels.orgEntryInForce); the other says so.
function notInForceText(row) {
  if (!row.overriddenBy) return null;
  return row.laterSave
    ? `Not in force: the later ${row.overriddenBy} save is enforced instead.`
    : `Not in force: the ${row.overriddenBy} budget is enforced instead.`;
}

// AG Grid cells are block boxes; custom cells centre their content.
const Cell = ({ children }) => (
  <Box sx={{ display: "flex", alignItems: "center", height: 1, minWidth: 0 }}>
    {children}
  </Box>
);
Cell.propTypes = { children: PropTypes.node };

function BudgetCell({ data }) {
  const notInForce = notInForceText(data);
  return (
    <Box
      data-testid={`budget-row-${data.id}`}
      sx={{ lineHeight: 1.2, py: 0.75, minWidth: 0 }}
    >
      <Typography typography="s1" noWrap>
        {data.label}
      </Typography>
      {notInForce ? (
        <Typography
          typography="s3"
          color="warning.main"
          noWrap
          title={notInForce}
        >
          {notInForce}
        </Typography>
      ) : (
        data.entry?.description && (
          <Typography typography="s3" color="text.secondary" noWrap>
            {data.entry.description}
          </Typography>
        )
      )}
    </Box>
  );
}
BudgetCell.propTypes = { data: PropTypes.object };

// An organization-level row: the org's spend for the level's period from
// the request logs (GET /agentcc/spend-summary/) against its limit. A team,
// user, key or tag level is counted by the gateway only.
function UsedCell({ data }) {
  const limitText =
    data.limit > 0 ? formatCostUpperBound(data.limit) : "No limit";
  if (data.group !== "org") {
    return (
      <Cell>
        <Typography typography="s2" color="text.secondary">
          {`Counted by the gateway · limit ${limitText}`}
        </Typography>
      </Cell>
    );
  }
  if (data.spendLoading) {
    return (
      <Cell>
        <Skeleton width={120} />
      </Cell>
    );
  }
  const known = typeof data.spend === "number";
  const spentText = known
    ? formatCost(data.spend)
    : data.spendFailed
      ? "Spend unavailable"
      : "—";
  const used = known && data.limit > 0 ? (data.spend / data.limit) * 100 : 0;
  return (
    <Stack justifyContent="center" gap={0.5} sx={{ height: 1, minWidth: 0 }}>
      <Typography typography="s2" noWrap>
        {`${spentText} / ${limitText}`}
      </Typography>
      <LinearProgress
        variant="determinate"
        value={Math.min(used, 100)}
        color={used >= 90 ? "error" : used >= 70 ? "warning" : "primary"}
        sx={{ height: 6, borderRadius: 3 }}
        aria-label={known ? `${used.toFixed(1)}% used` : "Spend not known"}
      />
    </Stack>
  );
}
UsedCell.propTypes = { data: PropTypes.object };

function ActionCell({ data }) {
  const chip = actionChip(data);
  return (
    <Stack justifyContent="center" gap={0.25} sx={{ height: 1, minWidth: 0 }}>
      <Box>
        <GatewayStatusChip tone={chip.tone} label={chip.label} />
      </Box>
      {data.action === "throttle" && (
        <Typography
          typography="s3"
          color="text.secondary"
          noWrap
          title={throttleText(data)}
        >
          {throttleText(data)}
        </Typography>
      )}
    </Stack>
  );
}
ActionCell.propTypes = { data: PropTypes.object };

// Setting and removing a budget are Owner or Admin only: for anyone else
// the controls show disabled and say why. Team, user, key and tag levels
// are edited in Org config > Budgets.
function RowActions({ data, context }) {
  if (data.group !== "org") {
    return (
      <Cell>
        <Link
          component={RouterLink}
          href={`${paths.dashboard.gateway.settings}?tab=org-config`}
          typography="s2"
          underline="hover"
        >
          Edit in Org config
        </Link>
      </Cell>
    );
  }
  const { canEdit } = context;
  const control = (label, icon, onClick, show) => {
    if (!show) return null;
    const button = (
      <IconButton
        size="small"
        aria-label={`${label} budget ${data.level}`}
        onClick={() => onClick(data)}
        disabled={!canEdit}
      >
        <SvgColor src={icon} sx={{ width: 16, height: 16 }} />
      </IconButton>
    );
    return canEdit ? (
      <Tooltip title={label}>{button}</Tooltip>
    ) : (
      <GatewayAdminOnly allowed={false}>{button}</GatewayAdminOnly>
    );
  };
  return (
    <Stack direction="row" gap={0.5} alignItems="center" sx={{ height: 1 }}>
      {control(
        "Edit",
        "/assets/icons/ic_edit.svg",
        context.onEdit,
        data.level === "org_limit" || data.level === "hard_limit",
      )}
      {control(
        "Remove",
        "/assets/icons/ic_delete.svg",
        context.onRemove,
        data.removable,
      )}
    </Stack>
  );
}
RowActions.propTypes = { data: PropTypes.object, context: PropTypes.object };

const COLUMNS = [
  {
    field: "label",
    headerName: "Budget",
    minWidth: 220,
    flex: 1.6,
    cellRenderer: BudgetCell,
  },
  {
    field: "period",
    headerName: "Period",
    minWidth: 110,
    flex: 0.6,
    valueFormatter: ({ value }) => PERIOD_LABEL[value] || value,
  },
  {
    colId: "used",
    headerName: "Spent / limit",
    minWidth: 200,
    flex: 1.3,
    sortable: false,
    cellRenderer: UsedCell,
  },
  {
    field: "action",
    headerName: "At the limit",
    minWidth: 220,
    flex: 1.2,
    cellRenderer: ActionCell,
  },
  {
    field: "alertThreshold",
    headerName: "Alert at",
    minWidth: 110,
    flex: 0.5,
    valueFormatter: ({ value }) => (value == null ? "—" : `${value}%`),
  },
  {
    colId: "actions",
    headerName: "",
    minWidth: 150,
    flex: 0.7,
    sortable: false,
    resizable: false,
    cellRenderer: RowActions,
  },
];

// Budgets: every spending limit the organization set (the organization, its
// teams, users, keys and tags), what each organization-level budget has
// spent in its period, and what the gateway does at the limit. The budgets
// switch, and whether this gateway enforces budgets at all, come first
// (BudgetEnforcementCard).
export default function BudgetRBACSection() {
  const { canEdit } = useGatewayAdmin();
  const { gatewayId } = useGatewayContext();
  const {
    data: config,
    isLoading,
    error,
    refetch,
  } = useGatewayConfig(gatewayId, { meta: { errorHandled: true } });
  const removeBudget = useRemoveBudget();
  const [dialog, setDialog] = useState(null);
  // The budget a row's Remove asked to remove, until the user confirms.
  const [removeTarget, setRemoveTarget] = useState(null);
  const [removedLevels, setRemovedLevels] = useState(() => new Set());

  const storedBudgets = config?.budgets;
  const rows = useMemo(
    () => budgetRows(storedBudgets).filter((r) => !removedLevels.has(r.level)),
    [storedBudgets, removedLevels],
  );
  const periods = useMemo(() => orgSpendPeriods(rows), [rows]);
  const spend = useBudgetSpend(periods);

  // The last 30 days of the org's requests (start only: the overview read
  // takes no gateway id).
  const monthAgo = useMemo(
    () => new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString(),
    [],
  );
  const { data: month } = useAnalyticsOverview({ start: monthAgo });

  const gridRows = useMemo(
    () =>
      rows.map((row) => {
        const spent =
          row.group === "org"
            ? spend.data?.[row.period]?.total_spend
            : undefined;
        return {
          ...row,
          spend: typeof spent === "number" ? spent : null,
          spendLoading: row.group === "org" && spend.isLoading && !spent,
          spendFailed: row.group === "org" && spend.isError,
        };
      }),
    [rows, spend.data, spend.isLoading, spend.isError],
  );
  const hasOrgLevel = rows.some((row) => row.group === "org");
  const hasOtherLevel = rows.some((row) => row.group !== "org");

  // Optimistic: the row goes at once and comes back if the remove fails.
  const remove = (row) => {
    setRemovedLevels((prev) => new Set([...prev, row.level]));
    removeBudget.mutate(
      { gatewayId, level: row.level },
      {
        onSuccess: () =>
          enqueueSnackbar(`${row.label} budget removed`, {
            variant: "success",
          }),
        onError: () => {
          setRemovedLevels((prev) => {
            const next = new Set(prev);
            next.delete(row.level);
            return next;
          });
          enqueueSnackbar("Failed to remove the budget", { variant: "error" });
        },
      },
    );
  };

  // The dialog edits an Organization or Hard Limit budget with its stored
  // entry, so a save keeps its threshold, action and throttle rate.
  const edit = (row) =>
    setDialog({
      budget: {
        ...(row.entry || {}),
        level: row.level,
        limit: row.limit,
        on_exceed: row.action,
      },
    });

  const frame = (content) => (
    <GatewayPageFrame
      title="Budgets"
      description="Spending limits for your organization, its teams, users, API keys and tags, what each organization budget has spent this period, and what the gateway does at the limit."
      actions={
        <GatewayAdminOnly allowed={canEdit}>
          <Button
            variant="contained"
            color="primary"
            onClick={() => setDialog({ budget: null })}
            disabled={!canEdit || !gatewayId}
          >
            Set budget
          </Button>
        </GatewayAdminOnly>
      }
    >
      {content}
      <SetBudgetDialog
        open={dialog !== null}
        onClose={() => setDialog(null)}
        gatewayId={gatewayId}
        budget={dialog?.budget || null}
        period={orgBudgetPeriod(storedBudgets)}
      />
      <ConfirmDialog
        open={Boolean(removeTarget)}
        onClose={() => setRemoveTarget(null)}
        title={
          removeTarget
            ? `Remove the ${removeTarget.label} budget?`
            : "Remove budget"
        }
        message="The gateway no longer applies this limit to the organization's requests. Set budget adds a limit again."
        confirmLabel="Remove budget"
        confirmColor="error"
        isLoading={removeBudget.isPending}
        onConfirm={() => {
          if (!canEdit || !removeTarget) return;
          const target = removeTarget;
          setRemoveTarget(null);
          remove(target);
        }}
      />
    </GatewayPageFrame>
  );

  if (isLoading) {
    return frame(
      <Stack spacing={2} data-testid="budgets-loading">
        <Skeleton variant="rounded" height={120} />
        <Skeleton variant="rounded" height={240} />
      </Stack>,
    );
  }

  if (error) {
    return frame(
      <GatewayEmptyState
        icon="budgets"
        title="Couldn't load your budgets"
        description={error.message || "The config request failed."}
        actionLabel="Retry"
        onAction={() => refetch?.()}
      />,
    );
  }

  return frame(
    <>
      <BudgetEnforcementCard
        gatewayId={gatewayId}
        budgets={storedBudgets}
        sectionsEnforced={config?.org_sections_enforced}
      />

      <SettingsPanel
        title="Spend"
        description="All requests of the last 30 days, from the request logs."
      >
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: {
              xs: "1fr 1fr",
              md: "repeat(3, minmax(0, 1fr))",
            },
            gap: 2,
          }}
        >
          <Figure
            label="Spent"
            value={month ? <CostTotal {...overviewCost(month)} /> : "—"}
          />
          <Figure
            label="Requests"
            value={
              month
                ? Number(val(month.total_requests) ?? 0).toLocaleString()
                : "—"
            }
          />
          <Figure label="Budgets" value={String(rows.length)} />
        </Box>
      </SettingsPanel>

      {hasOrgLevel && (
        <Typography typography="s2" color="text.secondary">
          Organization spend for each budget&apos;s period, from the request
          logs. The gateway checks each limit against its own count of this
          spend, which can be lower than the bar after a gateway restart or a
          change of the budget&apos;s period.
        </Typography>
      )}

      <GatewayGrid
        rowData={gridRows}
        columnDefs={COLUMNS}
        stateKey="gateway-budgets"
        getRowId={(params) => params.data.id}
        height={Math.max(gridRows.length, 1) * 56 + 42}
        rowHeight={56}
        context={{ canEdit, onEdit: edit, onRemove: setRemoveTarget }}
        emptyState={{
          icon: "budgets",
          title: "No budgets yet",
          description:
            "Set a limit for your organization, or per team, user, API key and tag in Org config > Budgets.",
          actionLabel: canEdit ? "Set budget" : undefined,
          onAction: canEdit ? () => setDialog({ budget: null }) : undefined,
        }}
      />

      {hasOtherLevel && (
        <Typography typography="s2" color="text.secondary">
          Spend for a team, user, key or tag level is counted by the gateway;
          this page shows organization spend only. Edit those levels in Org
          config &gt; Budgets.
        </Typography>
      )}

      <Typography typography="s2" color="text.secondary">
        Per-request costs are in{" "}
        <Link
          component={RouterLink}
          href={paths.dashboard.gateway.logs}
          underline="hover"
        >
          Logs
        </Link>
        .
      </Typography>
    </>,
  );
}
