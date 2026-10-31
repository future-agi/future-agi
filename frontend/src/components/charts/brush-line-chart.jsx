/* eslint-disable no-restricted-syntax -- ds: data colours (chart series) */
import React, { useState } from "react";
import PropTypes from "prop-types";
import { useTheme } from "@mui/material";
import {
  VictoryAxis,
  VictoryChart,
  VictoryLine,
  VictoryScatter,
  VictoryTooltip,
  createContainer,
} from "victory";

export default function BrushLineChart({ data }) {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";
  data = [
    { x: new Date(2022, 11, 26), y: 0.5 },
    { x: new Date(2022, 11, 27), y: 0.1 },
    { x: new Date(2022, 11, 28), y: 0.2 },
    { x: new Date(2022, 11, 29), y: 0.8 },
    { x: new Date(2022, 11, 30), y: 0.5 },
    { x: new Date(2022, 11, 31), y: 0.6 },
  ];
  const initialDomain = {
    x: [data[0].x, data[data.length - 1].x],
  };
  const [selectedDomain] = useState(initialDomain);

  // const handleBrush = (domain) => {
  //   setSelectedDomain(domain);
  //   onDataRangeChange(domain);
  // };

  const VictoryCombinedContainer = createContainer("brush", "voronoi");

  return (
    <div style={{ width: "100%" }}>
      <VictoryChart
        height={80}
        padding={{ bottom: 25, left: 35, top: 5 }} // Adjust as needed for your axis labels
        scale={{ x: "time" }}
        domainPadding={{ x: [10, 10], y: [10, 10] }} // Adjust these values as needed
        containerComponent={
          <VictoryCombinedContainer
            voronoiBlacklist={["lineChart"]}
            mouseFollowTooltips
            voronoiDimension="x"
            brushDimension="x"
            brushDomain={selectedDomain}
            // onBrushDomainChange={handleBrush}
            labels={({ datum }) =>
              `Date: ${datum.x.toLocaleString()}\nFalse Negative Rate A: ${datum.y}`
            }
            labelComponent={
              <VictoryTooltip
                centerOffset={{ x: 60, y: 5 }}
                flyoutStyle={{
                  stroke: ({ active }) => (active ? "white" : "none"),
                  fill: isDark ? "#18181b" : "black",
                  strokeWidth: 2,
                }}
                style={{
                  fill: isDark ? "#18181b" : "#ffffff",
                  fontSize: 6,
                }}
                pointerLength={0} // Adjust or remove pointer
                cornerRadius={5} // To have rounded corners
                flyoutPadding={{ top: 5, bottom: 5, left: 15, right: 15 }} // Adjust padding
              />
            }
            brushStyle={{
              fill: isDark ? "#18181b" : "#551a8b33",
              stroke: isDark ? "#a1a1aa" : "black", // Color of the border
              strokeWidth: 1, // Thickness of the border
              strokeDasharray: "4, 4", // Dotted border pattern
            }}
          />
        }
      >
        <VictoryAxis
          tickFormat={(x) => `${x.getDate()}/${x.getMonth() + 1}`}
          style={{
            axisLabel: {
              padding: 12,
              fontSize: 8,
              fontFamily: "Inter, sans-serif",
              margin: 0,
            },
            ticks: { stroke: "grey", size: 5 },
            tickLabels: {
              fontSize: 6.5,
              fontFamily: "Inter, sans-serif",
              padding: 0,
            },
          }}
          label="Date"
        />
        <VictoryAxis
          dependentAxis
          tickFormat={(y) => `${y.toFixed(2)}`} // Customize this to format your Y values as needed
          style={{
            axisLabel: {
              padding: 25,
              fontSize: 8,
              fontFamily: "Inter, sans-serif",
              margin: 0,
            },
            ticks: { stroke: "grey", size: 5 },
            tickLabels: {
              fontSize: 6.5,
              fontFamily: "Inter, sans-serif",
              padding: 0,
            },
          }}
          label="Value"
        />
        <VictoryLine
          name="lineChart"
          interpolation="natural"
          style={{
            data: { stroke: "#551a8b" },
          }}
          data={data}
        />
        <VictoryScatter data={data} size={({ active }) => (active ? 3 : 2)} />
      </VictoryChart>
    </div>
  );
}

BrushLineChart.propTypes = {
  data: PropTypes.object,
};
