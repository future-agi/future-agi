import {
  Box,
  // Button,
  Stack,
  Typography,
  Tab,
} from "@mui/material";
import { SegmentedTabs } from "src/components/tabs/tabs";
import PropTypes from "prop-types";
import React, { useMemo, useState } from "react";
import { tabOptions } from "./common";
// import SvgColor from "src/components/svg-color";
import BreakDownDrawer from "./BreakDownDrawer";
import NoWorkspaceUsageTable from "./EvalutionsBreakdownTable";
import WorkspaceUsageTable from "./Workspacestable";

export default function TableView({
  singleWorkSpace,
  selectedMonth,
  totalMetrics,
  selectedWorkspaceId,
}) {
  const [currentTab, setCurrentTab] = useState("cost");
  const [openBreakDownDrawer, setOpenBreakDownDrawer] = useState(false);
  const [selectedWorkspace, setSelectedWorkspace] = useState(null);

  const heading = useMemo(() => {
    if (singleWorkSpace) {
      return {
        title: "Evaluation Cost Usage Breakdown",
        subtitle: "Detailed cost usage by evaluation",
      };
    }
    return {
      title: "Workspace Cost Usage Breakdown",
      subtitle: "Detailed cost usage by workspace",
    };
  }, [singleWorkSpace]);

  const handleClose = () => {
    setOpenBreakDownDrawer(false);
  };

  return (
    <>
      <Box
        sx={{
          padding: 2,
          border: "1px solid",
          borderColor: "divider",
          display: "flex",
          flexDirection: "column",
          gap: 2.5,
        }}
      >
        <Stack
          direction={"row"}
          justifyContent={"space-between"}
          alignItems={"flex-end"}
        >
          <Stack gap={1}>
            <Typography
              color={"text.primary"}
              typography={"m3"}
              fontWeight={"fontWeightMedium"}
            >
              {heading.title}
            </Typography>
            <Typography
              color={"text.primary"}
              typography={"s1"}
              fontWeight={"fontWeightRegular"}
            >
              {heading.subtitle}
            </Typography>
          </Stack>
          <Box
            sx={{
              width: "fit-content",
              display: "flex",
              flexDirection: "row",
              gap: 1,
            }}
          >
            <Box sx={{ mb: 1.5 }}>
              <SegmentedTabs
                value={currentTab}
                onChange={(e, value) => setCurrentTab(value)}
              >
                {tabOptions.map((tab) => (
                  <Tab
                    key={tab.value}
                    label={tab.label}
                    value={tab.value}
                    disabled={tab.disabled}
                  />
                ))}
              </SegmentedTabs>
            </Box>
            {/* <Button
              variant="outlined"
              size="small"
              startIcon={
                <SvgColor
                  src="/assets/icons/action_buttons/ic_download.svg"
                  sx={{
                    width: 20,
                    height: 20,
                    color: "text.primary",
                  }}
                />
              }
            >
              Export
            </Button> */}
          </Box>
        </Stack>
        {singleWorkSpace ? (
          <NoWorkspaceUsageTable
            currentTab={currentTab}
            selectedMonth={selectedMonth}
            workspaceId={selectedWorkspaceId}
          />
        ) : (
          <WorkspaceUsageTable
            currentTab={currentTab}
            selectedMonth={selectedMonth}
            setOpenBreakDownDrawer={setOpenBreakDownDrawer}
            setSelectedWorkspace={setSelectedWorkspace}
            totalMetrics={totalMetrics}
          />
        )}
      </Box>
      <BreakDownDrawer
        currentTab={currentTab}
        selectedWorkspace={selectedWorkspace}
        selectedMonth={selectedMonth}
        open={openBreakDownDrawer}
        onClose={handleClose}
      />
    </>
  );
}

TableView.propTypes = {
  singleWorkSpace: PropTypes.bool,
  isLoadingMetrics: PropTypes.bool,
  selectedMonth: PropTypes.string,
  selectedWorkspaceId: PropTypes.string,
  totalMetrics: PropTypes.object,
};
