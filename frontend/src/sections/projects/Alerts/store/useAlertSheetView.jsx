import { useCallback, useEffect } from "react";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useNavigate } from "react-router";
import { format } from "date-fns";
import { enqueueSnackbar } from "notistack";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import SvgColor from "src/components/svg-color";
import { Typography } from "@mui/material";
import {
  ActionCell,
  IssueNameCell,
  StatusCell,
} from "../components/AlertsListView/AlertCells";
import { useAlertSheetStore } from "./useAlertSheetStore";
import { buildAlertTraceLink } from "../alertTraceLink";

// The window "View trace" should scope to. A row action names one fire and uses
// its window. The header names none, so it spans every fire, taken from the
// rule-level window_start/window_end the details endpoint aggregates before its
// type filter and pagination — reading the newest row out of logs.results would
// instead follow whichever slice the issues grid last requested.
export const pickTraceWindow = (alertRuleDetails, clickedLog) => {
  if (clickedLog) {
    return {
      windowStart: clickedLog.time_window_start,
      windowEnd: clickedLog.time_window_end,
    };
  }
  return {
    windowStart: alertRuleDetails?.windowStart ?? alertRuleDetails?.window_start,
    windowEnd: alertRuleDetails?.windowEnd ?? alertRuleDetails?.window_end,
  };
};

// Custom hook that combines Zustand store with React hooks and mutations
export const useAlertSheetView = () => {
  const store = useAlertSheetStore();
  const navigate = useNavigate();

  const { mutate: resolveAlerts, isPending: isResolving } = useMutation({
    mutationFn: (data) => axios.post(endpoints.project.resolveAlerts, data),
    onSuccess: (data) => {
      enqueueSnackbar(data.data.result, { variant: "success" });
      store.handleClearSelection();
      refreshGrid();
      handleCancelSelection();
    },
  });

  const handleSelectAll = useCallback(() => {
    if (!store?.gridRef.current) return;

    store?.gridRef.current.api?.forEachNode((node) => node.setSelected(true));
    const allRows = [];
    store?.gridRef.current.api?.forEachNode((node) => {
      if (node.data) allRows.push(node.data?.id);
    });
    store.setSelectedRows(allRows);
  }, [store]);

  const refreshGrid = useCallback(() => {
    if (store?.gridRef.current && store?.gridRef.current.api) {
      const api = store?.gridRef.current.api;
      if (api.refreshServerSide) {
        api.refreshServerSide();
      }
    }
  }, [store?.gridRef]);

  const handleCancelSelection = useCallback(() => {
    if (store?.gridRef.current) {
      store?.gridRef.current.api.deselectAll();
      store.setSelectedRows([]);
      store.setSelectedAll(false);
    }
  }, [store]);

  const handleViewTrace = useCallback(
    (clickedLog) => {
      const details = store?.alertRuleDetails;
      if (!details?.project) return;
      const { windowStart, windowEnd } = pickTraceWindow(details, clickedLog);
      navigate(
        buildAlertTraceLink({
          projectId: details.project,
          windowStart,
          windowEnd,
          monitorFilters: details.filters,
          metricType: details.metric_type,
        }),
      );
    },
    [navigate, store?.alertRuleDetails],
  );

  const handleResolveAlerts = () => {
    if (store?.selectedAll) {
      resolveAlerts({
        select_all: true,
        exclude_ids: Array.from(store?.excludingIds),
      });
      trackEvent(Events.alertIssueResolvedClicked, {
        [PropertyName.id]: store?.alertRuleDetails?.id,
        [PropertyName.list]: [],
      });
    } else if (store?.selectedRows?.length > 0) {
      const ids = store?.selectedRows?.map((row) => row?.id);
      resolveAlerts({ log_ids: ids });
      trackEvent(Events.alertIssueResolvedClicked, {
        [PropertyName.id]: store?.alertRuleDetails?.id,
        [PropertyName.list]: ids,
      });
    }
  };

  // Initialize column definitions
  useEffect(() => {
    const columnDefs = [
      {
        headerName: "Issue",
        field: "message",
        flex: 2,
        sortable: true,
        cellRenderer: IssueNameCell,
      },
      {
        headerName: "Trigger type",
        field: "type",
        flex: 1,
        cellRenderer: StatusCell,
        cellRendererParams: (params) => ({
          ...params,
          value: params?.data?.type,
        }),
      },
      {
        headerName: "Status",
        field: "resolved",
        flex: 1,
        cellRenderer: StatusCell,
        cellRendererParams: (params) => ({
          ...params,
          value: params?.data?.resolved === true ? "resolved" : "",
        }),
      },
      {
        headerName: "Triggered at",
        field: "created_at",
        flex: 1,
        sortable: true,
        valueFormatter: ({ value }) => {
          if (!value) return "";
          const date = new Date(value);
          return format(date, "dd-MM-yyyy, HH:mm");
        },
      },
      {
        headerName: "Actions",
        field: "actions",
        flex: 1,
        cellRenderer: ActionCell,
        cellRendererParams: (params) => {
          const isResolved = params.data?.resolved;

          const options = [
            !isResolved && {
              label: "Change to resolved",
              value: "resolve",
              component: (
                <>
                  <SvgColor
                    src="/assets/icons/status/success.svg"
                    sx={{ width: 14, height: 14 }}
                  />
                  <Typography
                    variant="s1"
                    color="text.primary"
                    fontWeight="fontWeightRegular"
                    className="action-label"
                  >
                    Change to resolved
                  </Typography>
                </>
              ),
            },
            {
              label: "View trace",
              value: "view_trace",
              component: (
                <>
                  <SvgColor
                    src="/assets/icons/custom/eye.svg"
                    sx={{ width: 16, height: 16 }}
                  />
                  <Typography
                    variant="s1"
                    color="text.primary"
                    fontWeight="fontWeightRegular"
                    className="action-label"
                  >
                    View trace
                  </Typography>
                </>
              ),
            },
          ].filter(Boolean);

          return {
            options,
            onClick: (action) => {
              if (action === "resolve") {
                resolveAlerts({ log_ids: [params?.data?.id] });
                trackEvent(Events.alertIssueResolvedClicked, {
                  [PropertyName.id]: store?.alertRuleDetails?.id,
                  [PropertyName.list]: [params?.data?.id],
                });
              } else if (action === "view_trace") {
                handleViewTrace(params?.data);
              }
            },
          };
        },
      },
    ];

    store.setColumnDefs(columnDefs);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [store?.alertRuleDetails?.id, handleViewTrace, resolveAlerts]);

  return {
    // State
    ...store,
    excludingIds: Array.from(store.excludingIds),
    isResolving,
    handleViewTrace,
    handleResolveAlerts,
    handleSelectAll,
    refreshGrid,
    handleCancelSelection,
  };
};
