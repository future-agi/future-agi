import { Box, Divider, Typography } from "@mui/material";
import PropTypes from "prop-types";
import React, { useMemo } from "react";
import ColumnBarChart from "./ChartsContainer/ColumnBarChart";
import { generateComparePromptData } from "./chartData";
import SvgColor from "src/components/svg-color";
import { getUniqueColorPalette } from "src/utils/utils";
import CompareDatasetSummaryIcon from "./CompareDatasetSummaryIcon";
import axios, { endpoints } from "src/utils/axios";
import { useQuery } from "@tanstack/react-query";
import PromptCardLoading from "./Loaders/PromptCardLoading";
import { ShowComponent } from "src/components/show";

const headerOptions = [
  {
    id: "1",
    icon: "/assets/icons/summary/database.svg",
    title: "Avg, token size",
    value: 0,
    valueKey: "avgTokens",
    unit: "",
    backgroundColor: "status.warning.bg",
    color: "status.warning.fg",
  },
  {
    id: "2",
    icon: "/assets/icons/summary/timmer.svg",
    title: "Avg. Response time",
    value: 0,
    valueKey: "avgTime",
    unit: "ms",
    backgroundColor: "status.info.bg",
    color: "status.info.fg",
  },
  {
    id: "3",
    icon: "/assets/icons/summary/dollar.svg",
    title: "Average cost",
    value: 0,
    valueKey: "avgCost",
    unit: "$",
    backgroundColor: "status.success.bg",
    color: "status.success.fg",
  },
];

const DatasetComparePromptSummary = (props) => {
  const {
    isCompare,
    currentDataset,
    selectedDatasets,
    selectedIndex,
    commonColumn,
    baseColumn,
    selectedDatasetData,
  } = props;

  const { data, isPending } = useQuery({
    queryKey: ["prompt-compare-Summary", "run_prompt", selectedDatasets?.[0]],
    queryFn: () => {
      return axios.post(
        endpoints.dataset.getSummaryTable(selectedDatasets?.[0] || ""),
        {
          dataset_ids: selectedDatasets?.slice(1),
          common_column_names: commonColumn,
          base_column_name: baseColumn,
          stat_type: "run_prompt",
        },
      );
    },
    select: (data) => data?.data?.result || {},
    enabled: Boolean(selectedDatasets?.[0]),
    refetchOnMount: true,
    staleTime: 1000,
  });

  const { graphData, headerData } = useMemo(() => {
    if (!data) {
      return { graphData: [], headerData: [] };
    }
    if (!isPending) {
      if (isCompare) {
        const { headerData, graphData } = generateComparePromptData(
          data || {},
          selectedDatasetData,
        );
        return { headerData, graphData };
      }
    }
    const graphData =
      data?.[currentDataset]?.prompts?.map((item) => ({
        name: item.name,
        datasetIndex: selectedIndex,
        value: [
          item.input_token ?? item.inputToken,
          item.output_token ?? item.outputToken,
          item.total_token ?? item.totalToken,
        ],
      })) || [];
    const headerData = [
      {
        datasetName: selectedDatasetData[selectedIndex],
        avgTokens: data?.[currentDataset]?.avgTokens,
        avgCost: data?.[currentDataset]?.avgCost,
        avgTime: data?.[currentDataset]?.avgTime,
      },
    ];

    return { graphData, headerData };
  }, [
    data,
    isCompare,
    currentDataset,
    selectedDatasetData,
    selectedIndex,
    isPending,
  ]);

  if (isPending) {
    return <PromptCardLoading isCompare />;
  }
  return (
    <Box display={"flex"} gap={2} flexDirection={"column"} height="97%">
      <ShowComponent condition={graphData?.length > 0}>
        <Box display="flex" gap={2} justifyContent="space-between">
          {headerOptions.map((item) => {
            return (
              <Box
                key={item.id}
                sx={{
                  flex: 1,
                  display: "flex",
                  gap: "12px",
                  flexDirection: "column",
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "4px",
                  padding: "12px",
                }}
              >
                <Box
                  sx={{ display: "flex", gap: "12px", alignItems: "center" }}
                >
                  <Box
                    sx={{
                      display: "flex",
                      justifyContent: "center",
                      alignItems: "center",
                      width: "40px",
                      height: "40px",
                      backgroundColor: item.backgroundColor,
                      padding: "12px",
                      borderRadius: "8px",
                    }}
                  >
                    <SvgColor
                      // @ts-ignore
                      src={item.icon}
                      sx={{
                        backgroundColor: item.color,
                        width: "24px",
                        height: "24px",
                      }}
                    />
                  </Box>
                  <Box display={"flex"} flexDirection={"column"} gap={"2px"}>
                    <Typography
                      typography={"m3"}
                      fontWeight={"fontWeightMedium"}
                    >
                      {item.title}
                    </Typography>
                  </Box>
                </Box>
                <Divider orientation="horizontal" />
                {headerData.map((temp, index) => {
                  const currentIndex =
                    selectedIndex || selectedIndex == 0
                      ? selectedIndex
                      : temp.datasetIndex;
                  return (
                    <Box
                      key={index}
                      display="flex"
                      justifyContent={"space-between"}
                    >
                      <Typography
                        typography={"s1"}
                        fontWeight={"fontWeightRegular"}
                        display="flex"
                        gap={1}
                      >
                        <CompareDatasetSummaryIcon index={currentIndex} />
                        {temp.datasetName}
                      </Typography>
                      <Typography
                        typography={"s1"}
                        fontWeight={"fontWeightSemiBold"}
                      >
                        {temp[item.valueKey]}
                        {item.unit}
                      </Typography>
                    </Box>
                  );
                })}
              </Box>
            );
          })}
        </Box>
        <Box>
          <ColumnBarChart
            data={graphData}
            graphLabels={["Input", "Output", "Total"]}
            showCustomLegend={false}
            title={{ text: "Avg. token" }}
            legend={{
              show: true,
              onItemClick: {
                toggleDataSeries: false,
              },
              formatter: function (seriesName, opts) {
                let showLabel = "";
                const datasetIndex =
                  selectedIndex || selectedIndex == 0
                    ? selectedIndex
                    : graphData[opts.seriesIndex]?.datasetIndex;
                if (datasetIndex != null && datasetIndex != undefined) {
                  const { tagBackground, tagForeground } =
                    getUniqueColorPalette(datasetIndex);
                  showLabel += `<span style="margin-left: 8px;color:${tagForeground}; background-color:${tagBackground}; width:24px; height:24px; border-radius: 5px; font-size: 12px; font-weight:500; padding: 0px 4px; display: flex; justify-content: center; align-items: center;">${String.fromCharCode(65 + (datasetIndex || 0))}</span>`;
                }
                return `<span style="font-size: 16px;display:flex;gap:8px;align-items: center;">${showLabel} <span>${seriesName}</span></span>`;
              },
            }}
            colors={["pink", "orange", "blue", "purple", "green", "red"]}
          />
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

export default DatasetComparePromptSummary;

DatasetComparePromptSummary.propTypes = {
  selectedColumns: PropTypes.array,
  isCompare: PropTypes.bool,
  currentDataset: PropTypes.string,
  selectedDatasets: PropTypes.array,
  selectedIndex: PropTypes.number,
  commonColumn: PropTypes.array,
  baseColumn: PropTypes.string,
  selectedDatasetData: PropTypes.array,
};
