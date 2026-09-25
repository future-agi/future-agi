const outputTypeKeyMap = {
  score: "float",
  choices: "str_list",
  "Pass/Fail": "bool",
};

export function transformEvaluationPayload(evaluation) {
  if (!evaluation?.id) return {};

  const outputType = outputTypeKeyMap[evaluation?.outputType] ?? "";
  const basePayload = {
    id: evaluation.id,
    output_type: outputType,
    type: "EVAL",
  };

  return {
    req_data_config: JSON.stringify(basePayload),
  };
}

const baseKeys = ["pink", "blue", "orange", "green", "purple"];
const shadeRange = [400, 500, 600, 700, 800, 900, 1000];

export const generateAllColors = (paletteFn, newBaseKey = baseKeys) => {
  const colors = [];
  for (const shade of shadeRange) {
    for (const key of newBaseKey) {
      const color = paletteFn("light")[key][shade];
      if (color) colors.push(color);
    }
  }
  return colors; // will return exactly 35
};

/**
 * Labels of the project latency chart. The server declares each series'
 * statistic in `system_metric_statistics`; latency is the median. Without
 * that declaration (an older server) the chart keeps the plain label rather
 * than guessing a statistic.
 */
export const latencyChartLabels = (systemMetricStatistics) =>
  systemMetricStatistics?.latency === "median"
    ? {
        label: "Latency (median)",
        seriesName: "Latency (median)",
        yAxisLabel: "Median latency (ms)",
      }
    : {
        label: "Latency",
        seriesName: "Latency",
        yAxisLabel: "Latency in (ms)",
      };
