import { Box, Tab, Tabs } from "@mui/material";
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

  const currentTab = location.pathname.split("/").pop();

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
