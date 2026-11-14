import React from "react";
import {
  VictoryChart,
  VictoryLine,
  VictoryTooltip,
  VictoryVoronoiContainer,
  VictoryAxis,
} from "victory";

// Sample data for the first line
const dataLine1 = [
  { x: new Date(2019, 6), y: 3000 },
  { x: new Date(2019, 7), y: 3200 },
  // ... other data points
];

// Sample data for the second line
const dataLine2 = [
  { x: new Date(2019, 6), y: 2800 },
  { x: new Date(2019, 7), y: 2700 },
  // ... other data points
];

export default function LineChart() {
  return (
    <VictoryChart
      containerComponent={
        <VictoryVoronoiContainer
          labels={({ datum }) =>
            `${datum.y} events on ${datum.x.toLocaleDateString()}`
          }
          labelComponent={
            <VictoryTooltip
              style={{ fontSize: 12 }}
              cornerRadius={0} // This will make the tooltip a square shape
              //   flyoutStyle={tooltipStyle}
              flyoutPadding={{ top: 5, bottom: 5, left: 15, right: 15 }} // Adjust padding as needed
              pointerLength={5} // The little arrow at the bottom of the tooltip
              // Add this for a smoother transition
              //   transitionDuration={500}
            />
          }
        />
      }
    >
      <VictoryAxis
        tickFormat={(t) =>
          new Date(t).toLocaleDateString("en-US", {
            month: "short",
            year: "numeric",
          })
        }
      />
      <VictoryAxis dependentAxis />
      <VictoryLine data={dataLine1} />
      <VictoryLine data={dataLine2} />
    </VictoryChart>
  );
}
