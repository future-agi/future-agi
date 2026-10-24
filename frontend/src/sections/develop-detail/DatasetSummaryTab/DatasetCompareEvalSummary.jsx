import PropTypes from "prop-types";
import React, { useMemo } from "react";
import {
  generateCompareEvalData,
  getChartData,
  getPassFailChartData,
} from "./chartData";
import { Box, Typography } from "@mui/material";
import { ShowComponent } from "src/components/show";
import RedarChart from "./ChartsContainer/RedarChart";
import Iconify from "src/components/iconify";
import DonutChart from "./ChartsContainer/DonutChart";
import StackBarChart from "./ChartsContainer/StackBarChart";
import AreaChartWrapper from "./ChartsContainer/AreaChartWrapper";
import CompareDatasetSummaryIcon from "./CompareDatasetSummaryIcon";
import ColumnBarChart from "./ChartsContainer/ColumnBarChart";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import EvalsCardLoading from "./Loaders/EvalsCardLoading";
import { OutputTypes } from "../../common/DevelopCellRenderer/CellRenderers/cellRendererHelper";

// selectedColumns for filter applied.
const DatasetCompareEvalSummary = (props) => {
  const {
    isCompare,
    currentDataset,
    selectedDatasets,
    selectedIndex,
    commonColumn,
    baseColumn,
  } = props;

  const { data, isPending } = useQuery({
    queryKey: ["prompt-compare-Summary", "evaluation", selectedDatasets?.[0]],
    queryFn: () => {
      return axios.post(
        endpoints.dataset.getSummaryTable(selectedDatasets?.[0] || ""),
        {
          dataset_ids: selectedDatasets?.slice(1),
          common_column_names: commonColumn,
          base_column_name: baseColumn,
          stat_type: "evaluation",
        },
      );
    },
    select: (data) => data?.data?.result || {},
    enabled: Boolean(selectedDatasets?.[0]),
    staleTime: 1000,
    refetchOnMount: true,
  });

  const graphData = useMemo(() => {
    if (!isPending) {
      if (isCompare) {
        return generateCompareEvalData(data || {});
      } else {
        return data?.[currentDataset] || [];
      }
    }
  }, [data, currentDataset, isCompare, isPending]);

  const redarChart = useMemo(() => {
    const obj = {};
    if (isCompare) {
      Object.entries(data || {}).map(([id, item], index) => {
        item.forEach((temp) => {
          if (temp.outputType === "score") {
            if (!obj[temp.name]) {
              obj[temp.name] = [
                {
                  datasetId: id,
                  name: temp.name,
                  datasetIndex: index,
                  average: temp.totalAvg || 0,
                },
              ];
            } else {
              obj[temp.name].push({
                datasetId: id,
                name: temp.name,
                datasetIndex: index,
                average: temp.totalAvg || 0,
              });
            }
          }
          if (temp.outputType === "Pass/Fail") {
            if (!obj[temp.name]) {
              obj[temp.name] = [
                {
                  datasetId: id,
                  name: temp.name,
                  datasetIndex: index,
                  average: temp.totalPassRate || 0,
                },
              ];
            } else {
              obj[temp.name].push({
                datasetId: id,
                name: temp.name,
                datasetIndex: index,
                average: temp.totalPassRate || 0,
              });
            }
          }
        });
      });
    }
    if (!isCompare) {
      const redarLabel = [];
      const redarData = [];
      graphData?.forEach((item) => {
        if (item.outputType === "score") {
          redarData.push(item.totalAvg);
          redarLabel.push(item.name);
        }
        if (item.outputType === "Pass/Fail") {
          redarLabel.push(item.name);
          redarData.push(item.totalPassRate);
        }
      });

      return { label: redarLabel, data: [{ value: redarData }] };
    }

    return obj;
  }, [graphData, isCompare, data]);

  if (isPending) {
    return <EvalsCardLoading isCompare />;
  }

  return (
    <Box display={"flex"} gap={2} flexDirection={"column"} height="97%">
      <ShowComponent condition={graphData?.length > 0}>
        <ShowComponent condition={isCompare}>
          {Object.keys(redarChart)?.length > 0 && (
            <Box display={"flex"} gap={2} flexWrap={"wrap"}>
              <Box
                sx={{
                  width: "100%",
                  display: "flex",
                  alignItems: "center",
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "8px",
                  padding: "16px",
                  height: "430px",
                }}
              >
                {(() => {
                  const label = [];

                  const evalsData = Object.values(redarChart);
                  const result = evalsData[0].map((_, i) =>
                    evalsData.map((o) => o[i]?.average),
                  );
                  evalsData.forEach((temp) => {
                    label.push(temp?.[0]?.name || "");
                  });
                  return (
                    <RedarChart
                      data={result.map((item, ind) => ({
                        name: `dataset_${ind}`,
                        value: item,
                      }))}
                      graphLabels={label}
                    />
                  );
                })()}
                <Box
                  sx={{
                    padding: 2,
                    borderRadius: "4px",
                    display: "flex",
                    flexDirection: "column",
                    gap: 2.5,
                    border: "2px solid",
                    borderColor: "action.hover",
                    height: "100%",
                    width: "400px",
                    overflowY: "auto",
                  }}
                >
                  {Object.values(redarChart).map((item, idx) => {
                    return (
                      <Box
                        key={idx}
                        sx={{
                          display: "flex",
                          flexDirection: "column",
                          gap: 0.5,
                        }}
                      >
                        <Typography
                          typography={"s1"}
                          fontWeight={"fontWeightMedium"}
                          color="text.primary"
                        >
                          {item?.[0]?.name}
                        </Typography>
                        {item.map((temp, index) => {
                          return (
                            <Box
                              key={`${idx}-${index}`}
                              display="flex"
                              justifyContent="space-between"
                            >
                              <Typography
                                typography={"s2"}
                                fontWeight={"fontWeightRegular"}
                                color="text.primary"
                                display="flex"
                                gap={1}
                              >
                                <CompareDatasetSummaryIcon
                                  index={temp.datasetIndex}
                                />
                                {temp.name}
                              </Typography>
                              <Typography
                                typography={"s2"}
                                fontWeight={"fontWeightRegular"}
                                color="text.primary"
                              >
                                {temp.average}%
                              </Typography>
                            </Box>
                          );
                        })}
                      </Box>
                    );
                  })}
                </Box>
              </Box>
            </Box>
          )}
        </ShowComponent>
        <ShowComponent condition={!isCompare}>
          {redarChart?.label?.length > 0 && (
            <Box display={"flex"} gap={2} flexWrap={"wrap"}>
              <Box
                sx={{
                  display: "flex",
                  alignItems: "center",
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "8px",
                  padding: "16px",
                  height: "430px",
                  width: "100%",
                }}
              >
                <ShowComponent
                  condition={
                    redarChart?.label?.length > 0 &&
                    redarChart?.label?.length < 3
                  }
                >
                  <Box width={500}>
                    <ColumnBarChart
                      data={redarChart.data}
                      graphLabels={redarChart.label}
                    />
                  </Box>
                </ShowComponent>
                <ShowComponent condition={redarChart?.label?.length >= 3}>
                  <RedarChart
                    data={redarChart.data}
                    graphLabels={redarChart.label}
                  />
                </ShowComponent>
                <Box
                  sx={{
                    ml: 4,
                    padding: 2,
                    borderRadius: "4px",
                    display: "flex",
                    flexDirection: "column",
                    gap: 2,
                    border: "1px solid",
                    borderColor: "action.hover",
                    height: "100%",
                    width: "250px",
                    overflowY: "auto",
                  }}
                >
                  {redarChart.data.map((temp, ind) =>
                    redarChart.label.map((item, index) => (
                      <Box
                        key={`${ind}-${index}`}
                        sx={{ display: "flex", alignItems: "center", gap: 2 }}
                      >
                        <Box
                          sx={{
                            backgroundColor: "status.success.bg",
                            padding: 1,
                            color: "status.success.fg",
                            display: "flex",
                            justifyContent: "center",
                            alignItems: "center",
                            width: "32px",
                            height: "32px",
                            borderRadius: "4px",
                          }}
                        >
                          <Iconify
                            // @ts-ignore
                            icon="qlementine-icons:success-16"
                            color="status.success.fg"
                          />
                        </Box>
                        <Box>
                          <Typography
                            typography="s3"
                            fontWeight={"fontWeightMedium"}
                            sx={{ color: "text.secondary", fontWeight: 500 }}
                          >
                            {item}
                          </Typography>
                          <Typography
                            typography="s1"
                            fontWeight={"fontWeightSemiBold"}
                            sx={{
                              color: "text.primary",
                              fontWeight: 600,
                              fontSize: "1rem",
                            }}
                          >
                            {temp?.value?.[index] || 0} %
                          </Typography>
                        </Box>
                      </Box>
                    )),
                  )}
                </Box>
              </Box>
            </Box>
          )}
        </ShowComponent>

        <Box display={"flex"} gap={2} flexWrap={"wrap"}>
          {graphData?.map((item) => {
            if (item?.result.length === 0) return <></>;
            const headerData = {
              name: item.name,
              average:
                item.outputType == "choices"
                  ? item.totalChoicesAvg
                  : item.outputType == "Pass/Fail"
                    ? item.totalPassRate || 0
                    : item.totalAvg || 0,
              isNumericEval:
                item.outputType == OutputTypes.NUMERIC
                  ? true
                  : item?.isNumericEval,
              isNumericEvalPercentage: item?.isNumericEvalPercentage,
            };
            const applySort = Boolean(item?.isNumericEval);
            const { graphLabels, graphData } =
              item.outputType == "Pass/Fail"
                ? getPassFailChartData(item?.result)
                : getChartData(item?.result, applySort);
            return (
              <Box
                key={item.id}
                sx={{
                  width: "calc(50% - 8px)",
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "8px",
                  padding: "16px",
                }}
              >
                <ShowComponent condition={item.outputType == "choices"}>
                  <DonutChart
                    height={300}
                    data={graphData}
                    graphLabels={graphLabels}
                    headerData={headerData}
                    datasetIndex={selectedIndex}
                  />
                </ShowComponent>
                <ShowComponent condition={item.outputType == "Pass/Fail"}>
                  <StackBarChart
                    height={300}
                    data={graphData}
                    graphLabels={graphLabels}
                    headerData={headerData}
                    datasetIndex={selectedIndex}
                  />
                </ShowComponent>
                <ShowComponent condition={item.outputType == "score"}>
                  <AreaChartWrapper
                    data={graphData}
                    graphLabels={graphLabels}
                    headerData={headerData}
                    datasetIndex={selectedIndex}
                  />
                </ShowComponent>
              </Box>
            );
          })}
        </Box>
      </ShowComponent>
      <ShowComponent condition={!graphData || graphData?.length === 0}>
        <Box
          sx={{
            marginTop: "16px",
            borderRadius: "4px",
            backgroundColor: "status.info.bg",
            border: "1px solid",
            borderColor: "status.info.border",
            padding: "12px",
            display: "flex",
            flexDirection: "column",
            gap: 0.5,
          }}
        >
          <Typography
            typography={"s1"}
            fontWeight={"fontWeightSemiBold"}
            color="status.info.fg"
          >
            There are no common columns to compare
          </Typography>
          <Typography
            typography={"s3"}
            fontWeight={"fontWeightRegular"}
            color="status.info.fg"
          >
            {"We've"} summarized each dataset. Please select one to view and
            compare individually.
          </Typography>
        </Box>
      </ShowComponent>
    </Box>
  );
};

export default DatasetCompareEvalSummary;

DatasetCompareEvalSummary.propTypes = {
  selectedColumns: PropTypes.array,
  isCompare: PropTypes.bool,
  currentDataset: PropTypes.string,
  selectedDatasets: PropTypes.array,
  selectedIndex: PropTypes.number,
  commonColumn: PropTypes.array,
  baseColumn: PropTypes.string,
  selectedDatasetData: PropTypes.array,
};
