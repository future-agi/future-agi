import React, { useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Button, Tab, Tabs } from "@mui/material";
import { ShowComponent } from "src/components/show";
import TestExecutionSelectionPopover from "./TestExecutionSelectionPopover";
import { useSelectedExecutionsStoreShallow } from "./states";

const TestDetailTabsComponent = ({ tabs, currentTab, onTabChange }) => {
  const [open, setOpen] = useState(false);
  const anchorRef = useRef(null);
  const selectedExecutions = useSelectedExecutionsStoreShallow(
    (s) => s.selectedExecutions,
  );
  // Get all tabs with icons from navData

  const handleTabChange = React.useCallback(
    (event, newTabId) => {
      onTabChange(event, newTabId);
    },
    [onTabChange],
  );

  return (
    <Box
      sx={{
        borderBottom: 1,
        borderColor: "divider",
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        gap: 1,
      }}
    >
      <Tabs value={currentTab?.id || tabs[0]?.id} onChange={handleTabChange}>
        {tabs.map((tab) => {
          return (
            <Tab
              key={tab.id}
              label={tab.title}
              value={tab.id}
              icon={tab?.icon}
            />
          );
        })}
      </Tabs>
      <ShowComponent condition={currentTab?.id === "analytics"}>
        <Button
          variant="outlined"
          size="small"
          ref={anchorRef}
          onClick={() => {
            setOpen(true);
          }}
        >
          Executions ({selectedExecutions.length})
        </Button>
        <TestExecutionSelectionPopover
          open={open}
          onClose={() => setOpen(false)}
          anchor={anchorRef.current}
        />
      </ShowComponent>
    </Box>
  );
};

TestDetailTabsComponent.propTypes = {
  tabs: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      title: PropTypes.string.isRequired,
      path: PropTypes.string.isRequired,
    }),
  ).isRequired,
  currentTab: PropTypes.shape({
    id: PropTypes.string.isRequired,
    title: PropTypes.string.isRequired,
    path: PropTypes.string.isRequired,
  }),
  onTabChange: PropTypes.func.isRequired,
};

const TestDetailTabs = React.memo(TestDetailTabsComponent);
TestDetailTabs.displayName = "TestDetailTabs";

export default TestDetailTabs;
