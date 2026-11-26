import { Box, Typography, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";

const DescriptionSummary = ({ showTitle }) => {
  const theme = useTheme();
  const tableStyle = {
    width: "100%",
    borderCollapse: "separate",
    borderSpacing: 0,
    fontFamily: "Inter, sans-serif",
    border: `0.5px solid ${theme.palette.divider}`,
    borderRadius: "8px",
    overflow: "hidden",
  };

  const thStyle = {
    border: `0.5px solid ${theme.palette.divider}`,
    padding: "8px",
    backgroundColor: theme.palette.background.paper,
    textAlign: "left",
    fontWeight: theme.typography["fontWeightSemiBold"],
    ...theme.typography["s2"],
  };

  const tdStyle = {
    border: `0.5px solid ${theme.palette.divider}`,
    padding: "8px",
    backgroundColor: theme.palette.background.paper,
    fontWeight: theme.typography["fontWeightRegular"],
    ...theme.typography["s2"],
  };

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        gap: "12px",
        backgroundColor: "status.warning.bg",
        border: "1px solid",
        borderColor: "status.warning.border",
        padding: "12px",
        borderRadius: "8px",
      }}
    >
      {showTitle && (
        <Typography
          variant="s1"
          fontWeight={"fontWeightSemiBold"}
          color="text.primary"
        >
          How to write effective description?
        </Typography>
      )}
      <Box>
        <Typography
          fontWeight={"fontWeightRegular"}
          color="text.primary"
          typography={"s2"}
        >
          A good description clearly helps the system understand the scope and
          nature of the data, lending to outputs that are more focused and
          aligned with your expectations.
        </Typography>
      </Box>
      <Box>
        <table style={tableStyle}>
          <thead>
            <tr>
              <th style={thStyle}>Element</th>
              <th style={thStyle}>Example</th>
              <th style={thStyle}>Purpose</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((item) => (
              <tr key={item.id}>
                <td style={thStyle}>{item.element}</td>
                <td style={tdStyle}>{item.example}</td>
                <td style={tdStyle}>{item.element}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Box>
      <Box>
        <Typography
          fontWeight={"fontWeightRegular"}
          color="text.primary"
          fontSize="12px"
          lineHeight="18px"
        >
          This dataset should simulate user complaints related to failed digital
          payments within a mobile banking app. It should reflect typical issues
          reported by users and represent a variety of real-world support
          scenarios.
        </Typography>
      </Box>
    </Box>
  );
};

export default DescriptionSummary;

DescriptionSummary.propTypes = {
  showTitle: PropTypes.bool,
};

const rows = [
  {
    id: 1,
    element: "Scenario defined",
    example: "“simulate user complaints”",
    purpose: "States what data to generate",
  },
  {
    id: 2,
    element: "Domain specified",
    example: "“failed digital payments within a mobile banking app”",
    purpose: "Established context and person",
  },
  {
    id: 3,
    element: "Output expectation",
    example: "“typical issues reported by users”",
    purpose: "Guides content requirements",
  },
  {
    id: 4,
    element: "Variability defined",
    example: "“variety of real-world support scenarios”",
    purpose: "Indicates need for diversity",
  },
];
