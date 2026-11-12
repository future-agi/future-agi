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
import { useSearchParams } from "react-router-dom";

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
import {
  PERIOD_RESET,
  budgetRows,
  orgBudgetPeriod,
  orgSpendPeriods,
  savedAtMs,
} from "./budgetLevels";
import { useBudgetSpend } from "./hooks/useBudgetSpend";
import SetBudgetDialog from "./SetBudgetDialog";
import BudgetCapDeepLink from "./BudgetCapDeepLink";

const PERIOD_LABEL = {
  daily: "Daily",
  weekly: "Weekly",
  monthly: "Monthly",
  total: "All time",
};

// A Set budget save's time, in UTC: the date, then the time.
const SAVED_DATE = new Intl.DateTimeFormat("en-US", {
  timeZone: "UTC",
  month: "short",
  day: "numeric",
  year: "numeric",
});
const SAVED_TIME = new Intl.DateTimeFormat("en-US", {
  timeZone: "UTC",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

// How much of a limit the period's spend has used, whole percent down, so
// a limit is never shown as reached before it is.
function usedText(spend, limit) {
  const used = (spend / limit) * 100;
  if (spend > 0 && used < 1) return "Under 1% used";
  return `${Math.floor(used)}% used`;
}

const levelCount = (n) => `${n} ${n === 1 ? "level" : "levels"}`;

// A level that is not in force gets a taller row, so its warning wraps and
// shows in full.
const rowHeightOf = (row) => (row?.overriddenBy ? 76 : 56);

// The organization's budget period (the period a Set budget save counts
// over) and when it starts again.
function orgPeriodText(period) {
  const label = PERIOD_LABEL[period] || period;
  const reset = PERIOD_RESET[period];
  return reset
    ? `Organization period: ${label}. ${reset}.`
    : `Organization period: ${label}.`;
}

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
        // Grid cells do not wrap by default; this warning wraps in its
        // taller row.
        <Typography
          typography="s3"
          color="warning.main"
          sx={{ whiteSpace: "normal" }}
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

// The level's period and when it starts again (UTC).
function PeriodCell({ data }) {
  const reset = PERIOD_RESET[data.period];
  return (
    <Stack justifyContent="center" sx={{ height: 1, minWidth: 0 }}>
      <Typography typography="s2" noWrap>
        {PERIOD_LABEL[data.period] || data.period}
      </Typography>
      {reset && (
        <Typography typography="s3" color="text.secondary" noWrap title={reset}>
          {reset}
        </Typography>
      )}
    </Stack>
  );
}
PeriodCell.propTypes = { data: PropTypes.object };

// When the Set budget dialog last saved the level (its saved_at, in UTC).
// Levels set in Org config carry no save time.
function SavedCell({ data }) {
  if (data.savedAtMs == null) {
    return (
      <Cell>
        <Typography typography="s2" color="text.secondary">
          —
        </Typography>
      </Cell>
    );
  }
  return (
    <Stack
      justifyContent="center"
      sx={{ height: 1, minWidth: 0 }}
      title={new Date(data.savedAtMs).toISOString()}
    >
      <Typography typography="s2" noWrap>
        {SAVED_DATE.format(data.savedAtMs)}
      </Typography>
      <Typography typography="s3" color="text.secondary" noWrap>
        {`${SAVED_TIME.format(data.savedAtMs)} UTC`}
      </Typography>
    </Stack>
  );
}
SavedCell.propTypes = { data: PropTypes.object };

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
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="baseline"
        gap={1}
        sx={{ minWidth: 0 }}
      >
        <Typography typography="s2" noWrap>
          {`${spentText} / ${limitText}`}
        </Typography>
        {known && data.limit > 0 && (
          <Typography
            typography="s3"
            color="text.secondary"
            noWrap
            sx={{ flexShrink: 0 }}
          >
            {usedText(data.spend, data.limit)}
          </Typography>
        )}
      </Stack>
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
          href={context.budgetEditorHref}
          typography="s2"
          underline="hover"
          sx={{ whiteSpace: "normal", lineHeight: 1.3 }}
        >
          {context.canEdit ? "Edit in Org config" : "View in Org config"}
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
    minWidth: 160,
    flex: 2,
    cellRenderer: BudgetCell,
  },
  {
    field: "period",
    headerName: "Period",
    minWidth: 185,
    flex: 1,
    cellRenderer: PeriodCell,
  },
  {
    colId: "used",
    headerName: "Spent / limit",
    minWidth: 170,
    flex: 1,
    sortable: false,
    cellRenderer: UsedCell,
  },
  {
    field: "action",
    headerName: "At the limit",
    minWidth: 130,
    flex: 0.9,
    cellRenderer: ActionCell,
  },
  {
    field: "alertThreshold",
    headerName: "Alert at",
    minWidth: 80,
    flex: 0.4,
    valueFormatter: ({ value }) => (value == null ? "—" : `${value}%`),
  },
  {
    field: "savedAtMs",
    headerName: "Last saved",
    headerTooltip:
      "When Set budget last saved this level, in UTC. Levels set in Org config show a dash.",
    minWidth: 110,
    flex: 0.7,
    cellRenderer: SavedCell,
  },
  {
    colId: "actions",
    headerName: "",
    // A fixed width: a pinned column with flex takes a share of the whole
    // grid, which pushed the other columns into a sideways scroll at 1280.
    width: 120,
    minWidth: 120,
    // Edit and Remove stay in view when the columns scroll sideways.
    pinned: "right",
    sortable: false,
    resizable: false,
    cellRenderer: RowActions,
  },
];

