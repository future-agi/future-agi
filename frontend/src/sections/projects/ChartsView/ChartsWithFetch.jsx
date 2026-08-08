import { useQuery } from "@tanstack/react-query";
import PropTypes from "prop-types";
import React, { useMemo } from "react";
import ChartsGenerator from "./ChartsGenerator";
import axios, { endpoints } from "src/utils/axios";
import { transformEvaluationPayload } from "./common";
import { Box, Skeleton, Typography } from "@mui/material";
import { useChartsViewContext } from "./ChartsViewProvider/ChartsViewContext";
import { getStorage } from "src/hooks/use-local-storage";
import { normalizeTimestamp } from "./ChartsViewProvider/common";
import {
  AGGREGATION_PREPARING_MESSAGE,
  getExactAggregationReadState,
} from "src/utils/queryReadState";

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

  const { data, isLoading, isError } = useQuery({
    queryKey,
    queryFn: () => {
      const payload = {
        project_id: observeId,
        property: "average",
        interval: selectedInterval?.toLowerCase(),
        filters: JSON.stringify(filters),
        ...transformEvaluationPayload(evaluation),
      };

      return axios.get(endpoints.project.getEvalGraph, {
        params: { ...payload },
      });
    },
    refetchInterval: autoRefresh && inView ? 10000 : false,
    staleTime: Infinity,
    refetchIntervalInBackground: false,
    enabled: inView,
  });

  const result = data?.data?.result;
  const queryReadState = getExactAggregationReadState(result, { isError });
  const queryReadMessage =
    queryReadState === "complete" ? null : AGGREGATION_PREPARING_MESSAGE;

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
        <Typography
          role="status"
          variant="caption"
          color="text.secondary"
          sx={{ display: "block", mb: 1 }}
        >
          {queryReadMessage}
        </Typography>
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
