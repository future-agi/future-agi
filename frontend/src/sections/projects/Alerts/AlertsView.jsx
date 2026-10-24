import React, { lazy, Suspense, useEffect } from "react";
import { Box } from "@mui/material";
import { useAlertStore } from "./store/useAlertStore";
import { useParams } from "react-router";
import {
  AlertTableSkeleton,
  EmptyAlertsSkeleton,
} from "./components/AlertSkeletons";

// Eagerly load always-visible components
import SelectProject from "src/sections/alerts/component/SelectProject";
import AlertsListView from "./components/AlertsListView/AlertsListView";
import EmptyAlerts from "./components/EmptyAlerts";
import PageHeader from "src/components/page-header";

// Lazy load modals/drawers (opened on demand)
const ConfirmAlertsDialog = lazy(
  () => import("./components/ConfirmAlertsDialog"),
);
const CreateAlertsDrawer = lazy(
  () => import("./components/CreateAlertsDrawer"),
);

export default function AlertsView() {
  const {
    openCreateAlerts,
    handleCloseCreateAlert,
    handleStartCreatingAlerts,
    mainPage,
    selectedProject,
    handleProjectChange,
    openSelectProjectModal,
    handleCloseProjectModal,
    handleOpenProjectModal,
    hasData,
    setCurrentTab,
    setHasData,
    initializeWithObserveId,
  } = useAlertStore();
  const { observeId } = useParams();

  useEffect(() => {
    if (observeId) {
      initializeWithObserveId(observeId);
    }
    return () => {
      setHasData(true);
    };
  }, [initializeWithObserveId, observeId, selectedProject, setHasData]);

  return (
    <Box
      sx={{
        bgcolor: mainPage ? "background.paper" : "transparent",
        height: "100%",
        p: mainPage ? 2 : 0,
        display: "flex",
        flexDirection: "column",
        gap: 1.5,
        overflow: "hidden",
        minHeight: 0,
      }}
    >
      {mainPage && (
        <PageHeader
          title="Alerts"
          description="Proactively monitor your LLM by setting up alerts"
          sx={{ mb: 0 }}
        />
      )}
      {!hasData ? (
        <Suspense fallback={<EmptyAlertsSkeleton mainPage={mainPage} />}>
          <EmptyAlerts
            mainPage={mainPage}
            onStartCreatingAlerts={() => {
              if (mainPage) {
                handleOpenProjectModal();
              } else {
                setCurrentTab(0);
                handleStartCreatingAlerts();
              }
            }}
          />
        </Suspense>
      ) : (
        <Suspense fallback={<AlertTableSkeleton />}>
          <AlertsListView />
        </Suspense>
      )}
      <Suspense>
        <CreateAlertsDrawer
          onClose={handleCloseCreateAlert}
          open={openCreateAlerts}
        />
      </Suspense>
      <Suspense>
        <ConfirmAlertsDialog />
      </Suspense>
      <Suspense>
        <SelectProject
          open={openSelectProjectModal}
          onChange={handleProjectChange}
          value={selectedProject}
          onClose={handleCloseProjectModal}
          onAction={() => {
            setCurrentTab(0);
            setTimeout(() => {
              handleCloseProjectModal();
              handleStartCreatingAlerts();
            }, 100);
          }}
        />
      </Suspense>
    </Box>
  );
}
