import { Box, IconButton, keyframes } from "@mui/material";
import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { format } from "date-fns";
import SvgColor from "src/components/svg-color";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { EXECUTION_STATUS_ROLE } from "src/sections/common/simulation/constants/statusStyles";
import CustomTooltip from "src/components/tooltip";
import { ShowComponent } from "src/components/show/ShowComponent";
import { useReRunExperiment, useStopExperiment } from "../Experiment/api";

// The Experiments list's table: its columns, status badge and row actions.
// The dataset's Experiments tab uses them for its list, and the group of
// experiments created before runs were recorded uses the same ones.

// ── Status badge (matches ConsistentCellRender) ──────────────────────

const spin = keyframes`
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
`;

const STATUS_META = {
  NotStarted: {
    icon: "/assets/icons/ic_call_pending.svg",
    label: "Not Started",
  },
  Queued: { icon: "/assets/icons/ic_queued_header.svg" },
  Running: { icon: "/assets/icons/ic_queued_header.svg" },
  Completed: { icon: "/assets/icons/ic_completed.svg" },
  Cancelled: { icon: "/assets/icons/ic_close.svg" },
  Failed: { icon: "/assets/icons/ic_failed.svg" },
  Editing: { icon: "/assets/icons/ic_edit_pencil.svg" },
};

const StatusChip = ({ status }) => {
  const key = STATUS_META[status] ? status : "Completed";
  const s = STATUS_META[key];
  return (
    <StatusBadge
      status={status}
      label={s.label || status}
      role={EXECUTION_STATUS_ROLE[key]}
      icon={
        s.icon ? (
          <SvgColor
            src={s.icon}
            sx={{
              height: 14,
              width: 14,
              ...(status === "Running" && {
                animation: `${spin} 1s linear infinite`,
              }),
            }}
          />
        ) : undefined
      }
    />
  );
};

StatusChip.propTypes = { status: PropTypes.string };

// ── Actions cell (rerun / stop) ─────────────────────────────────────

const COMPLETED_STATUSES = ["completed", "failed", "cancelled"];
const RUNNING_STATUSES = ["running", "queued", "notstarted"];

const ExperimentActions = ({ experimentId, status, onRefresh }) => {
  const normalizedStatus = status?.toLowerCase();
  const { stopExperiment, isStoppingExperiment } = useStopExperiment(
    experimentId,
    onRefresh,
  );
  const { reRunExperiment, isReRunningExperiment } = useReRunExperiment(
    [experimentId],
    false,
    onRefresh,
  );

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        height: "100%",
        width: "100%",
        gap: 1,
      }}
      onClick={(e) => e.stopPropagation()}
    >
      <ShowComponent
        condition={
          !isReRunningExperiment &&
          COMPLETED_STATUSES.includes(normalizedStatus)
        }
      >
        <CustomTooltip
          size="small"
          type="black"
          arrow={true}
          show={true}
          title="Rerun Experiment"
        >
          <IconButton
            sx={{
              width: 32,
              height: 32,
              border: "1px solid",
              borderColor: isReRunningExperiment ? "text.disabled" : "divider",
              borderRadius: "4px",
              backgroundColor: "transparent",
              p: 0,
            }}
            aria-label="Rerun Experiment"
            data-testid="classic-experiment-rerun"
            onClick={() => reRunExperiment()}
            disabled={isReRunningExperiment}
          >
            <SvgColor
              sx={{ width: 20, height: 20 }}
              src="/assets/icons/navbar/ic_evaluate.svg"
            />
          </IconButton>
        </CustomTooltip>
      </ShowComponent>

      <ShowComponent condition={RUNNING_STATUSES.includes(normalizedStatus)}>
        <CustomTooltip
          size="small"
          type="black"
          arrow={true}
          show={true}
          title={
            normalizedStatus === "notstarted"
              ? "Experiment has not started yet"
              : "Stop Experiment"
          }
        >
          <span>
            <IconButton
              sx={{
                width: 32,
                height: 32,
                border: "1px solid",
                borderColor: isStoppingExperiment
                  ? "border.hover"
                  : "border.default",
                borderRadius: "4px",
                backgroundColor: "transparent",
                p: 0,
              }}
              aria-label="Stop Experiment"
              data-testid="classic-experiment-stop"
              onClick={() => stopExperiment()}
              disabled={
                isStoppingExperiment || normalizedStatus === "notstarted"
              }
            >
              <SvgColor
                sx={{ width: 20, height: 20, color: "error.main" }}
                src="/assets/icons/ic_stop.svg"
              />
            </IconButton>
          </span>
        </CustomTooltip>
      </ShowComponent>
    </Box>
  );
};

ExperimentActions.propTypes = {
  experimentId: PropTypes.string,
  status: PropTypes.string,
  onRefresh: PropTypes.func,
};

// ── Refresh interval for running experiments ─────────────────────────
export const REFRESH_STATUSES = new Set(["Running", "NotStarted", "Queued"]);
export const PAGE_SIZE = 20;

// The page a list row opens (its own module, so a runs-view cell can link to
// it without loading this table).
export { classicExperimentPath } from "./classicExperimentPath";

// ── Columns (TanStack format → DataTable) ────────────────────────────
export function useClassicExperimentColumns(refetch) {
  return useMemo(
    () => [
      {
        id: "name",
        accessorKey: "name",
        header: "Experiment Name",
        meta: { flex: 1.5 },
        minSize: 200,
      },
      {
        id: "status",
        accessorKey: "status",
        header: "Status",
        size: 140,
        enableSorting: false,
        cell: ({ getValue }) => <StatusChip status={getValue()} />,
      },
      {
        id: "models_count",
        accessorKey: "models_count",
        header: "Models",
        size: 100,
        enableSorting: false,
        cell: ({ getValue }) => (
          <Box sx={{ textAlign: "center", width: "100%" }}>
            {getValue() ?? 0}
          </Box>
        ),
      },
      {
        id: "eval_templates_count",
        accessorKey: "eval_templates_count",
        header: "Evals",
        size: 100,
        enableSorting: false,
        cell: ({ getValue }) => (
          <Box sx={{ textAlign: "center", width: "100%" }}>
            {getValue() ?? 0}
          </Box>
        ),
      },
      {
        id: "actions",
        header: "Actions",
        size: 100,
        enableSorting: false,
        cell: ({ row }) => (
          <ExperimentActions
            experimentId={row.original.id}
            status={row.original.status}
            onRefresh={refetch}
          />
        ),
      },
      {
        id: "created_at",
        accessorKey: "created_at",
        header: "Created At",
        size: 170,
        cell: ({ getValue }) => {
          const v = getValue();
          if (!v) return "";
          try {
            return format(new Date(v), "MM-dd-yyyy, HH:mm");
          } catch {
            return v;
          }
        },
      },
    ],
    [refetch],
  );
}
