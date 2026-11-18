import { Box, Button, IconButton, keyframes } from "@mui/material";
import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useNavigate, useParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import PropTypes from "prop-types";
import axios, { endpoints } from "src/utils/axios";
import { DataTable, DataTablePagination } from "src/components/data-table";
import { useDebounce } from "src/hooks/use-debounce";
import { trackEvent, Events } from "src/utils/Mixpanel";
import SvgColor from "src/components/svg-color";
import StatusBadge from "src/components/status-badge/StatusBadge";
import { EXECUTION_STATUS_ROLE } from "src/sections/common/simulation/constants/statusStyles";
import CustomTooltip from "src/components/tooltip";
import EmptyLayout from "src/components/EmptyLayout/EmptyLayout";
import { ShowComponent } from "src/components/show/ShowComponent";
import { useReRunExperiment, useStopExperiment } from "../Experiment/api";
import { useRunExperimentStoreShallow } from "../states";
import { format } from "date-fns";

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
              border: "1px solid",
              borderColor: isReRunningExperiment ? "text.disabled" : "divider",
              backgroundColor: "transparent",
              p: 0,
            }}
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
                border: "1px solid",
                borderColor: isStoppingExperiment
                  ? "border.hover"
                  : "border.default",
                backgroundColor: "transparent",
                p: 0,
              }}
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
const REFRESH_STATUSES = new Set(["Running", "NotStarted", "Queued"]);
const PAGE_SIZE = 20;

// ── Component ────────────────────────────────────────────────────────

const ExperimentTab = React.forwardRef(
  ({ experimentSearch, setRowSelected, setCurrentTab }, ref) => {
    const { dataset } = useParams();
    const navigate = useNavigate();

    const [page, setPage] = useState(0);
    const [rowSelection, setRowSelection] = useState({});
    const refreshTimerRef = useRef(null);

    const { initiateCreateMode } = useRunExperimentStoreShallow((state) => ({
      initiateCreateMode: state.initiateCreateMode,
    }));

    const debouncedSearch = useDebounce(experimentSearch, 400);

    // Reset page when search changes
    useEffect(() => {
      setPage(0);
    }, [debouncedSearch]);

    // ── Data fetching ────────────────────────────────────────────────
    const {
      data: response,
      isLoading,
      refetch,
    } = useQuery({
      queryKey: ["experiment-list", dataset, page, debouncedSearch],
      queryFn: () =>
        axios.get(endpoints.develop.experiment.experimentListPaginated, {
          params: {
            dataset_id: dataset,
            page: page + 1,
            limit: PAGE_SIZE,
            search: debouncedSearch || undefined,
          },
        }),
      select: (res) => res?.data,
      keepPreviousData: true,
      refetchOnWindowFocus: false,
    });

    const rows = response?.results || [];
    const totalCount = response?.count || 0;

    // ── Auto-refresh when experiments are running ────────────────────
    const hasRunning = rows.some((r) => REFRESH_STATUSES.has(r.status));

    useEffect(() => {
      if (hasRunning) {
        refreshTimerRef.current = setInterval(() => refetch(), 10000);
      }
      return () => {
        if (refreshTimerRef.current) {
          clearInterval(refreshTimerRef.current);
          refreshTimerRef.current = null;
        }
      };
    }, [hasRunning, refetch]);

    // ── Expose refresh + clear to parent via ref ─────────────────────
    React.useImperativeHandle(
      ref,
      () => ({
        refreshExperimentTab: () => {
          refetch();
          setRowSelection({});
          setRowSelected([]);
        },
        clearRowSelection: () => {
          setRowSelection({});
          setRowSelected([]);
        },
      }),
      [refetch, setRowSelected],
    );

    // ── Selection sync ───────────────────────────────────────────────
    const handleRowSelectionChange = useCallback(
      (sel) => {
        setRowSelection(sel);
        const selected = Object.keys(sel)
          .filter((k) => sel[k])
          .map((k) => rows[parseInt(k, 10)])
          .filter(Boolean);
        setRowSelected(selected);
      },
      [rows, setRowSelected],
    );

    // ── Row click → navigate ─────────────────────────────────────────
    const handleRowClick = useCallback(
      (row) => {
        trackEvent(Events.expSelected, {
          datasetId: row.dataset,
          experimentName: row.name,
        });
        const encoded = encodeURIComponent(
          JSON.stringify({ id: row.id, name: row.name }),
        );
        navigate(
          `/dashboard/develop/experiment/${row.id}/data?datasetId=${dataset}&payload=${encoded}`,
        );
      },
      [dataset, navigate],
    );

    // ── Columns (TanStack format → DataTable) ────────────────────────
    const columns = useMemo(
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

    // ── Empty state ──────────────────────────────────────────────────
    if (!isLoading && rows.length === 0 && !debouncedSearch) {
      return (
        <Box
          sx={{
            flex: 1,
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            p: 4,
          }}
        >
          <EmptyLayout
            title="Ready to run your first experiment"
            description="Run your first experiment to test, compare, and improve prompt performance."
            icon="/assets/icons/navbar/ic_experiment.svg"
            action={
              <Button
                color="primary"
                variant="contained"
                onClick={() => {
                  setCurrentTab("data");
                  setTimeout(() => initiateCreateMode(), 700);
                }}
              >
                Run Experiment
              </Button>
            }
          />
        </Box>
      );
    }

    return (
      <Box
        sx={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          p: "12px",
          overflow: "hidden",
        }}
      >
        <DataTable
          columns={columns}
          data={rows}
          isLoading={isLoading}
          rowCount={totalCount}
          enableSelection
          rowSelection={rowSelection}
          onRowSelectionChange={handleRowSelectionChange}
          onRowClick={handleRowClick}
          getRowId={(row) => row.id}
          emptyMessage={
            debouncedSearch
              ? "No experiments match your search"
              : "No experiments yet"
          }
        />

        <DataTablePagination
          page={page}
          pageSize={PAGE_SIZE}
          total={totalCount}
          onPageChange={setPage}
          pageSizeOptions={[PAGE_SIZE]}
        />
      </Box>
    );
  },
);

ExperimentTab.displayName = "ExperimentTab";

ExperimentTab.propTypes = {
  experimentSearch: PropTypes.string,
  setRowSelected: PropTypes.func.isRequired,
  setCurrentTab: PropTypes.func,
};

export default ExperimentTab;
