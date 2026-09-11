import { Box, Button, Divider, Drawer, IconButton, Stack } from "@mui/material";
import PropTypes from "prop-types";
import React from "react";
import Header from "./Header";
import ContentWrapper from "./ContentWrapper";
import {
  resetReplaySessionsStore,
  resetSessionsGridStore,
  useReplaySessionsStoreShallow,
} from "./store";
import SvgColor from "../../../../components/svg-color/svg-color";
import { ConfirmDialog } from "src/components/custom-dialog";
import { DrawerProvider } from "./ReplayDrawerContext";
import { useGetScenarioDetail } from "../../../../api/scenarios/scenarios";
import { getConfirmationMessages } from "./common";
import { useReplayConfiguration } from "./useReplayConfiguration";
import { SCENARIO_STATUS } from "../../../../pages/dashboard/scenarios/common";

const ReplaySessions = ({ isCollapsed, setIsCollapsed, gridApi }) => {
  const config = useReplayConfiguration();
  const {
    expandView,
    openEvaluationDialog,
    isConfirmationModalOpen,
    setIsConfirmationModalOpen,
    openReplaySessionDrawer,
    setOpenReplaySessionDrawer,
    createdScenario,
    currentStep,
    createdReplay,
  } = useReplaySessionsStoreShallow((s) => ({
    expandView: s.expandView,
    openEvaluationDialog: s.openEvaluationDialog,
    isConfirmationModalOpen: s.isConfirmationModalOpen,
    setIsConfirmationModalOpen: s.setIsConfirmationModalOpen,
    createdScenario: s.createdScenario,
    openReplaySessionDrawer: s.openReplaySessionDrawer,
    setOpenReplaySessionDrawer: s.setOpenReplaySessionDrawer,
    currentStep: s.currentStep,
    createdReplay: s.createdReplay,
  }));

  const open = Object.values(openReplaySessionDrawer).some((val) => val);

  // Fetch scenarioDetail in parent component for use in confirmation dialog
  // The select function returns d.data, so scenarioDetail will be the data object
  const { data: scenarioDetail, isLoading: isScenarioDetailLoading } =
    useGetScenarioDetail(createdScenario?.scenario_id, {
      enabled: !!createdScenario?.scenario_id && currentStep === 1,
    });

  const handleCloseReplaySessionDrawer = () => {
    setOpenReplaySessionDrawer(config?.module, false);
  };

  const handleClose = () => {
    handleCloseReplaySessionDrawer();
    resetReplaySessionsStore();
    resetSessionsGridStore();
  };

  const handleConfirmClose = () => {
    handleClose();
    setIsConfirmationModalOpen(false);
    gridApi?.deselectAll();
  };

  const handleCancelClose = () => {
    setIsConfirmationModalOpen(false);
  };

  const handleCloseDrawer = () => {
    setIsConfirmationModalOpen(true);
  };

  const confirmationMessages = getConfirmationMessages(scenarioDetail?.status);

  return (
    <>
      <Drawer
        open={open}
        onClose={handleCloseDrawer}
        anchor="right"
        hideBackdrop={true}
        variant="persistent"
        PaperProps={{
          sx: {
            height: "100vh",
            position: "fixed",
            zIndex: 110,
            boxShadow: "none",
            borderRadius: "0px !important",
            borderLeft: "1px solid",
            borderColor: "divider",
            backgroundColor: "background.paper",
            display: "flex",
            overflow: "visible",
            minWidth:
              openEvaluationDialog || isCollapsed
                ? "0px"
                : expandView
                  ? "97%"
                  : "800px",
            maxWidth:
              openEvaluationDialog || isCollapsed
                ? "0px"
                : expandView
                  ? "97%"
                  : "800px",
            padding: isCollapsed ? "0px" : 2,
            transition:
              "min-width 300ms ease-in-out, max-width 300ms ease-in-out",
          },
        }}
        ModalProps={{
          style: {
            pointerEvents: "none",
          },
          BackdropProps: {
            style: {
              backgroundColor: "transparent",
              boxShadow: "none",
            },
          },
        }}
        sx={{
          "& .MuiDrawer-paper": {
            pointerEvents: "auto",
          },
        }}
      >
        <DrawerProvider
          value={{
            gridApi,
          }}
        >
          <IconButton
            sx={{
              bgcolor: "background.paper",
              border: "1px solid",
              borderColor: "divider",
              padding: 0.75,
              flexShrink: 0,
              position: "absolute",
              top: "48px",
              left: "-29px",
              borderRight: 0,
              borderRadius: "4px 0px 0px 4px",
              "&:hover": {
                borderRight: "none",
                bgcolor: "background.default",
              },
            }}
            onClick={() => setIsCollapsed(config?.module, !isCollapsed)}
          >
            <SvgColor
              // @ts-ignore
              src={"/assets/icons/custom/lucide--chevron-right.svg"}
              sx={{
                width: "16px",
                height: "16px",
                color: "text.disabled",
                transform: !isCollapsed ? "rotate(0deg)" : "rotate(180deg)",
                transition: "transform 0.3s ease-in-out",
              }}
            />
          </IconButton>
          <Stack
            gap={2}
            sx={{
              display: "flex",
              flexDirection: "column",
              height: "100%",
              overflow: "hidden",
            }}
          >
            <Box sx={{ flexShrink: 0 }}>
              <Header onClose={handleCloseDrawer} />
            </Box>
            <Divider sx={{ flexShrink: 0 }} />
            <Box
              sx={{
                flex: 1,
                minHeight: 0,
                display: "flex",
                flexDirection: "column",
                pb: 1,
              }}
            >
              {createdReplay && (
                <ContentWrapper
                  scenarioDetail={scenarioDetail}
                  isScenarioDetailLoading={isScenarioDetailLoading}
                  createdReplay={createdReplay}
                />
              )}
            </Box>
          </Stack>
        </DrawerProvider>
      </Drawer>
      <ConfirmDialog
        content={confirmationMessages.content}
        action={
          <Button
            variant="contained"
            color="error"
            size="small"
            onClick={handleConfirmClose}
            sx={{ paddingX: (theme) => theme.spacing(3) }}
          >
            {scenarioDetail?.status === SCENARIO_STATUS.PROCESSING
              ? "Close"
              : "Discard Changes"}
          </Button>
        }
        open={isConfirmationModalOpen}
        onClose={handleCancelClose}
        title={confirmationMessages.title}
        message={confirmationMessages.message}
      />
    </>
  );
};

export default ReplaySessions;

ReplaySessions.propTypes = {
  isCollapsed: PropTypes.bool.isRequired,
  setIsCollapsed: PropTypes.func.isRequired,
  gridApi: PropTypes.object.isRequired,
  config: PropTypes.object, // Optional configuration object
};
