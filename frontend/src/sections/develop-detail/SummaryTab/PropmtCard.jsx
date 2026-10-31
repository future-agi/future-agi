import React, { useMemo } from "react";
import { Box, useTheme } from "@mui/material";
import { PropmtTitle } from "./SummaryStyle";
import ReactApexChart from "react-apexcharts";
import PropTypes from "prop-types";
import { useQuery } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { useParams } from "react-router";
import PropmtCardSkeleton from "../Common/Skeleton/PropmtCardSkeleton";
import EmptyCard from "./EmptyCard";
import {
  useRunEvaluationStoreShallow,
  useRunPromptStoreShallow,
} from "../states";

const PropmtCard = ({ setCurrentTab, datasetId = null }) => {
  const { dataset } = useParams();
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const setOpenRunPrompt = useRunPromptStoreShallow((s) => s.setOpenRunPrompt);
  const setOpenRunEvaluation = useRunEvaluationStoreShallow(
    (s) => s.setOpenRunEvaluation,
  );

  // Fetch data using useQuery
  const { data, isLoading } = useQuery({
    queryKey: ["prompt-summary", datasetId ? datasetId : dataset],
    queryFn: () =>
      axios.get(
        endpoints.dataset.promptSummary(datasetId ? datasetId : dataset),
      ),
    select: (e) => e?.data?.result,
  });

  const chartData = useMemo(() => {
    if (!data || !data.runPromptStats) {
      return {
        avgPromptToken: [],
        avgCompletionToken: [],
        avgTotalToken: [],
      };
    }

    const promptMapped = {
      avgPromptToken: [],
      avgCompletionToken: [],
      avgTotalToken: [],
    };

    data.runPromptStats.forEach((i) => {
      promptMapped.avgPromptToken.push({
        name: i.promptName,
        value: i.tokenUsage.avgPromptTokens,
      });
      promptMapped.avgCompletionToken.push({
        name: i.promptName,
        value: i.tokenUsage.avgCompletionTokens,
      });
      promptMapped.avgTotalToken.push({
        name: i.promptName,
        value: i.tokenUsage.avgTotalTokens,
      });
    });

    return promptMapped;
  }, [data]);

  // Function to generate chart options
  const generateChartOptions = (categories, color) => ({
    chart: {
      type: "bar",
      background: "transparent",
      // eslint-disable-next-line no-restricted-syntax -- ds: data colour
      foreColor: isDark ? "#a1a1aa" : undefined,
      toolbar: { show: false },
    },
    theme: {
      mode: isDark ? "dark" : "light",
    },
    xaxis: {
      categories: categories.map((item) => item.name),
    },
    title: { text: undefined },
    tooltip: {
      theme: isDark ? "dark" : "light",
      enabled: true,
    },
    dataLabels: { enabled: false },
    colors: [color],
    plotOptions: {
      bar: {
        borderRadius: 4,
        columnWidth: "42px",
      },
    },
    grid: {
      // eslint-disable-next-line no-restricted-syntax -- ds: data colour
      borderColor: isDark ? "#27272a" : undefined,
    },
  });

  // Chart options for each chart type
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  const optionsOne = generateChartOptions(chartData.avgPromptToken, "#9585D5");
  const optionsTwo = generateChartOptions(
    chartData.avgCompletionToken,
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    "#E6CAF5",
  );
  // eslint-disable-next-line no-restricted-syntax -- ds: data colour
  const optionsThree = generateChartOptions(chartData.avgTotalToken, "#BF75E5");

  if (isLoading) {
    return (
      <>
        {Array.from({ length: 3 }).map((_, index) => (
          <PropmtCardSkeleton key={index} />
        ))}
      </>
    );
  }

  return (
    <div style={{ height: "75vh", overflowX: "hidden", overflowY: "auto" }}>
      {chartData.avgPromptToken?.length === 0 &&
      chartData.avgCompletionToken?.length === 0 &&
      chartData.avgTotalToken?.length === 0 ? (
        <EmptyCard
          dataSet={dataset}
          tab={"Prompts"}
          setCurrentTab={setCurrentTab}
          onRunEvaluation={() => setOpenRunEvaluation(true)}
          onRunPrompt={() => setOpenRunPrompt(true)}
          datasetId={datasetId}
        />
      ) : (
        <div>
          <Box
            sx={{
              p: "24px",
              borderRadius: "12px",
              border: "1px solid var(--border-default)",
              mb: "29px",
            }}
          >
            <PropmtTitle variant="h4">Average Prompt Token</PropmtTitle>
            <ReactApexChart
              options={optionsOne}
              series={[
                { data: chartData.avgPromptToken.map((item) => item.value) },
              ]}
              type="bar"
              height={350}
            />
          </Box>
          <Box
            sx={{
              p: "24px",
              borderRadius: "12px",
              border: "1px solid var(--border-default)",
              mb: "29px",
            }}
          >
            <PropmtTitle variant="h4">Average Completion Token</PropmtTitle>
            <ReactApexChart
              options={optionsTwo}
              series={[
                {
                  data: chartData.avgCompletionToken.map((item) => item.value),
                },
              ]}
              type="bar"
              height={350}
            />
          </Box>
          <Box
            sx={{
              p: "24px",
              borderRadius: "12px",
              border: "1px solid var(--border-default)",
              mb: "29px",
            }}
          >
            <PropmtTitle variant="h4">Average Total Token</PropmtTitle>
            <ReactApexChart
              options={optionsThree}
              series={[
                { data: chartData.avgTotalToken.map((item) => item.value) },
              ]}
              type="bar"
              height={350}
            />
          </Box>
        </div>
      )}
    </div>
  );
};

PropmtCard.propTypes = {
  datasetId: PropTypes.string,
  graphData: PropTypes.array,
  setCurrentTab: PropTypes.func.isRequired,
};

export default PropmtCard;
