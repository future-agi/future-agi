import React from "react";
import PropTypes from "prop-types";
import { Box, Button, Tab, Tabs } from "@mui/material";
import TabSvgIcon from "src/components/tabs/tab-svg-icon";
import SvgColor from "src/components/svg-color";
import { useTestDetailStoreShallow } from "./states";
import { useFixMyAgentBlocked } from "./common";
import { useParams } from "react-router";
import CustomTooltip from "src/components/tooltip";
import { useFixMyAgentDrawerStoreShallow } from "./FixMyAgentDrawer/state";
import { FixMyAgentDrawerSections } from "./FixMyAgentDrawer/common";

const TestExecutionDetailTabsComponent = ({
  tabs,
  currentTab,
  onTabChange,
  agentType: _agentType,
}) => {
  const { setFixMyAgentDrawerOpen } = useTestDetailStoreShallow((state) => ({
    setFixMyAgentDrawerOpen: state.setFixMyAgentDrawerOpen,
  }));

  const { executionId } = useParams();

  const { disabled, reason } = useFixMyAgentBlocked(executionId);

  const { setOpenSection } = useFixMyAgentDrawerStoreShallow((s) => ({
    setOpenSection: s.setOpenSection,
  }));

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
        paddingX: 2,
        backgroundColor: "background.paper",
        zIndex: 1,
        paddingTop: 1,
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
      }}
    >
      <Tabs value={currentTab?.id || tabs[0]?.id} onChange={handleTabChange}>
        {tabs.map((tab) => {
          return (
            <Tab
              key={tab.id}
              label={tab.title}
              value={tab.id}
              icon={<TabSvgIcon src={tab.icon} size={20} />}
            />
          );
        })}
      </Tabs>

      <CustomTooltip
        show={disabled}
        title={reason}
        placement="bottom"
        arrow
        size="small"
        type="black"
      >
        <Box>
          <Button
            variant="contained"
            color="primary"
            size="small"
            startIcon={<SvgColor src="/assets/icons/navbar/ic_optimize.svg" />}
            onClick={() => {
              setFixMyAgentDrawerOpen(true);
              setOpenSection({
                section: FixMyAgentDrawerSections.SUGGESTIONS,
              });
            }}
            disabled={disabled}
          >
            Fix My Agent
          </Button>
        </Box>
      </CustomTooltip>
    </Box>
  );
};

TestExecutionDetailTabsComponent.propTypes = {
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
  agentType: PropTypes.string,
};

const TestExecutionDetailTabs = React.memo(TestExecutionDetailTabsComponent);
TestExecutionDetailTabs.displayName = "TestExecutionDetailTabs";

export default TestExecutionDetailTabs;
