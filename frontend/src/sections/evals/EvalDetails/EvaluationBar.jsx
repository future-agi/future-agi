import React from "react";
import PropTypes from "prop-types";
import { Box, useTheme, Tab, Tabs } from "@mui/material";
import { Events, PropertyName, trackEvent } from "src/utils/Mixpanel";
import { useParams } from "react-router";

const TabOptions = [
  { label: "Logs", value: "logs" },
  { label: "Config", value: "config" },
  // { label: "Feedback", value: "feedback" },
];

const EvaluationBar = ({
  currentTab,
  setCurrentTab,
  rowSelected,
  setRowSelected,
}) => {
  const theme = useTheme();
  const { id } = useParams();

  return (
    <Box>
      <Box
        sx={{
          borderBottom: 1,
          borderColor: "divider",
          display: "flex",
          alignItems: "center",
          // marginTop: 1,
          justifyContent: "space-between",
        }}
      >
        <Box>
          <Tabs
            textColor="primary"
            value={currentTab}
            onChange={(e, value) => {
              setCurrentTab(value);
              setRowSelected([]);
              if (value === "config") {
                trackEvent(Events.usageConfigClicked, {
                  [PropertyName.evalId]: id,
                });
              }
            }}
            sx={{
              "& .MuiTab-root": {
                margin: "0 !important",
              },
            }}
          >
            {TabOptions.map((tab) => (
              <Tab
                key={tab.value}
                label={tab.label}
                value={tab.value}
                sx={{
                  margin: theme.spacing(0),
                }}
              />
            ))}
          </Tabs>
        </Box>

        {rowSelected.length > 0 && (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-around",
              border: "1px solid var(--border-default)",
              borderRadius: "8px",
              padding: "4px 16px",
              gap: "8px",
              marginRight: "5px",
              backgroundColor: "background.paper",
              marginLeft: "auto",
            }}
          >
            <Box
              sx={{
                color: theme.palette.primary.main,
                marginRight: "12px",
                fontFamily: "IBM Plex Sans",
                fontWeight: 500,
                fontSize: "13px",
                lineHeight: "22px",
                paddingRight: "24px",
                borderRight: "2px solid var(--border-default)",
              }}
            >
              {`${rowSelected.length} selected`}
            </Box>
          </Box>
        )}
      </Box>
    </Box>
  );
};

EvaluationBar.propTypes = {
  currentTab: PropTypes.string,
  setCurrentTab: PropTypes.func,
  rowSelected: PropTypes.array.isRequired,
  setRowSelected: PropTypes.func.isRequired,
  rightSection: PropTypes.func.isRequired,
  tabRef: PropTypes.object.isRequired,
};

export default EvaluationBar;
