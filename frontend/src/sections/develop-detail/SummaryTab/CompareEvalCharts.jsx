import React from "react";
import { Box, IconButton, Typography } from "@mui/material";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  ResponsiveContainer,
  Cell,
  CartesianGrid,
} from "recharts";
import Iconify from "src/components/iconify";
import PropTypes from "prop-types";
import { colorPalette, getUniqueColorPalette } from "src/utils/utils";
import Tooltip from "@mui/material/Tooltip";

// Custom Y-axis label renderer for the chart
const CustomYAxisTick = (props) => {
  const { x, y, payload, chartData } = props;

  // Find the index of the dataset in chartData
  const index = chartData.findIndex((item) => item.name === payload.value);
  const { tagBackground, tagForeground } = getUniqueColorPalette(index);

  return (
    <g transform={`translate(${x},${y})`}>
      <foreignObject x={-190} y={-10} width="220" height="40">
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
          {/* Dataset ID Box */}
          <Box
            sx={{
              backgroundColor: tagBackground,
              color: tagForeground,
              // padding: "2px 4px",
              borderRadius: "6px",
              fontSize: "12px",
              marginRight: "8px",
              display: "flex",
              height: "24px",
              width: "24px",
              justifyContent: "center",
              alignItems: "center",
            }}
          >
            {String.fromCharCode(65 + index)}
          </Box>

          {/* Text Label */}
          <Tooltip title={payload.value}>
            <Typography
              sx={{
                fontSize: 12,
                color: "text.secondary",
                fontWeight: 500,
              }}
            >
              {payload.value.length > 20
                ? payload.value.substring(0, 25) + "..."
                : payload.value}
            </Typography>
          </Tooltip>
        </Box>
      </foreignObject>
    </g>
  );
};

// Custom X-axis tick renderer
const CustomXAxisTick = (props) => {
  const { x, y, payload } = props;
  return (
    <text
      x={x}
      y={y + 20}
      textAnchor="middle"
      fontSize={13}
      fill="currentColor"
    >
      {payload.value}%
    </text>
  );
};

// Proper prop validation for custom components
CustomYAxisTick.propTypes = {
  x: PropTypes.number,
  y: PropTypes.number,
  payload: PropTypes.shape({
    value: PropTypes.any,
  }),
  chartData: PropTypes.array,
};

CustomXAxisTick.propTypes = {
  x: PropTypes.number,
  y: PropTypes.number,
  payload: PropTypes.shape({
    value: PropTypes.any,
  }),
};

const CompareEvalCharts = ({ multiChartData, metricColumns }) => {
  // Render custom Y-axis tick with proper chartData prop
  const renderCustomYAxisTick = (props, chartData) => (
    <CustomYAxisTick {...props} chartData={chartData} />
  );

  return (
    <Box mt={4}>
      {metricColumns.map(
        (columnName) =>
          multiChartData[columnName] &&
          multiChartData[columnName].length > 0 && (
            <Box
              key={columnName}
              sx={{
                border: "1px solid var(--border-light)",
                paddingX: "10px",
                paddingY: "20px",
                borderRadius: "8px",
                mb: 3,
              }}
            >
              <Box sx={{ display: "flex", alignItems: "center", gap: 1 }}>
                <IconButton
                  size="small"
                  sx={{
                    color: "status.success.fg",
                    border: "1px solid",
                    borderColor: "status.success.fg",
                    width: "15px",
                    padding: 0,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <Iconify icon="solar:check-linear" width={14} height={14} />
                </IconButton>
                <Typography
                  sx={{
                    fontSize: "13px",
                    fontWeight: 500,
                    color: "text.primary",
                  }}
                >
                  {columnName}
                </Typography>
              </Box>

              <ResponsiveContainer
                width="100%"
                height={(multiChartData[columnName]?.length || 1) * 74}
              >
                <BarChart
                  layout="vertical"
                  data={multiChartData[columnName]}
                  margin={{ top: 20, right: 80, left: 20, bottom: 5 }}
                >
                  {/* X-Axis (Horizontal) - No line, No tick lines */}
                  <XAxis
                    type="number"
                    domain={[0, 100]}
                    tick={<CustomXAxisTick />}
                    axisLine={false}
                    tickLine={false}
                  />

                  {/* Y-Axis (Vertical) - Custom Labels, No Line */}
                  <YAxis
                    type="category"
                    dataKey="name"
                    width={180}
                    tick={(props) =>
                      renderCustomYAxisTick(props, multiChartData[columnName])
                    }
                    axisLine={false}
                    tickLine={false}
                  />

                  {/* Custom grid lines between bars */}
                  <CartesianGrid
                    horizontal={true}
                    vertical={false}
                    stroke="divider"
                    strokeDasharray="1 1"
                    strokeWidth={1}
                    horizontalPoints={[multiChartData[columnName]?.length * 62]}
                  />

                  {/* Bars with labels */}
                  <Bar dataKey="value" barSize={34} radius={[4, 4, 4, 4]}>
                    {multiChartData[columnName].map((entry, index) => (
                      <Cell
                        key={`cell-${index}`}
                        fill={
                          colorPalette[index % colorPalette.length].graphBgColor
                        }
                        strokeWidth={0.5}
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </Box>
          ),
      )}
    </Box>
  );
};

CompareEvalCharts.propTypes = {
  multiChartData: PropTypes.object.isRequired,
  metricColumns: PropTypes.array.isRequired,
};

export default CompareEvalCharts;
