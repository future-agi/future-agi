import React, { useMemo, useState } from "react";
import { Box, Grid, useTheme } from "@mui/material";
import { AgGridReact } from "ag-grid-react";
import { useAgThemeWith } from "src/hooks/use-ag-theme";
import { AG_THEME_OVERRIDES } from "src/theme/ag-theme";
import {
  evaluationDefaultColDef,
  metricDefaultColDef,
  metricTabColumnDefs,
} from "./tableConfig";
import {
  CustomLabel,
  CustomTabButton,
  CustomText,
  HeadingH6,
  SubHeading,
} from "./SummaryStyle";
import ReactApexChart from "react-apexcharts";
import PropTypes from "prop-types";
import { useParams } from "react-router";
import axiosInstance, { endpoints } from "src/utils/axios";
import { useQuery } from "@tanstack/react-query";
import { interpolateColorBasedOnScore } from "src/utils/utils";
import EvalsCardSkeleton from "../Common/Skeleton/EvalsCardSkeleton";
import EmptyCard from "./EmptyCard";
import {
  useRunEvaluationStoreShallow,
  useRunPromptStoreShallow,
} from "../states";
import logger from "src/utils/logger";

const EvalCard = ({ setCurrentTab, datasetId = null }) => {
  const { dataset } = useParams();
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  const agTheme = useAgThemeWith(AG_THEME_OVERRIDES.noHeaderBorder);
  const [currentVerticalTab, setCurrentVerticalTab] = useState("");
  const [currentVerticalTabId, setCurrentVerticalTabId] = useState("");
  const setOpenRunPrompt = useRunPromptStoreShallow((s) => s.setOpenRunPrompt);
  const setOpenRunEvaluation = useRunEvaluationStoreShallow(
    (s) => s.setOpenRunEvaluation,
  );

  // Fetch data using useQuery
  const { data, isLoading } = useQuery({
    queryKey: ["evals-summary", datasetId ? datasetId : dataset],
    queryFn: () =>
      axiosInstance.get(
        endpoints.dataset.evalsSummary(datasetId ? datasetId : dataset),
      ),
    select: (e) => e?.data?.result,
  });
  const [barData, setBarData] = useState([]);

  const tableData = useMemo(() => {
    if (data?.results) {
      const tableDataMapped = {
        tableFirst: [],
        tableSecond: [],
        chartData: [],
      };

      data.results.forEach((i) => {
        const percentiles = {};
        const percentileKeys = Array.from(
          { length: 20 },
          (_, index) => `p${(index + 1) * 5}`,
        );

        // Dynamically map percentile properties
        percentileKeys.forEach((key) => {
          percentiles[key] = i[key];
        });

        tableDataMapped.tableFirst.push({
          metric: i.metricName,
          row: i.totalRows,
          score: i.averageScore,
          reverseOutput: i.reverseOutput,
        });

        tableDataMapped.tableSecond.push({
          metric: i.metricName,
          row: i.totalRows,
          score: i.averageScore,
          ...percentiles,
        });

        tableDataMapped.chartData.push({
          id: i.id,
          title: i.metricName,
          bar: percentileKeys.map((key) => ({ [key]: i[key] })),
        });
      });
      setCurrentVerticalTab(tableDataMapped.chartData[0]?.title);
      setCurrentVerticalTabId(tableDataMapped.chartData[0]?.id);
      setBarData(
        tableDataMapped.chartData[0]?.bar.map((obj) => Object.values(obj)[0]),
      );
      return tableDataMapped;
    }

    return { tableFirst: [], tableSecond: [], chartData: [] };
  }, [data]);

  const generateColumnDef = (headerName, field) => ({
    headerName,
    field,
    flex: 1,
    cellStyle: (row) => ({
      backgroundColor: interpolateColorBasedOnScore(row.value, 1),
    }),
  });

  const evaluationTabColumnDefs = [
    {
      headerName: "Evaluation",
      field: "metric",
      flex: 2.5,
      headerClass: "hasRightBorder",
      cellClass: "hasRightBorder",
    },
    {
      headerName: "Rows",
      field: "row",
      flex: 1.5,
      headerClass: "hasRightBorder",
      cellClass: "hasRightBorder",
    },
    {
      headerName: "Average",
      field: "score",
      flex: 1.5,
      cellStyle: (row) => ({
        backgroundColor: interpolateColorBasedOnScore(row.value, 100),
      }),
    },
    ...Array.from({ length: 20 }, (_, index) => `p${(index + 1) * 5}`).map(
      (percentile) =>
        generateColumnDef(`${percentile.toUpperCase()}`, percentile),
    ),
  ];

  const averageScore = useMemo(() => {
    if (tableData?.tableFirst?.length) {
      const totalScore = tableData.tableFirst.reduce(
        (sum, item) => sum + item.score,
        0,
      );
      return totalScore / tableData.tableFirst.length;
    }
    return 0;
  }, [tableData]);

  const options = {
    chart: {
      type: "bar",
      background: "transparent",
      // eslint-disable-next-line no-restricted-syntax -- ds: data colour
      foreColor: isDark ? "#a1a1aa" : undefined,
      toolbar: {
        show: false,
      },
    },
    theme: {
      mode: isDark ? "dark" : "light",
    },
    xaxis: {
      categories:
        tableData.chartData[0]?.bar.map((obj) => Object.keys(obj)[0]) || [],
      title: {},
    },
    title: {
      text: undefined,
      style: {
        fontSize: "16px",
      },
    },
    tooltip: {
      theme: isDark ? "dark" : "light",
      enabled: true,
    },
    grid: {
      // eslint-disable-next-line no-restricted-syntax -- ds: data colour
      borderColor: isDark ? "#27272a" : undefined,
    },
    dataLabels: {
      enabled: false,
    },
    // eslint-disable-next-line no-restricted-syntax -- ds: data colour
    colors: ["#A792FD"],
    plotOptions: {
      bar: {
        borderRadius: 4,
        columnWidth: "42px",
      },
    },
  };

  const onTabChange = (item) => {
    setCurrentVerticalTab(item.title);
    setCurrentVerticalTabId(item.id);
    setBarData(item.bar.map((obj) => Object.values(obj)[0]));
  };

  if (isLoading) {
    return <EvalsCardSkeleton />;
  }
  return (
    <div style={{ height: "75vh", overflowX: "hidden", overflowY: "auto" }}>
      {tableData.tableFirst?.length === 0 &&
      tableData?.tableSecond?.length === 0 &&
      tableData?.chartData?.length === 0 ? (
        <EmptyCard
          dataSet={dataset}
          tab={"Evaluations"}
          setCurrentTab={setCurrentTab}
          onRunEvaluation={() => {
            logger.debug("onRunEvaluation 2");
            setOpenRunEvaluation(true);
          }}
          onRunPrompt={() => {
            logger.debug("onRunPrompt");
            setOpenRunPrompt(true);
          }}
          datasetId={datasetId}
        />
      ) : (
        <>
          <Box
            sx={{
              overflowY: "hidden",
              width: "100%",
              marginBottom: "20px",
            }}
          >
            <AgGridReact
              theme={agTheme}
              columnDefs={metricTabColumnDefs}
              defaultColDef={metricDefaultColDef}
              pagination={false}
              domLayout="autoHeight"
              rowData={tableData.tableFirst}
              maxBlocksInCache={1}
            />
            <CustomText>
              Pass rate{" "}
              <span
                style={{
                  fontSize: "20px",
                  fontWeight: "600",
                  color: theme.palette.success.main,
                }}
              >
                {averageScore.toFixed(2)}
              </span>{" "}
              across {tableData.tableFirst.length} rows
            </CustomText>
          </Box>
          <div
            style={{ width: "100%", overflowX: "auto" }}
            className="no-scroll-x"
          >
            <Box
              sx={{
                width: "100rem",
                marginBottom: "48px",
              }}
            >
              <AgGridReact
                theme={agTheme}
                columnDefs={evaluationTabColumnDefs}
                defaultColDef={evaluationDefaultColDef}
                pagination={false}
                domLayout="autoHeight"
                paginationAutoPageSize={true}
                suppressRowClickSelection={true}
                paginationPageSizeSelector={false}
                rowData={tableData.tableSecond}
                maxBlocksInCache={1}
              />
            </Box>
          </div>

          <Grid
            container
            sx={{
              borderTop: 3,
              borderTopStyle: "solid",
              borderTopColor: "divider",
            }}
          >
            <Grid item sm={3}>
              <Box
                sx={{
                  px: "12px",
                  py: "17px",
                  borderRight: 3,
                  borderRightStyle: "solid",
                  borderRightColor: "divider",
                  height: "450px",
                  overflowY: "auto",
                }}
              >
                <CustomLabel>Configured Evals</CustomLabel>
                {tableData.chartData.map((item, idx) => (
                  <CustomTabButton
                    className={
                      item.id === currentVerticalTabId ? "active" : null
                    }
                    key={idx}
                    onClick={() => onTabChange(item)}
                  >
                    {item.title}
                  </CustomTabButton>
                ))}
              </Box>
            </Grid>
            <Grid item sm={9}>
              <Box sx={{ pl: { sm: "44px" }, pt: "24px" }}>
                <HeadingH6 variant="h6">{currentVerticalTab}</HeadingH6>
                <SubHeading>Percentile Distribution</SubHeading>
                <ReactApexChart
                  options={options}
                  series={[{ data: barData }]}
                  type="bar"
                  height={350}
                />
              </Box>
            </Grid>
          </Grid>
        </>
      )}
    </div>
  );
};

EvalCard.propTypes = {
  datasetId: PropTypes.string,
  graphData: PropTypes.array,
  setCurrentTab: PropTypes.func.isRequired,
};

export default EvalCard;
