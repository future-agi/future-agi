import React, { useMemo } from "react";
import PropTypes from "prop-types";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useParams } from "react-router";
import { Box, Typography } from "@mui/material";
import { ShowComponent } from "src/components/show";
import EmptyCard from "./EmptyCard";
import { useRunPromptStoreShallow } from "../states";
import SvgColor from "src/components/svg-color";
import ColumnBarChart from "./ChartsContainer/ColumnBarChart";
import { getUniqueColorPalette } from "src/utils/utils";
import PromptCardLoading from "./Loaders/PromptCardLoading";

const headerData = [
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

const PromptCard = ({
  setCurrentTab,
  datasetId = null,
  selectedColumns = [],
  datasetIndex,
}) => {
  const { dataset } = useParams();
  const setOpenRunPrompt = useRunPromptStoreShallow((s) => s.setOpenRunPrompt);
  // const data = newGraphData.result;

  const activeDatasetId = datasetId || dataset;

  const { data, isPending, isLoading } = useQuery({
    queryKey: ["prompt-summary", activeDatasetId, selectedColumns],
    queryFn: () =>
      axios.get(endpoints.dataset.promptSummary(activeDatasetId), {
        ...(selectedColumns?.length > 0 && {
          params: { prompt_ids: selectedColumns.join(",") },
        }),
      }),
    select: (e) => e?.data?.result,
    enabled: Boolean(activeDatasetId),
  });

  const graphData = useMemo(() => {
    return data?.prompts?.map((item) => ({
      name: item.name,
      value: [
        item.input_token ?? item.inputToken,
        item.output_token ?? item.outputToken,
        item.total_token ?? item.totalToken,
      ],
    }));
  }, [data]);

  if (isPending || isLoading) {
    return <PromptCardLoading />;
  }

  return (
    <Box display={"flex"} gap={2} flexDirection={"column"} height="97%">
      <ShowComponent condition={data?.prompts?.length > 0}>
        <Box
          sx={{ display: "flex", gap: "16px", justifyContent: "space-between" }}
        >
          {headerData.map((item) => {
            return (
              <Box
                key={item.id}
                sx={{
                  flex: 1,
                  display: "flex",
                  gap: "12px",
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "4px",
                  padding: "12px",
                }}
              >
                <Box
                  sx={{
                    display: "flex",
                    justifyContent: "center",
                    alignItems: "center",
                    width: "70px",
                    height: "70px",
                    backgroundColor: item.backgroundColor,
                    padding: "12px",
                    borderRadius: "8px",
                  }}
                >
                  <SvgColor
                    src={item.icon}
                    sx={{
                      backgroundColor: item.color,
                      width: "32px",
                      height: "32px",
                    }}
                  />
                </Box>
                <Box display={"flex"} flexDirection={"column"} gap={"2px"}>
                  <Typography typography={"s2"} fontWeight={"fontWeightMedium"}>
                    {item.title}
                  </Typography>
                  <Typography
                    typography={"l1"}
                    fontWeight={"fontWeightSemiBold"}
                  >
                    {data?.[item?.valueKey]}
                    {item.unit}
                  </Typography>
                </Box>
              </Box>
            );
          })}
        </Box>
        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "8px",
            padding: "12px",
          }}
        >
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
              formatter: function (seriesName) {
                let showLabel = "";
                if (datasetIndex != null && datasetIndex != undefined) {
                  const { tagBackground, tagForeground } =
                    getUniqueColorPalette(datasetIndex);
                  showLabel += `<span style="margin-left: 8px;color:${tagForeground}; background-color:${tagBackground}; width:24px; height:24px; border-radius: 5px; font-size: 12px; font-weight:500; padding: 0px 4px; display: flex; justify-content: center; align-items: center;">${String.fromCharCode(65 + (datasetIndex || 0))}</span>`;
                }
                return `<span style="font-size: 16px;display:flex;gap:8px;align-items: center;">${showLabel} <span>${seriesName}</span></span>`; // 👈 custom format here
              },
            }}
            colors={["pink", "orange", "blue", "purple", "green", "red"]}
          />
        </Box>
      </ShowComponent>
      <ShowComponent condition={!data || data?.prompts?.length === 0}>
        <EmptyCard
          tab={"prompts"}
          setCurrentTab={setCurrentTab}
          action={() => setOpenRunPrompt(true)}
          datasetId={datasetId}
          icon="/assets/icons/summary/empty-prompt.svg"
          title="No prompts added"
          description="To view results, add prompts to your dataset"
        />
      </ShowComponent>
    </Box>
  );
};

export default PromptCard;

PromptCard.propTypes = {
  setCurrentTab: PropTypes.func,
  datasetId: PropTypes.string,
  selectedColumns: PropTypes.array,
  datasetIndex: PropTypes.number,
};
