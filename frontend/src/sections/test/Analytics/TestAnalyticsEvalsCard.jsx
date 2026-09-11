import PropTypes from "prop-types";
import React, { lazy, Suspense, useMemo } from "react";
import { Box, Typography } from "@mui/material";
import { ShowComponent } from "src/components/show";
import Iconify from "src/components/iconify";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import {
  generateCompareEvalData,
  getChartData,
  getPassFailChartData,
} from "src/sections/develop-detail/DatasetSummaryTab/chartData";
import CompareDatasetSummaryIcon from "src/sections/develop-detail/DatasetSummaryTab/CompareDatasetSummaryIcon";
import { useParams } from "react-router";
import { useSelectedExecutionsStore } from "../states";
import EvalsCardLoading from "src/sections/develop-detail/DatasetSummaryTab/Loaders/EvalsCardLoading";
import { OutputTypes } from "../../common/DevelopCellRenderer/CellRenderers/cellRendererHelper";

const RedarChart = lazy(
  () =>
    import(
      "src/sections/develop-detail/DatasetSummaryTab/ChartsContainer/RedarChart"
    ),
);
const ColumnBarChart = lazy(
  () =>
    import(
      "src/sections/develop-detail/DatasetSummaryTab/ChartsContainer/ColumnBarChart"
    ),
);
const DonutChart = lazy(
  () =>
    import(
      "src/sections/develop-detail/DatasetSummaryTab/ChartsContainer/DonutChart"
    ),
);
const StackBarChart = lazy(
  () =>
    import(
      "src/sections/develop-detail/DatasetSummaryTab/ChartsContainer/StackBarChart"
    ),
);
const AreaChartWrapper = lazy(
  () =>
    import(
      "src/sections/develop-detail/DatasetSummaryTab/ChartsContainer/AreaChartWrapper"
    ),
);

