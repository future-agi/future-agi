import {
  Alert,
  Box,
  Button,
  Divider,
  Stack,
  Typography,
  useTheme,
} from "@mui/material";
import React, {
  lazy,
  Suspense,
  useState,
  useCallback,
  useMemo,
  useRef,
  useEffect,
} from "react";
import TracingControls from "../../LLMTracing/TracingControls";
import PropTypes from "prop-types";
import SvgColor from "src/components/svg-color";
import AlertsChart from "./AlertsChart";
import {
  alertChartSeriesName,
  getCompareChartConfig,
  getSimpleLineChartConfig,
} from "../common";
import { staticThresholdBands } from "../alertThresholdBands";
import { useQuery } from "@tanstack/react-query";
import axiosInstance, { endpoints } from "src/utils/axios";
import { getDefaultAlertConfigValues } from "./validation";
import { useAlertSheetView } from "../store/useAlertSheetView";
import { useAlertStore } from "../store/useAlertStore";
import {
  keepPreviousMonitorGraphData,
  MONITOR_GRAPH_ERROR_MESSAGE,
  monitorGraphDisplayState,
  monitorGraphRequestConfig,
} from "../../monitor_graph_read";
import { errorStatus } from "../../requestErrors";
import { getSafeActionErrorMessage } from "src/utils/errorUtils";
const AlertSettingsForm = lazy(() => import("./AlertSettingsForm"));

