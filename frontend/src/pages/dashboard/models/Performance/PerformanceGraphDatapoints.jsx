import {
  Box,
  Paper,
  Table,
  TableBody,
  TableHead,
  TableRow,
} from "@mui/material";
import { format } from "date-fns";
import PropTypes from "prop-types";
import React from "react";
import PerformanceTableCell from "./PerformanceTableCell";

const PerformanceGraphDatapoints = ({ graphData, selectedDataset }) => {
  const filteredGraphData = graphData
    ? Object.entries(graphData)
        .filter(([name]) => name.includes(`Dataset ${selectedDataset + 1}`))
        .reduce((acu, [name, points]) => {
          acu[name] = points;
          return acu;
        }, {})
    : [];

  return (
    <Box sx={{ overflowX: "auto" }}>
      <Table component={Paper}>
        <TableHead>
          <TableRow>
            <PerformanceTableCell>Datapoint</PerformanceTableCell>
            {Object.values(filteredGraphData)?.[0]?.map(([date]) => (
              <PerformanceTableCell key={date}>
                {format(new Date(date), "dd/MM/yy")}
              </PerformanceTableCell>
            ))}
          </TableRow>
        </TableHead>
        <TableBody>
          {Object.entries(filteredGraphData).map(([name, points]) => (
            <TableRow key={name}>
              <PerformanceTableCell>{name}</PerformanceTableCell>
              {points.map(([date, score]) => (
                <PerformanceTableCell key={date}>
                  {Math.round(score)}
                </PerformanceTableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Box>
  );
};

PerformanceGraphDatapoints.propTypes = {
  graphData: PropTypes.object,
  selectedDataset: PropTypes.object,
};

export default PerformanceGraphDatapoints;