const TestAnalyticsEvalsCard = (props) => {
  const { selectedIndex } = props;

  const isCompare = selectedIndex === null;

  const { selectedExecutions } = useSelectedExecutionsStore();

  const { testId } = useParams();

  const executionIds = useMemo(
    () => selectedExecutions.map((e) => e.id),
    [selectedExecutions],
  );

  const { data, isPending } = useQuery({
    queryKey: ["test-compare-summary", testId, executionIds],
    queryFn: () => {
      return axios.get(endpoints.testExecutions.compareSummary(testId), {
        params: {
          execution_ids: JSON.stringify(executionIds),
        },
      });
    },
    select: (data) => data?.data?.result || {},
    staleTime: 1000,
    refetchOnMount: true,
    enabled: executionIds?.length > 0,
  });

  const graphData = useMemo(() => {
    if (!isPending) {
      if (isCompare) {
        return generateCompareEvalData(data || {});
      } else {
        return data?.[selectedExecutions[0].id] || [];
      }
    }
  }, [data, isCompare, isPending]);

  const redarChart = useMemo(() => {
    const obj = {};
    if (isCompare) {
      Object.entries(data || {}).map(([id, item], index) => {
        item.forEach((temp) => {
          if (temp.output_type === "score") {
            if (!obj[temp.name]) {
              obj[temp.name] = [
                {
                  datasetId: id,
                  name: temp.name,
                  datasetIndex: index,
                  average: temp.total_avg || 0,
                },
              ];
            } else {
              obj[temp.name].push({
                datasetId: id,
                name: temp.name,
                datasetIndex: index,
                average: temp.total_avg || 0,
              });
            }
          }
          if (temp.output_type === "Pass/Fail") {
            if (!obj[temp.name]) {
              obj[temp.name] = [
                {
                  datasetId: id,
                  name: temp.name,
                  datasetIndex: index,
                  average: temp.total_pass_rate || 0,
                },
              ];
            } else {
              obj[temp.name].push({
                datasetId: id,
                name: temp.name,
                datasetIndex: index,
                average: temp.total_pass_rate || 0,
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
        if (item.output_type === "score") {
          redarData.push(item.total_avg);
          redarLabel.push(item.name);
        }
        if (item.output_type === "Pass/Fail") {
          redarLabel.push(item.name);
          redarData.push(item.total_pass_rate);
        }
      });

      return { label: redarLabel, data: [{ value: redarData }] };
    }

    return obj;
  }, [graphData, isCompare, data]);

  const compareRadarProps = useMemo(() => {
    if (!isCompare || !redarChart || Object.keys(redarChart).length === 0)
      return null;
    const evalsData = Object.values(redarChart);
    const label = evalsData.map((temp) => temp?.[0]?.name || "");
    const result = evalsData[0].map((_, i) =>
      evalsData.map((o) => o[i]?.average),
    );
    return {
      data: result.map((item, ind) => ({
        name: `dataset_${ind}`,
        value: item,
      })),
      graphLabels: label,
    };
  }, [redarChart, isCompare]);

  if (isPending) {
    return (
      <Box
        className="ag-theme-quartz"
        sx={{
          flex: 1,
        }}
      >
        <EvalsCardLoading isCompare />
      </Box>
    );
  }

  return (
    <Box
      className="ag-theme-quartz"
      sx={{
        flex: 1,
        padding: "12px",
      }}
    >
      <Suspense fallback={<EvalsCardLoading isCompare={isCompare} />}>
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
                    {compareRadarProps && (
                      <RedarChart
                        data={compareRadarProps.data}
                        graphLabels={compareRadarProps.graphLabels}
                      />
                    )}
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
                            {item?.map((temp, index) => {
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
                            sx={{
                              display: "flex",
                              alignItems: "center",
                              gap: 2,
                            }}
                          >
                            <Box
                              sx={{
                                backgroundColor: "green.o10",
                                padding: 1,
                                color: "green.500",
                                display: "flex",
                                justifyContent: "center",
                                alignItems: "center",
                                width: "32px",
                                height: "32px",
                                borderRadius: "2px",
                              }}
                            >
                              <Iconify
                                // @ts-ignore
                                icon="qlementine-icons:success-16"
                                color="green.500"
                              />
                            </Box>
                            <Box>
                              <Typography
                                typography="s3"
                                fontWeight={"fontWeightMedium"}
                                sx={{ color: "text.disabled", fontWeight: 500 }}
                              >
                                {item}
                              </Typography>
                              <Typography
                                typography="s1"
                                fontWeight={"fontWeightSemiBold"}
                                sx={{
                                  color: "text.primary",
                                  fontWeight: 700,
                                  fontSize: "1.125rem",
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
                    item.output_type == "choices"
                      ? item.total_choices_avg
                      : item.output_type == "Pass/Fail"
                        ? item.total_pass_rate || 0
                        : item.total_avg || 0,
                  isNumericEval:
                    item.output_type == OutputTypes.NUMERIC
                      ? true
                      : item?.is_numeric_eval,
                  isNumericEvalPercentage: item?.is_numeric_eval_percentage,
                };
                const applySort = Boolean(item?.is_numeric_eval);
                const { graphLabels, graphData } =
                  item.output_type == "Pass/Fail"
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
                    <ShowComponent condition={item.output_type == "choices"}>
                      <DonutChart
                        height={300}
                        data={graphData}
                        graphLabels={graphLabels}
                        headerData={headerData}
                        datasetIndex={selectedIndex}
                      />
                    </ShowComponent>
                    <ShowComponent condition={item.output_type == "Pass/Fail"}>
                      <StackBarChart
                        height={300}
                        data={graphData}
                        graphLabels={graphLabels}
                        headerData={headerData}
                        datasetIndex={selectedIndex}
                      />
                    </ShowComponent>
                    <ShowComponent condition={item.output_type == "score"}>
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
                backgroundColor: "blue.o5",
                border: "1px solid",
                borderColor: "blue.200",
                padding: "12px",
                display: "flex",
                flexDirection: "column",
                gap: 0.5,
              }}
            >
              <Typography
                typography={"s1"}
                fontWeight={"fontWeightSemiBold"}
                color="blue.500"
              >
                There are no common columns to compare
              </Typography>
              <Typography
                typography={"s3"}
                fontWeight={"fontWeightRegular"}
                color="blue.500"
              >
                {"We've"} summarized each dataset. Please select one to view and
                compare individually.
              </Typography>
            </Box>
          </ShowComponent>
        </Box>
      </Suspense>
    </Box>
  );
};

TestAnalyticsEvalsCard.propTypes = {
  selectedIndex: PropTypes.number,
};

TestAnalyticsEvalsCard.defaultProps = {
  selectedIndex: -1,
};

export default TestAnalyticsEvalsCard;
