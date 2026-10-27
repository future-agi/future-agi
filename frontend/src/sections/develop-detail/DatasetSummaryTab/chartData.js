import { cleanChoiceLabel } from "../DataTab/common";

export const getChartData = (values, applySort = false) => {
  const graphLabels = [];
  const graphData = [];
  values?.forEach((item) => {
    // Replace null values with 0
    let finalOutput = Object.fromEntries(
      Object.entries(item?.output || {}).map(([key, value]) => [
        key,
        value == null ? 0 : value,
      ]),
    );
    let finalValue = Object.values(finalOutput);
    let finalLabels = Object.keys(finalOutput).map(cleanChoiceLabel);

    if (applySort) {
      const sortedObject = Object.fromEntries(
        Object.entries(item?.output)
          .sort(([a], [b]) => Number(a) - Number(b))
          .map(([k, v]) => [`_${k}`, v]), //convert keys to string to avoid numeric sorting issues when decimals are involved
      );
      finalOutput = sortedObject;

      finalValue = Object.values(sortedObject);
      finalLabels = Object.keys(sortedObject).map((k) =>
        cleanChoiceLabel(k.slice(1)),
      ); //remove the leading underscore
    }

    graphData.push({
      ...item,
      name: item.name || "",
      id: item.id,
      value: finalValue,
      output: finalOutput, // to retain original output structure
    });

    if (graphLabels.length === 0) {
      graphLabels.push(...finalLabels);
    }
  });
  return { graphLabels, graphData };
};

export const getPassFailChartData = (values) => {
  const graphLabels = [];
  const graphData = [];

  values?.forEach((item) => {
    graphLabels.push(item.name);
    const newData = {
      ...item,
      name: item.name || "",
      id: item.id,
      value: [item.output.pass ?? 0, item.output.fail ?? 0],
    };
    graphData.push(newData);
  });
  return { graphLabels, graphData };
};

export const generateCompareEvalData = (data) => {
  const mergedMap = {};
  const avgMap = {};

  Object.entries(data)?.forEach(([datasetId, items], datasetIndex) => {
    items?.forEach((item) => {
      const { id, result, totalChoicesAvg, totalPassRate, totalAvg } = item;

      const average = totalPassRate ?? totalAvg ?? null;

      if (!mergedMap[id]) {
        avgMap[id] = totalChoicesAvg ? null : average !== null ? [average] : [];

        mergedMap[id] = {
          ...item,
          result: result?.map((r) => ({
            datasetId,
            datasetIndex,
            ...r,
          })),
        };
      } else {
        if (average !== null && !totalChoicesAvg) {
          avgMap[id]?.push(average);
        }

        mergedMap[id].result.push(
          ...result.map((r) => ({
            datasetId,
            datasetIndex,
            ...r,
          })),
        );
      }
    });
  });

  return Object.values(mergedMap).map((item) => {
    let averageValue = item.totalChoicesAvg;
    if (!item.totalChoicesAvg) {
      const arr = avgMap[item.id];
      averageValue = arr.length
        ? arr.reduce((sum, v) => sum + v, 0) / arr.length
        : null;
    }

    return {
      ...item,
      [item.outputType === "choices"
        ? "totalChoicesAvg"
        : item.outputType === "Pass/Fail"
          ? "totalPassRate"
          : "totalAvg"]: averageValue,
    };
  });
};

export const generateComparePromptData = (data, selectedDatasets = []) => {
  const headerData = [];
  const graphData = [];
  Object.entries(data)?.forEach(([datasetId, items], datasetIndex) => {
    const { avgTokens, avgCost, avgTime, prompts } = items;
    headerData.push({
      avgTokens,
      avgCost,
      avgTime,
      datasetIndex,
      datasetId,
      datasetName: selectedDatasets[datasetIndex],
    });
    prompts?.forEach((temp) => {
      graphData.push({
        datasetIndex: datasetIndex,
        datasetId: datasetId,
        name: temp.name,
        value: [
          temp.input_token ?? temp.inputToken,
          temp.output_token ?? temp.outputToken,
          temp.total_token ?? temp.totalToken,
        ],
      });
    });
  });
  return { headerData, graphData };
};
