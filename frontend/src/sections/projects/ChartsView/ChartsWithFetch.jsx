import { useQuery } from "@tanstack/react-query";
import PropTypes from "prop-types";
import React, { useMemo } from "react";
import ChartsGenerator from "./ChartsGenerator";
import axios, { endpoints } from "src/utils/axios";
import { transformEvaluationPayload } from "./common";
import { Box, Button, Skeleton, Typography } from "@mui/material";
import { useChartsViewContext } from "./ChartsViewProvider/ChartsViewContext";
import { getStorage } from "src/hooks/use-local-storage";
import { normalizeTimestamp } from "./ChartsViewProvider/common";
import {
  AGGREGATION_PREPARING_MESSAGE,
  awaitAggregationRequestWithDeadline,
  getExactAggregationReadState,
  QUERY_FAILED_RETRY_MESSAGE,
} from "src/utils/queryReadState";
import { INTERACTIVE_REQUEST_TIMEOUT_MS } from "src/config/runtime_limits";

const EVAL_CHART_REQUEST_TIMEOUT_MS = INTERACTIVE_REQUEST_TIMEOUT_MS;

export default function ChartWithFetch({ evaluation, observeId, inView }) {
  const autoRefresh = getStorage("autoRefresh") ?? false;
  const { selectedInterval, filters, handleZoomChange } =
    useChartsViewContext();

  const queryKey = [
    "chart-data",
    evaluation?.id,
    evaluation?.name,
    observeId,
    selectedInterval.toLowerCase(),
    JSON.stringify(filters),
  ];

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey,
    queryFn: ({ signal }) => {
      const payload = {
        project_id: observeId,
        property: "average",
        interval: selectedInterval?.toLowerCase(),
        filters: JSON.stringify(filters),
        ...transformEvaluationPayload(evaluation),
      };

      return awaitAggregationRequestWithDeadline(
        (requestSignal) =>
          axios.get(endpoints.project.getEvalGraph, {
            params: { ...payload },
            signal: requestSignal,
            timeout: EVAL_CHART_REQUEST_TIMEOUT_MS,
          }),
        { timeoutMs: EVAL_CHART_REQUEST_TIMEOUT_MS, signal },
      );
    },
    refetchInterval: autoRefresh && inView ? 10000 : false,
    staleTime: Infinity,
    refetchIntervalInBackground: false,
    enabled: inView,
  });

  const result = data?.data?.result;
  const retainedReadState = getExactAggregationReadState(result, {
    isError: false,
  });
  const queryReadState =
    retainedReadState === "complete"
      ? "complete"
      : getExactAggregationReadState(result, { isError });
  const queryReadMessage = isError
    ? QUERY_FAILED_RETRY_MESSAGE
    : queryReadState === "complete"
      ? null
      : AGGREGATION_PREPARING_MESSAGE;

  const evalsChartData = useMemo(() => {
    const baseChart = {
      id: `chart-${evaluation?.id}`,
      label: evaluation?.name,
      unit: "%",
      yAxisLabel: `${evaluation?.name} in (%)`,
      isEvaluationChart: true,
    };

    if (!Array.isArray(result) || queryReadState !== "complete") {
      return { ...baseChart, series: [] };
    }

    return {
      ...baseChart,
      series: result.map((seriesObj) => ({
        name: seriesObj?.name,
        data: (seriesObj?.data ?? []).map((item) => ({
          x: normalizeTimestamp(item.timestamp),
          y: item?.value,
        })),
      })),
    };
  }, [evaluation?.id, evaluation?.name, queryReadState, result]);

  if (isLoading) {
    return <Skeleton variant="rectangular" width="100%" height={250} />;
  }

  return (
    <Box>
      {queryReadMessage && (
        <Box
          role={isError ? "alert" : "status"}
          sx={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            mb: 1,
          }}
        >
          <Typography
            variant="caption"
            color={isError ? "warning.main" : "text.secondary"}
          >
            {queryReadMessage}
          </Typography>
          {isError && (
            <Button size="small" onClick={() => refetch()}>
              Retry
            </Button>
          )}
        </Box>
      )}
      <ChartsGenerator {...evalsChartData} onZoom={handleZoomChange} />
    </Box>
  );
}

ChartWithFetch.propTypes = {
  evaluation: PropTypes.object,
  observeId: PropTypes.string,
  inView: PropTypes.bool,
};
