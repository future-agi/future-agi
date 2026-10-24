import { Box, Tabs, Tab, useTheme } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import FunctionOutput from "./functionOutput";
import FunctionDefinition from "./functionDefinition";

function CustomTabPanel(props) {
  const { children, value, index, ...other } = props;

  return (
    <div
      role="tabpanel"
      hidden={value !== index}
      id={`simple-tabpanel-${index}`}
      aria-labelledby={`simple-tab-${index}`}
      {...other}
    >
      {value === index && <Box sx={{ p: 3 }}>{children}</Box>}
    </div>
  );
}

CustomTabPanel.propTypes = {
  children: PropTypes.node,
  index: PropTypes.number.isRequired,
  value: PropTypes.number.isRequired,
};

function a11yProps(index) {
  return {
    id: `simple-tab-${index}`,
    "aria-controls": `simple-tabpanel-${index}`,
  };
}
const BottomFunctionsTab = ({ toolCalls, toolDefinitions = [] }) => {
  const [tabValue, setTabValue] = React.useState(0);
  const theme = useTheme();

  const handleTabChange = (event, newValue) => {
    setTabValue(newValue);
  };

  return (
    <Box
      sx={{
        width: "100%",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "12px",
        paddingY: 2,
      }}
    >
      {/* Tab headers */}
      <Box sx={{ borderBottom: 1, borderColor: "divider", pl: 3 }}>
        <Tabs
          value={tabValue}
          onChange={handleTabChange}
          aria-label="function tabs"
          textColor="primary"
          indicatorColor="primary"
          sx={{
            "& .MuiTab-root": {
              margin: "0 !important",
            },
          }}
        >
          <Tab
            sx={{
              margin: theme.spacing(0),
            }}
            label="Function Output"
            {...a11yProps(0)}
          />
          <Tab
            sx={{
              margin: theme.spacing(0),
            }}
            label="Function Definition"
            {...a11yProps(1)}
          />
        </Tabs>
      </Box>

      {/* Tab panels */}
      <CustomTabPanel value={tabValue} index={0}>
        <FunctionOutput toolCalls={toolCalls} />
      </CustomTabPanel>

      <CustomTabPanel value={tabValue} index={1}>
        <FunctionDefinition toolDefinitions={toolDefinitions} />
      </CustomTabPanel>
    </Box>
  );
};

BottomFunctionsTab.propTypes = {
  toolCalls: PropTypes.array,
  toolDefinitions: PropTypes.array,
};

export default BottomFunctionsTab;
