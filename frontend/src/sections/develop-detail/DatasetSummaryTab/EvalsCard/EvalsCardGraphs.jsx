import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import { Box, Tab, Typography } from "@mui/material";
import { ShowComponent } from "src/components/show";
import { getChartData, getPassFailChartData } from "../chartData";
import Iconify from "src/components/iconify";
import EvalsCardLoading from "../Loaders/EvalsCardLoading";
import CriticalIssues from "../CriticalIssues";
import { getSuffixForCharts } from "../ChartsContainer/constant";
import { OutputTypes } from "src/sections/common/DevelopCellRenderer/CellRenderers/cellRendererHelper";

// Eagerly load chart components — always visible on summary page
import RedarChart from "../ChartsContainer/RedarChart";
import AreaChartWrapper from "../ChartsContainer/AreaChartWrapper";
import StackBarChart from "../ChartsContainer/StackBarChart";
import DonutChart from "../ChartsContainer/DonutChart";
import ColumnBarChart from "../ChartsContainer/ColumnBarChart";
import AnalysisCard from "src/sections/common/Analysis/AnalysisCard";
import { datasetGridHref } from "src/sections/common/Analysis/analysisLinks";
import { SegmentedTabs } from "src/components/tabs/tabs";
import EvalsOverviewTable from "../EvalsOverviewTable";
import { useDsEvalsLayout } from "../dsEvalsLayout";
import ColumnsMenu from "src/sections/common/ux/oneview/ColumnsMenu";
import EvalHistogramCard, { FailingRowsLink } from "./EvalHistogramCard";

