import { Box, Tab, Tabs, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import { useLocation, useNavigate, useParams } from "react-router";

const TabOptions = [
  { label: "Data", value: "data" },
  // { label: "Summary", value: "summary" },
];

const IndividualExperimentBar = ({ rightSection }) => {
  const location = useLocation();
  const navigate = useNavigate();
  const { individualExperimentId } = useParams();

  // The page is first rendered at /individual-experiment/:id (before the
  // redirect to /data) and /summary has no tab, so only a known tab value is
  // passed to Tabs; MUI logs an error for any other value.
  const lastSegment = location.pathname.split("/").pop();
  const currentTab = TabOptions.some((tab) => tab.value === lastSegment)
    ? lastSegment
    : false;

  const theme = useTheme();

  return (
    <Box
      sx={{
        borderBottom: 1,
        borderColor: "divider",
        paddingX: 2.5,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
      }}
    >
      <Box>
        <Tabs
          value={currentTab}
          // sx={{ color: "primary.main" }}
          TabIndicatorProps={{
            style: {
              backgroundColor: theme.palette.primary.main,
            },
          }}
          sx={{
            minHeight: 32,
            "& .MuiTab-root": {
              minHeight: 32,
              padding: "12px 12px",
              marginRight: 0,
              color: "text.disabled",
            },
            "& .MuiTab-root.Mui-selected": {
              color: "primary.main",
            },
          }}
          onChange={(e, value) => {
            navigate(
              `/dashboard/develop/individual-experiment/${individualExperimentId}/${value}`,
              {
                replace: true,
              },
            );
          }}
        >
          {TabOptions.map((tab) => (
            <Tab key={tab.value} label={tab.label} value={tab.value} />
          ))}
        </Tabs>
      </Box>
      <Box>{rightSection}</Box>
    </Box>
  );
};

IndividualExperimentBar.propTypes = {
  rightSection: PropTypes.any,
};

export default IndividualExperimentBar;
