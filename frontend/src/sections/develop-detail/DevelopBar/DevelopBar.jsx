import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, useTheme, Tab, Tabs } from "@mui/material";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";
import { enqueueSnackbar } from "notistack";
import { trackEvent, Events } from "src/utils/Mixpanel";
// primary is accessed via theme.palette.primary for dark mode support
import logger from "src/utils/logger";
import SvgColor from "src/components/svg-color";
import { useAuthContext } from "src/auth/hooks";

const TabOptions = [
  { label: "Data", value: "data", icons: "/assets/icons/summary/database.svg" },
  {
    label: "Experiments",
    value: "experiments",
    icons: "/assets/icons/navbar/ic_experiment.svg",
  },
  {
    label: "Optimization",
    value: "optimization",
    icons: "/assets/icons/action_buttons/ic_optimize.svg",
  },
  {
    label: "Summary",
    value: "summary",
    icons: "/assets/icons/ic_summary.svg",
  },
];

const DevelopBar = ({
  isCompareDataset,
  currentTab,
  setCurrentTab,
  rowSelected,
  setRowSelected,
  rightSection: _rightSection,
  tabRef,
  hideScenarioFeatures,
}) => {
  const theme = useTheme();
  const [_openDialog, setOpenDialog] = useState(null); // Tracks which dialog is open
  const { role: _role } = useAuthContext();
  const _handleButtonClick = (action) => {
    if (action === "Re-Run") {
      trackEvent(Events.expRowReRunClick);
    }
    if (action === "Cancel") {
      setRowSelected([]);
      if (tabRef.current) {
        tabRef.current.clearRowSelection();
      }
    } else {
      setOpenDialog(action);
    }
  };

  const refreshGrid = () => {
    if (tabRef.current) {
      tabRef.current.refreshExperimentTab();
    }
  };

  const mutation = useMutation({
    mutationFn: async (data) =>
      axios.post(endpoints.develop.experiment.rerun, data),
    onSuccess: () => {
      setTimeout(refreshGrid, 500);
    },
    onError: (error) => {
      setTimeout(refreshGrid, 500);
      logger.error("Re-run error:", error);
    },
  });

  const _handleRerun = () => {
    const data = { experiment_ids: rowSelected.map((item) => item.id) };
    trackEvent(Events.expRowReRunSuccess, {
      experiment_name: rowSelected.map((item) => item.name),
    });
    enqueueSnackbar("Re-Run has started for the selected experiments", {
      variant: "success",
    });
    mutation.mutate(data);
    closeDialog();
    setTimeout(refreshGrid, 500);
  };

  const _deleteMutation = useMutation({
    mutationFn: async (data) =>
      axios.delete(endpoints.develop.experiment.delete, { data }),
    onSuccess: () => {
      if (tabRef.current) {
        tabRef.current.clearRowSelection();
      }
      refreshGrid();
    },
    onError: (error) => {
      logger.error("Delete error:", error);
    },
  });

  const closeDialog = () => setOpenDialog(null);

  return (
    <Box
      sx={{
        paddingX: theme.spacing(2),
      }}
    >
      <Box
        sx={{
          borderBottom: 1,
          borderColor: "divider",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          minHeight: theme.spacing(3.75),
        }}
      >
        <Box
          sx={{
            padding: theme.spacing(0),
            margin: theme.spacing(0),
          }}
        >
          <Tabs
            value={currentTab}
            onChange={(e, value) => {
              setCurrentTab(value);
              setRowSelected([]);
              // if (value === "optimization") trackEvent(Events.optViewed);
              if (value === "experiments") trackEvent(Events.expViewed);
              if (value === "summary") trackEvent(Events.summaryViewed);
            }}
            TabIndicatorProps={{
              style: { backgroundColor: theme.palette.primary.main },
            }}
            sx={{
              minHeight: 0,
              "& .MuiTab-root": {
                margin: "0 !important",
                fontWeight: "600",
                typography: "s1",
                color: "primary.main",
                "&:not(.Mui-selected)": {
                  color: "text.secondary",
                  fontWeight: "fontWeightRegular",
                },
              },
            }}
          >
            {TabOptions.map((tab) => {
              // If hideScenarioFeatures is true, only show the Data tab
              if (hideScenarioFeatures && tab.value !== "data") {
                return null;
              }
              // Existing logic for compareDataset
              if (
                isCompareDataset &&
                ["Annotations", "Optimization", "Experiments"].includes(
                  tab.label,
                )
              ) {
                return null;
              }
              return (
                <Tab
                  sx={{
                    margin: theme.spacing(0),
                    px: theme.spacing(1.875),
                  }}
                  key={tab.value}
                  label={tab.label}
                  value={tab.value}
                  icon={
                    <SvgColor
                      src={tab.icons}
                      sx={{
                        backgroundColor:
                          tab.value === currentTab
                            ? "primary.main"
                            : "text.secondary",
                        width: "16px",
                        height: "16px",
                      }}
                    />
                  }
                  iconPosition="start"
                />
              );
            })}
          </Tabs>
        </Box>

        {/* {rowSelected.length > 0 && (
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-around",
              border: "1px solid var(--border-default)",
              borderRadius: theme.shape.borderRadius,
              padding: `${theme.spacing(0.5)} ${theme.spacing(2)}`,
              gap: theme.spacing(1),
              marginRight: theme.spacing(0.625),
              backgroundColor: "background.paper",
              marginLeft: "auto",
            }}
          >
            <Box
              sx={{
                color: theme.palette.primary.main,
                marginRight: theme.spacing(1.5),
                fontFamily: "IBM Plex Sans",
                fontWeight: 500,
                fontSize: "14px",
                lineHeight: "22px",
                paddingRight: theme.spacing(3),
                borderRight: "2px solid var(--border-default)",
              }}
            >
              {`${rowSelected.length} selected`}
            </Box>
            {actions.map((action) => (
              <Button
                key={action.name}
                variant="text"
                sx={{
                  height: theme.spacing(3.75),
                  color: "text.secondary",
                  "&:hover": {
                    color: theme.palette.primary.main,
                  },
                  borderRadius: "4px",
                  padding: `${theme.spacing(0.75)} ${theme.spacing(1.25)}`,
                  fontFamily: "IBM Plex Sans",
                  fontWeight: 500,
                  fontSize: "14px",
                  lineHeight: "22px",
                }}
                disabled={action.disabled}
                onClick={() => handleButtonClick(action.name)}
                startIcon={
                  action.name === "Re-Run" ? (
                    <Iconify icon="token:rune" />
                  ) : action.name === "Delete" ? (
                    <Iconify icon="solar:trash-bin-trash-bold" />
                  ) : null
                }
              >
                {action.name}
              </Button>
            ))}
          </Box>
        )} */}
        {/* <ShowComponent condition={currentTab !== "data" || isCompareDataset}>
          {rightSection}
        </ShowComponent> */}

        {/* Delete Confirmation Dialog */}
        {/* <CustomDialog
          open={openDialog === "Delete"}
          onClose={closeDialog}
          title={`Delete Experiment${rowSelected?.length > 1 ? "s" : ""}`}
          actionButton="Delete"
          onClickAction={handleDelete}
          preTitleIcon="solar:trash-bin-trash-bold"
          color="error"
        >
          <Typography
            style={{
              paddingLeft: theme.spacing(3.125),
              color: "var(--text-secondary)",
            }}
          >
            {`Are you sure you want to delete the selected experiment${rowSelected?.length > 1 ? "s" : ""}?`}
          </Typography>
        </CustomDialog>

        {/* Re-Run Confirmation Dialog */}
        {/* <CustomDialog
          open={openDialog === "Re-Run"}
          onClose={closeDialog}
          title={`Re-Run Experiment${rowSelected?.length > 1 ? "s" : ""}`}
          actionButton="Re-Run"
          onClickAction={handleRerun}
          preTitleIcon="token:rune"
          color="primary"
        >
          <Typography
            style={{
              paddingLeft: theme.spacing(3.125),
              color: "var(--text-secondary)",
            }}
          >
            {`Are you sure you want to re-run the selected experiment${rowSelected?.length > 1 ? "s" : ""}? Existing run data will be overwritten`}
          </Typography>
        </CustomDialog>  */}
      </Box>
    </Box>
  );
};

DevelopBar.propTypes = {
  isCompareDataset: PropTypes.bool,
  currentTab: PropTypes.string,
  setCurrentTab: PropTypes.func,
  rowSelected: PropTypes.array.isRequired,
  setRowSelected: PropTypes.func.isRequired,
  rightSection: PropTypes.node,
  tabRef: PropTypes.object.isRequired,
  hideScenarioFeatures: PropTypes.bool,
};

export default DevelopBar;