export default function AlertConfiguration({ dateFilter, setDateFilter }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const { alertRuleDetails } = useAlertSheetView();
  const [selectedThresHoldType, setSelectedThresHoldType] = useState(
    getDefaultAlertConfigValues(alertRuleDetails)?.threshold_type,
  );
  const [chartKey, setChartKey] = useState(0);
  const { alertType, openSheetView } = useAlertStore();
  const [thresholdOperator, setThresholdOperator] = useState("");
  const [warningValue, setWarningValue] = useState("");
  const [criticalValue, setCriticalValue] = useState("");
  const [isFormDirty, setFormIsDirty] = useState(false);
  const [queryPayload, setQueryPayload] = useState(null);
  const [isQueryEnabled, setIsQueryEnabled] = useState(false);
  const [previewAwaitingLabel, setPreviewAwaitingLabel] = useState(false);
  const thresholdTimeoutRef = useRef(null);
  const retainedGraphDataRef = useRef();
  // Whether the retained data is the saved graph or a preview.
  const retainedFromSavedGraphRef = useRef(false);

  const handlePayloadChange = useCallback((payload, enabled, awaitingLabel) => {
    setQueryPayload(payload);
    setIsQueryEnabled(enabled);
    setPreviewAwaitingLabel(Boolean(awaitingLabel));
  }, []);

  const savedGraphQuery = useQuery({
    queryKey: ["alert-graph", openSheetView, dateFilter],
    queryFn: ({ signal }) =>
      axiosInstance.get(
        endpoints.project.getAlertGraph(openSheetView),
        monitorGraphRequestConfig({ signal, dateFilter }),
      ),
    enabled: Boolean(openSheetView && !isFormDirty),
    placeholderData: keepPreviousMonitorGraphData,
    retry: false,
  });

  const previewGraphQuery = useQuery({
    queryKey: ["preview-graph", queryPayload, dateFilter],
    queryFn: ({ signal }) =>
      axiosInstance.post(
        endpoints.project.getAlertGraphPreview,
        queryPayload,
        monitorGraphRequestConfig({ signal, dateFilter }),
      ),
    enabled: Boolean(isQueryEnabled && Boolean(queryPayload)),
    placeholderData: keepPreviousMonitorGraphData,
    retry: false,
  });

  const useSavedGraph = Boolean(openSheetView && !isFormDirty);
  // The saved graph already has its label; only a preview can be waiting.
  const showAwaitingLabel = !useSavedGraph && previewAwaitingLabel;
  const activeGraphQuery = useSavedGraph ? savedGraphQuery : previewGraphQuery;
  const latestFetchedData = activeGraphQuery.data;

  useEffect(() => {
    if (latestFetchedData !== undefined) {
      retainedGraphDataRef.current = latestFetchedData;
      retainedFromSavedGraphRef.current = useSavedGraph;
    }
  }, [latestFetchedData, useSavedGraph]);

  const graphState = monitorGraphDisplayState({
    latestData: latestFetchedData,
    retainedData: retainedGraphDataRef.current,
    isError: activeGraphQuery.isError,
  });
  const fetchedData = graphState.data;
  // A graph the server refuses (400), such as the preview of an alert whose
  // level is outside its metric's range, says what to fix. A failure on the
  // server's side keeps the generic line.
  const graphErrorMessage =
    errorStatus(activeGraphQuery.error) === 400
      ? getSafeActionErrorMessage(
          activeGraphQuery.error,
          MONITOR_GRAPH_ERROR_MESSAGE,
        )
      : MONITOR_GRAPH_ERROR_MESSAGE;

  // Handle threshold type change with proper cleanup
  const handleThresholdTypeChange = useCallback(
    (newType) => {
      if (newType === selectedThresHoldType) return;

      setChartKey((prev) => prev + 1);

      if (thresholdTimeoutRef?.current) {
        clearTimeout(thresholdTimeoutRef.current);
      }

      thresholdTimeoutRef.current = setTimeout(() => {
        setSelectedThresHoldType(newType);
      }, 100);
    },
    [selectedThresHoldType],
  );

  // The saved graph keeps the alert's own rule; an edited preview is computed
  // on the current rule. Until a preview arrives, or while an invalid edit runs
  // none, the chart still draws the data it read last, so the name follows it.
  const showsSavedGraph =
    latestFetchedData !== undefined
      ? useSavedGraph
      : retainedFromSavedGraphRef.current;
  const seriesName = alertChartSeriesName({
    metricType: alertType,
    earlier: showsSavedGraph && alertRuleDetails?.thresholdRule === "earlier",
  });

  // A level saved as infinity or not-a-number has words in the saved alert,
  // and the form keeps its field empty until a number is typed there. While
  // it has no number it draws no line.
  const criticalWords = openSheetView
    ? alertRuleDetails?.criticalThresholdLabel
    : undefined;
  const warningWords = openSheetView
    ? alertRuleDetails?.warningThresholdLabel
    : undefined;

  const { series, options } = useMemo(() => {
    if (!selectedThresHoldType || !fetchedData?.data) {
      return { series: [], options: {} };
    }

    try {
      let chartConfig;

      if (selectedThresHoldType === "static") {
        const values =
          fetchedData?.data?.result?.map((res) => res?.value) || [];

        let max = values.length ? Math.max(...values, 100) : 100;
        max = Math.ceil(max * 1.1);

        let min = values.length ? Math.min(...values) : 0;
        min = Math.max(0, Math.floor(min * 0.9));

        const thresholds = staticThresholdBands(theme, {
          thresholdOperator,
          critical: { value: criticalValue, words: criticalWords },
          warning: { value: warningValue, words: warningWords },
          max,
          min,
          dangerAlpha: 0.2,
        });

        chartConfig = getSimpleLineChartConfig(
          fetchedData?.data,
          {
            seriesName,
            unitKey: alertType,
            thresholds,
          },
          { isDark, theme },
        );
      } else if (selectedThresHoldType === "percentage_change") {
        chartConfig = getCompareChartConfig(
          fetchedData?.data,
          { seriesName, unitKey: alertType },
          { isDark, theme },
        );
      }

      if (chartConfig) {
        const cleanOptions = {
          ...chartConfig.options,
          annotations: {
            yaxis: chartConfig.options.annotations?.yaxis || [],
            xaxis: [],
            points: [],
          },
        };

        return {
          series: chartConfig.series,
          options: cleanOptions,
        };
      }

      return { series: [], options: {} };
    } catch (error) {
      return { series: [], options: {} };
    }
  }, [
    selectedThresHoldType,
    fetchedData,
    alertType,
    seriesName,
    thresholdOperator,
    warningValue,
    criticalValue,
    criticalWords,
    warningWords,
    isDark,
  ]);

  // Render chart with proper loading states. The points sit at their true
  // instants; the axis labels and the tooltip's time print the browser's
  // local time, where ApexCharts would print UTC.
  const renderChart = useCallback(() => {
    return (
      <AlertsChart
        key={`${openSheetView} ${selectedThresHoldType}-${chartKey}`}
        series={series}
        options={{
          ...options,
          xaxis: {
            type: "datetime",
            convertedCatToNumeric: false, // include this explicitly
            labels: {
              datetimeUTC: false,
              style: { fontSize: "12px", colors: theme.palette.text.tertiary },
            },
          },
          tooltip: { ...options.tooltip, x: { format: "dd MMM HH:mm" } },
        }}
      />
    );
  }, [
    chartKey,
    openSheetView,
    options,
    selectedThresHoldType,
    series,
    theme.palette.text.tertiary,
  ]);

  useEffect(() => {
    return () => {
      if (thresholdTimeoutRef?.current) {
        clearTimeout(thresholdTimeoutRef.current);
      }
    };
  }, []);

  return (
    <Box>
      <Stack
        direction={"row"}
        alignItems={"center"}
        gap={3}
        sx={{
          width: {
            xs: "100%",
            sm: "80%",
            md: "50%",
            lg: "33vw",
          },
          pb: 2,
        }}
      >
        <TracingControls
          dateFilter={dateFilter}
          setDateFilter={setDateFilter}
          fullWidth={false}
        />
        <Button
          size="small"
          sx={{
            minWidth: "90px",
            color: "text.primary",
          }}
          startIcon={<SvgColor src={"/assets/icons/ic_reload.svg"} />}
          onClick={() => {
            if (openSheetView && !isFormDirty) {
              savedGraphQuery.refetch();
            } else if (isQueryEnabled && Boolean(queryPayload)) {
              previewGraphQuery.refetch();
            }
          }}
        >
          <Typography
            variant="s1"
            color={"text.primary"}
            fontWeight={"fontWeightRegular"}
          >
            Refresh
          </Typography>
        </Button>
      </Stack>
      {showAwaitingLabel && (
        <Alert
          severity="info"
          sx={{ mb: 2 }}
          data-testid="preview-awaiting-label"
        >
          Pick a label to preview this alert.
        </Alert>
      )}
      {!showAwaitingLabel && graphState.showError && (
        <Alert
          severity="error"
          sx={{ mb: 2 }}
          action={
            <Button
              color="inherit"
              size="small"
              disabled={activeGraphQuery.isFetching}
              onClick={() => activeGraphQuery.refetch()}
            >
              {activeGraphQuery.isFetching ? "Retrying…" : "Retry"}
            </Button>
          }
        >
          {graphErrorMessage}
        </Alert>
      )}
      {!showAwaitingLabel && graphState.showGraph && renderChart()}
      <Divider />
      <Box
        sx={{
          pt: 2,
          zIndex: 2,
          backgroundColor: "background.paper",
          position: "relative",
        }}
      >
        <Suspense>
          <AlertSettingsForm
            onThresholdTypeChange={handleThresholdTypeChange}
            setCriticalValue={setCriticalValue}
            setWarningValue={setWarningValue}
            setThresholdOperator={setThresholdOperator}
            setFormIsDirty={setFormIsDirty}
            onPayloadChange={handlePayloadChange}
          />
        </Suspense>
      </Box>
    </Box>
  );
}

AlertConfiguration.displayName = "AlertConfiguration";
AlertConfiguration.propTypes = {
  dateFilter: PropTypes.object,
  setDateFilter: PropTypes.func,
};
