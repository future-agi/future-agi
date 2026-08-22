import {
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
import { getCompareChartConfig, getSimpleLineChartConfig } from "../common";
import { useQuery } from "@tanstack/react-query";
import axiosInstance, { endpoints } from "src/utils/axios";
import { getDefaultAlertConfigValues } from "./validation";
import { useAlertSheetView } from "../store/useAlertSheetView";
import { useAlertStore } from "../store/useAlertStore";
import _ from "lodash";
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
  const thresholdTimeoutRef = useRef(null);

  const handlePayloadChange = useCallback((payload, enabled) => {
    setQueryPayload(payload);
    setIsQueryEnabled(enabled);
  }, []);

  const { data: queryData, refetch: refetchQuery } = useQuery({
    queryKey: ["alert-graph", openSheetView, dateFilter],
    queryFn: () =>
      axiosInstance.get(endpoints.project.getAlertGraph(openSheetView), {
        params: {
          start_date: dateFilter?.dateFilter?.[0],
          end_date: dateFilter?.dateFilter?.[1],
        },
      }),
    enabled: Boolean(openSheetView && !isFormDirty),
  });

  const { data: mutationData, refetch: refetchPreviewData } = useQuery({
    queryKey: ["preview-graph", queryPayload, dateFilter],
    queryFn: () =>
      axiosInstance.post(endpoints.project.getAlertGraphPreview, queryPayload, {
        params: {
          start_date: dateFilter?.dateFilter?.[0],
          end_date: dateFilter?.dateFilter?.[1],
        },
      }),
    enabled: Boolean(isQueryEnabled && Boolean(queryPayload)),
  });

  const fetchedData = useMemo(() => {
    if (openSheetView && queryData?.data && !isFormDirty) {
      return queryData;
    } else if ((!openSheetView || isFormDirty) && mutationData) {
      return mutationData;
    }
  }, [queryData, mutationData, openSheetView, isFormDirty]);

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

        const isLessThan = thresholdOperator === "less_than";

        const thresholds = isLessThan
          ? [
              {
                value: max,
                y2: warningValue,
                fillColor: "#00A25108",
                borderColor: "#00A251",
              },
              {
                value: warningValue,
                y2: criticalValue,
                fillColor: "#E9690C08",
                borderColor: "#F49A54",
              },
              {
                value: criticalValue,
                y2: min,
                fillColor: "#D92D2033",
                borderColor: "#D92D20",
              },
            ]
          : [
              {
                value: max,
                y2: criticalValue,
                fillColor: "#D92D2033",
                borderColor: "#D92D20",
              },
              {
                value: criticalValue,
                y2: warningValue,
                fillColor: "#E9690C08",
                borderColor: "#F49A54",
              },
              {
                value: warningValue,
                y2: min,
                fillColor: "#00A25108",
                borderColor: "#00A251",
              },
            ];

        chartConfig = getSimpleLineChartConfig(
          fetchedData?.data,
          {
            seriesName: _.startCase(_.toLower(alertType)),
            thresholds,
          },
          { isDark },
        );
      } else if (selectedThresHoldType === "percentage_change") {
        chartConfig = getCompareChartConfig(
          fetchedData?.data,
          { seriesName: _.startCase(_.toLower(alertType)) },
          { isDark },
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
    thresholdOperator,
    warningValue,
    criticalValue,
    isDark,
  ]);

  // Render chart with proper loading states
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
              style: { fontSize: "12px", colors: isDark ? "#a1a1aa" : "#666" },
            },
          },
        }}
      />
    );
  }, [chartKey, openSheetView, options, selectedThresHoldType, series]);

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
              refetchQuery();
            } else if (isQueryEnabled && Boolean(queryPayload)) {
              refetchPreviewData();
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
      {renderChart()}
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