const EvalsCardGraphs = ({
  data,
  isPending,
  isLoading,
  datasetIndex,
  emptyComponent,
  showCriticalIssues = true,
  mode = "develop",
  overviewRows,
  agreement,
  datasetId,
  version,
}) => {
  const [view, setView] = useState("table");
  // A1-37: the evals table's columns (`3 hidden` by default), per dataset.
  const evalsLayout = useDsEvalsLayout(datasetId);
  const redarChart = useMemo(() => {
    const redarLabel = [];
    const redarData = [];
    const radarOutputType = [];

    data?.forEach((item) => {
      if (mode === "develop" && !item?.isVisible) return;
      if (
        item.output_type === "score" ||
        item.output_type === OutputTypes.NUMERIC
      ) {
        redarData.push(item.total_avg);
        redarLabel.push(item.name);
      }
      if (item.output_type === "Pass/Fail") {
        redarLabel.push(item.name);
        redarData.push(item.total_pass_rate);
      }
      radarOutputType.push(item.output_type);
    });

    if (redarLabel.length < 3) {
      return {
        label: redarLabel,
        data: redarLabel.map((item, index) => ({
          name: item,
          value: redarData.map((temp, ind) => (ind === index ? temp : null)),
          outputType: radarOutputType[index],
        })),
      };
    }

    return {
      label: redarLabel,
      data: [
        { value: redarData, name: redarLabel, outputType: radarOutputType },
      ],
    };
  }, [data, mode]);

  const hasOverview = Boolean(overviewRows?.length);
  const sideBySide = showCriticalIssues && !hasOverview;
  const rowByTemplate = useMemo(() => {
    const map = {};
    (overviewRows ?? []).forEach((r) => {
      if (!map[r.templateId]) map[r.templateId] = r;
    });
    return map;
  }, [overviewRows]);
  const evalLinks = useMemo(() => {
    const map = {};
    (overviewRows ?? []).forEach((r) => {
      map[r.id] = {
        count: r.failing ?? 0,
        href: datasetGridHref(datasetId, r.failingFilters, { version }),
      };
    });
    return map;
  }, [overviewRows, datasetId, version]);

  if (isPending || isLoading) {
    return <EvalsCardLoading />;
  }

  const radarBox = (
    <ShowComponent condition={redarChart?.label?.length > 0}>
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "8px",
          padding: "16px",
          height: "430px",
          bgcolor: "background.paper",
          ...(sideBySide ? {} : { width: "100%" }),
        }}
      >
        <ShowComponent
          condition={
            redarChart?.label?.length > 0 && redarChart?.label?.length < 3
          }
        >
          <Box width={sideBySide ? 500 : "calc(100% - 250px - 32px)"}>
            <ColumnBarChart
              data={redarChart.data}
              graphLabels={redarChart.label}
            />
          </Box>
        </ShowComponent>
        <ShowComponent condition={redarChart?.label?.length >= 3}>
          <Box
            sx={{
              width: sideBySide ? "auto" : "calc(100% - 250px - 32px)",
            }}
          >
            <RedarChart data={redarChart.data} graphLabels={redarChart.label} />
          </Box>
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
            width: sideBySide ? "250px" : "300px",
            overflowY: "auto",
          }}
        >
          {redarChart.label.map((temp, ind) => (
            <Box
              key={temp}
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
                  {temp}
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
                  {redarChart?.label?.length > 0 &&
                  redarChart?.label?.length < 3
                    ? redarChart?.data?.[ind]?.value || 0
                    : redarChart?.data?.[0]?.value?.[ind] || 0}{" "}
                  {getSuffixForCharts(data, ind)}
                </Typography>
              </Box>
            </Box>
          ))}
        </Box>
      </Box>
    </ShowComponent>
  );
  return (
    <Box display={"flex"} gap={2} flexDirection={"column"} height="97%">
      <ShowComponent condition={data?.length > 0}>
        {hasOverview ? (
          <>
            <AnalysisCard
              icon="mdi:clipboard-check-outline"
              title="Evals on this dataset"
              subtitle={`${overviewRows.length} ${overviewRows.length === 1 ? "eval" : "evals"} · pass rates use each eval's direction and threshold`}
              actions={
                <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                  {view === "table" && <ColumnsMenu layout={evalsLayout} />}
                  <Box sx={{ mb: 0 }}>
                    <SegmentedTabs value={view} onChange={(e, v) => setView(v)}>
                      <Tab label="Table" value="table" />
                      <Tab label="Radar" value="radar" />
                    </SegmentedTabs>
                  </Box>
                </Box>
              }
            >
              {view === "table" ? (
                <EvalsOverviewTable
                  rows={overviewRows}
                  agreement={agreement}
                  datasetId={datasetId}
                  version={version}
                />
              ) : (
                radarBox
              )}
            </AnalysisCard>
            <Box display={"flex"} gap={2}>
              <ShowComponent condition={showCriticalIssues}>
                <Box
                  sx={{
                    flex: 1,
                    border: "1px solid",
                    borderColor: "divider",
                    borderRadius: "8px",
                    height: "430px",
                  }}
                >
                  <CriticalIssues mode={mode} evalLinks={evalLinks} />
                </Box>
              </ShowComponent>
            </Box>
          </>
        ) : (
          <Box display={"flex"} gap={2} flexWrap={"wrap"}>
            {radarBox}
            <ShowComponent condition={showCriticalIssues}>
              <Box
                sx={{
                  flex: 1,
                  border: "1px solid",
                  borderColor: "divider",
                  borderRadius: "8px",
                  height: "430px",
                }}
              >
                <CriticalIssues mode={mode} evalLinks={evalLinks} />
              </Box>
            </ShowComponent>
          </Box>
        )}

        <Box display={"flex"} gap={2} flexWrap={"wrap"}>
          {data
            ?.filter((e) => (mode === "develop" ? e?.isVisible : true))
            ?.map((item) => {
              const headerData = {
                name: item.name,
                average:
                  item.output_type == "choices"
                    ? item.total_choices_avg
                    : item.output_type == "Pass/Fail"
                      ? item.total_pass_rate
                      : item.total_avg,
                isNumericEval:
                  item.output_type == OutputTypes.NUMERIC
                    ? true
                    : item?.is_numeric_eval,
                isNumericEvalPercentage: item?.is_numeric_eval_percentage,
              };
              const applySort = Boolean(item?.is_numeric_eval);
              const overviewRow = rowByTemplate[item.id];
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
                    bgcolor: "background.paper",
                  }}
                >
                  <ShowComponent condition={item.output_type == "choices"}>
                    <DonutChart
                      height={300}
                      data={graphData}
                      graphLabels={graphLabels}
                      headerData={headerData}
                      datasetIndex={datasetIndex}
                    />
                  </ShowComponent>
                  <ShowComponent condition={item.output_type == "Pass/Fail"}>
                    <StackBarChart
                      height={350}
                      data={graphData}
                      graphLabels={graphLabels}
                      headerData={headerData}
                      datasetIndex={datasetIndex}
                    />
                    {overviewRow && (
                      <FailingRowsLink
                        row={overviewRow}
                        datasetId={datasetId}
                        version={version}
                      />
                    )}
                  </ShowComponent>
                  <ShowComponent
                    condition={
                      item.output_type == "score" && Boolean(overviewRow)
                    }
                  >
                    {overviewRow && (
                      <EvalHistogramCard
                        row={overviewRow}
                        datasetId={datasetId}
                        version={version}
                      />
                    )}
                  </ShowComponent>
                  <ShowComponent
                    condition={
                      (item.output_type == "score" && !overviewRow) ||
                      item.output_type === OutputTypes.NUMERIC
                    }
                  >
                    <AreaChartWrapper
                      data={graphData}
                      graphLabels={graphLabels}
                      headerData={headerData}
                      datasetIndex={datasetIndex}
                    />
                  </ShowComponent>
                </Box>
              );
            })}
        </Box>
      </ShowComponent>
      <ShowComponent condition={!data || data?.length === 0}>
        {emptyComponent}
      </ShowComponent>
    </Box>
  );
};

EvalsCardGraphs.propTypes = {
  data: PropTypes.array,
  isPending: PropTypes.bool,
  isLoading: PropTypes.bool,
  datasetIndex: PropTypes.number,
  emptyComponent: PropTypes.any,
  showCriticalIssues: PropTypes.bool,
  mode: PropTypes.string,
  overviewRows: PropTypes.array,
  agreement: PropTypes.array,
  datasetId: PropTypes.string,
  version: PropTypes.oneOfType([PropTypes.string, PropTypes.number]),
};

export default EvalsCardGraphs;