// Budgets: every spending limit the organization set (the organization, its
// teams, users, keys and tags), what each organization-level budget has
// spent in its period, and what the gateway does at the limit. The budgets
// switch, and whether this gateway enforces budgets at all, come first
// (BudgetEnforcementCard); then the levels with their period, the notes on
// where their spend comes from, and last the organization's spend of the
// last 30 days.
export default function BudgetRBACSection() {
  const { canEdit } = useGatewayAdmin();
  const { gatewayId } = useGatewayContext();
  const [searchParams] = useSearchParams();
  // Keep any org/gateway context in the URL; the existing providers remain
  // authoritative. Opening the editor never changes the selected workspace.
  const budgetEditorParams = new URLSearchParams(searchParams);
  budgetEditorParams.set("tab", "org-config");
  budgetEditorParams.set("section", "budgets");
  const budgetEditorHref = `${paths.dashboard.gateway.settings}?${budgetEditorParams}`;
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
          savedAtMs: savedAtMs(row.entry),
        };
      }),
    [rows, spend.data, spend.isLoading, spend.isError],
  );
  const hasOrgLevel = rows.some((row) => row.group === "org");
  const hasOtherLevel = rows.some((row) => row.group !== "org");
  const orgPeriod = orgBudgetPeriod(storedBudgets);

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
          enqueueSnackbar("Failed to remove the budget", {
            variant: "error",
          });
        },
        // The write hook awaits its config refresh before settling. Never
        // keep a level excluded after that mutation: it may be created again.
        onSettled: () => {
          setRemovedLevels((prev) => {
            const next = new Set(prev);
            next.delete(row.level);
            return next;
          });
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
      description="Spending limits and what the gateway does when one is reached."
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
      <BudgetCapDeepLink />
      {content}
      <SetBudgetDialog
        open={dialog !== null}
        onClose={() => setDialog(null)}
        gatewayId={gatewayId}
        budget={dialog?.budget || null}
        period={orgPeriod}
        storedBudgets={storedBudgets}
        budgetEditorHref={budgetEditorHref}
        canEdit={canEdit}
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
        title="Budget levels"
        testId="budget-levels"
        description={
          <>
            <Box component="span" sx={{ display: "block" }}>
              <Box component="span" data-testid="budget-levels-count">
                {levelCount(rows.length)}
              </Box>
              {" · "}
              <Box component="span">
                Periods and team, user, API key and tag budgets open in Org
                config.
              </Box>
            </Box>
            <Box component="span" sx={{ display: "block" }}>
              {orgPeriodText(orgPeriod)}
            </Box>
          </>
        }
        actions={
          <>
            <Button
              component={RouterLink}
              href={budgetEditorHref}
              variant="outlined"
              size="small"
            >
              {canEdit ? "Change period" : "View period"}
            </Button>
            <Button
              component={RouterLink}
              href={budgetEditorHref}
              variant="outlined"
              size="small"
            >
              {canEdit ? "Manage scopes" : "View scopes"}
            </Button>
          </>
        }
      >
        <GatewayGrid
          rowData={gridRows}
          columnDefs={COLUMNS}
          stateKey="gateway-budgets"
          getRowId={(params) => params.data.id}
          height={
            (gridRows.reduce((sum, row) => sum + rowHeightOf(row), 0) || 56) +
            42
          }
          rowHeight={56}
          getRowHeight={(params) => rowHeightOf(params.data)}
          scrollCue
          context={{
            canEdit,
            onEdit: edit,
            onRemove: setRemoveTarget,
            budgetEditorHref,
          }}
          emptyState={{
            // One primary action per view: Set budget stays in the page
            // header, where it is on every state of the page, and the empty
            // state names it rather than repeating the button.
            icon: "budgets",
            title: "No budgets yet",
            description:
              "Use Set budget above to add an organization limit. Manage scopes adds team, user, API key and tag budgets in Org config.",
            sx: { py: { xs: 3, sm: 4 }, flex: "0 0 auto" },
          }}
        />

        {(hasOrgLevel || hasOtherLevel) && (
          <Stack spacing={0.75} sx={{ maxWidth: "72ch" }}>
            {hasOrgLevel && (
              <Typography typography="s2" color="text.secondary">
                Organization spend for each budget&apos;s period, from the
                request logs. The gateway checks each limit against its own
                count of this spend, which can be lower than the bar after a
                gateway restart or a change of the budget&apos;s period.
              </Typography>
            )}
            {hasOtherLevel && (
              <Typography typography="s2" color="text.secondary">
                Spend for a team, user, key or tag level is counted by the
                gateway; this page shows organization spend only. Edit those
                levels in Org config &gt; Budgets.
              </Typography>
            )}
          </Stack>
        )}
      </SettingsPanel>

      <SettingsPanel
        title="Last 30 days"
        description={
          <>
            Every request in your organization, from the request logs.
            Per-request costs are in{" "}
            <Link
              component={RouterLink}
              href={paths.dashboard.gateway.logs}
              underline="hover"
            >
              Logs
            </Link>
            .
          </>
        }
      >
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: {
              xs: "1fr 1fr",
              md: "repeat(4, minmax(0, 1fr))",
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
        </Box>
      </SettingsPanel>
    </>,
  );
}
