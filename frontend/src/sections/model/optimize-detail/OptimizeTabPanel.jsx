import { Box, Tab, Tabs } from "@mui/material";
import React from "react";
import PropTypes from "prop-types";

const OptimizeTabPanel = ({ openTab, setOpenTab }) => {
  return (
    <Box sx={{ borderBottom: 1, borderColor: "divider", paddingLeft: 2.5 }}>
      <Tabs
        value={openTab}
        onChange={(_, v) => setOpenTab(v)}
        textColor="primary"
      >
        <Tab label="Explore" value="explore" />
        <Tab label="Results" value="results" />
      </Tabs>
    </Box>
  );
};

OptimizeTabPanel.propTypes = {
  openTab: PropTypes.string,
  setOpenTab: PropTypes.func,
};

export default OptimizeTabPanel;
