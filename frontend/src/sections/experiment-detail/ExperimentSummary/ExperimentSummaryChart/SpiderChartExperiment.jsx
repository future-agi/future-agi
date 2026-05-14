import React, { useMemo, useState } from "react";
import PropTypes from "prop-types";
import RedarChart from "src/sections/develop-detail/DatasetSummaryTab/ChartsContainer/RedarChart";
import { OutputTypes } from "src/sections/common/DevelopCellRenderer/CellRenderers/cellRendererHelper";
import { Box, Typography } from "@mui/material";
import { ShowComponent } from "src/components/show";
import ColumnBarChart from "src/sections/develop-detail/DatasetSummaryTab/ChartsContainer/ColumnBarChart";
import { getUniqueColorPalette } from "src/utils/utils";
import CompareDatasetSummaryIcon from "./../../../develop-detail/DatasetSummaryTab/CompareDatasetSummaryIcon";

const SpiderChartExperiment = ({ data, cols }) => {
  // Track datasets (active/inactive)
  const [datasets, setDatasets] = useState(
    (data || []).map((item) => ({ ...item, active: true })),
  );

  // Track metrics (still stored if you need filtering later, but not used in sidebar toggle)
  const metrics = useMemo(
    () => (cols || []).map((item) => ({ ...item, active: true })),
    [cols],
  );

  const radarChart = useMemo(() => {
    // labels = metrics (cols)
    const radarLabel = metrics.map((col) => col.name);

    // data = each dataset with values across all metrics

    const radarData = datasets.map((item) => ({
      name: item.experiment_dataset_name ?? item.experimentDatasetName,
      active: item.active,
      value: metrics.map((col) => item[col.name]),
    }));

    return {
      label: radarLabel,
      data: radarData,
    };
  }, [datasets, metrics]);

  return (
    <Box
      sx={{
        display: "flex",
        alignItems: "center",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "8px",
        padding: "16px",
        height: "430px",
      }}
    >
      {/* Column chart if fewer than 3 metrics */}
      <ShowComponent
        condition={
          radarChart?.label?.length > 0 && radarChart?.label?.length < 3
        }
      >
        <Box width={500}>
          <ColumnBarChart
            showCustomLegend={false}
            height={350}
            data={radarChart?.data?.map((item) =>
              item.active
                ? item
                : {
                    ...item,
                    value: item.value.map(() => null),
                  },
            )}
            graphLabels={radarChart.label}
          />
        </Box>
      </ShowComponent>

      {/* Radar chart if 3+ metrics */}
      <ShowComponent condition={radarChart?.label?.length >= 3}>
        <RedarChart
          data={radarChart?.data?.map((item) =>
            item.active
              ? item
              : {
                  ...item,
                  value: item.value.map(() => null),
                },
          )}
          graphLabels={radarChart.label}
        />
      </ShowComponent>

      {/* Sidebar with dataset toggles */}
      <Box
        sx={{
          display: "flex",
          flexDirection: "column",
          gap: 2,
          overflow: "auto",
          height: "100%",
        }}
      >
        <Box sx={{ display: "flex", flexWrap: "wrap", gap: "16px 20px" }}>
          {radarChart.data.map((temp, ind) => {
            const { tagBackground, tagForeground } = getUniqueColorPalette(ind);

            return (
              <Box
                key={ind}
                sx={{
                  display: "flex",
                  alignItems: "center",
                  gap: 0.25,
                  cursor: "pointer",
                  opacity: temp.active ? 1 : 0.5,
                }}
                onClick={() =>
                  setDatasets((prev) =>
                    prev.map((item, index) =>
                      ind === index ? { ...item, active: !item.active } : item,
                    ),
                  )
                }
              >
                <Box
                  sx={{
                    backgroundColor: tagBackground,
                    width: "16px",
                    height: "16px",
                    borderRadius: "2px",
                    border: "1px solid",
                    borderColor: tagForeground,
                  }}
                />
                <Typography
                  typography="s3"
                  fontWeight={"fontWeightMedium"}
                  sx={{ color: "text.disabled", fontWeight: 500 }}
                >
                  {temp.name}
                </Typography>
              </Box>
            );
          })}
        </Box>
        <Box
          sx={{
            padding: 2,
            borderRadius: "4px",
            display: "flex",
            flexDirection: "column",
            gap: 2,
            border: "1px solid",
            borderColor: "action.hover",
            overflowY: "auto",
          }}
        >
          {radarChart.label.map((temp, ind) => {
            const value = radarChart.data.map(
              (item) => item?.value?.[ind] || 0,
            );

            return (
              <Box
                key={ind}
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
                  {temp}
                </Typography>
                {value.map((item, index) => {
                  return (
                    <Box
                      key={`${ind}-${index}`}
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
                        <CompareDatasetSummaryIcon index={index} />
                        {temp}
                      </Typography>
                      <Typography
                        typography={"s2"}
                        fontWeight={"fontWeightRegular"}
                        color="text.primary"
                      >
                        {item}
                        {cols?.[ind]?.output_type !== OutputTypes.NUMERIC && "%"}
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
  );
};

export default SpiderChartExperiment;

SpiderChartExperiment.propTypes = {
  data: PropTypes.array,
  cols: PropTypes.array,
};
