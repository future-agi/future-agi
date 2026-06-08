import React, { useEffect } from "react";
import { Outlet, useParams, useSearchParams } from "react-router-dom";
import Header from "./components/Header";
import { Divider, Box, CircularProgress } from "@mui/material";
import AgentPlayGroundTabs from "./components/AgentPlayGroundTabs";
import DraftConfirmationDialog from "./components/DraftConfirmationDialog";
import SaveAgentDialog from "./components/SaveAgentDialog";
import ExecutionStopDialog from "./components/ExecutionStopDialog";
import {
  resetAgentPlaygroundStore,
  useAgentPlaygroundStoreShallow,
  resetGlobalVariablesDrawerStore,
  resetWorkflowRunStore,
  resetTemplateLoadingStore,
} from "./store";
// import { useDraftConfirmation } from "./hooks/useDraftConfirmation";
// import { useBeforeUnload } from "src/hooks/useBeforeUnload";
import {
  useGetGraphDetail,
  useGetVersionDetail,
} from "src/api/agent-playground/agent-playground";
import { VERSION_STATUS } from "./utils/constants";

const TabContentLoader = () => (
  <Box
    sx={{
      display: "flex",
      justifyContent: "center",
      alignItems: "center",
      height: "200px",
    }}
  >
    <CircularProgress />
  </Box>
);

const AgentPlaygroundView = React.memo(function AgentPlaygroundView() {
  const { agentId } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const rawUrlVersion = searchParams.get("version");
  // Guard against literal "undefined" string in URL
  const urlVersionId =
    rawUrlVersion && rawUrlVersion !== undefined ? rawUrlVersion : null;

  const { currentAgent, setCurrentAgent } = useAgentPlaygroundStoreShallow(
    (s) => ({
      currentAgent: s.currentAgent,
      setCurrentAgent: s.setCurrentAgent,
    }),
  );

  // Check if current agent is a draft for beforeunload warning
  // const { isDraft } = useDraftConfirmation();
  // useBeforeUnload(
  //   isDraft,
  //   "You have unsaved changes. Are you sure you want to leave?",
  // );

  // Fetch graph detail only when navigating directly (e.g. row click from list).
  // When coming from create, currentAgent is already set in the store so this is skipped.
  const { data: agentData } = useGetGraphDetail(agentId, {
    enabled: !!agentId && !currentAgent,
  });

  // Fetch version detail when URL points to a specific version (needed for correct isDraft/versionName)
  const shouldFetchUrlVersion = !!agentId && !!urlVersionId && !currentAgent;
  const { data: urlVersionData } = useGetVersionDetail(agentId, urlVersionId, {
    enabled: shouldFetchUrlVersion,
  });

  // ONE-TIME initial setup — once currentAgent is set, this never runs again.
  // AgentBuilder owns all subsequent version changes via store.updateVersion().
  useEffect(() => {
    if (!agentData || currentAgent) return;

    if (urlVersionId) {
      // Wait for version detail to arrive for correct metadata
      if (!urlVersionData) return;
      setCurrentAgent({
        ...agentData,
        version_id: urlVersionId,
        version_name: `Version ${urlVersionData.version_number}`,
        is_draft: urlVersionData.status === VERSION_STATUS.DRAFT,
        version_status: urlVersionData.status,
      });
    } else {
      // No URL version — use active version and sync URL
      setCurrentAgent({
        ...agentData,
      });
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          next.set("version", agentData.version_id);
          return next;
        },
        { replace: true },
      );
    }
  }, [
    agentData,
    currentAgent,
    urlVersionId,
    urlVersionData,
    setCurrentAgent,
    setSearchParams,
  ]);

  useEffect(() => {
    return () => {
      resetAgentPlaygroundStore();
      resetGlobalVariablesDrawerStore();
      resetWorkflowRunStore();
      resetTemplateLoadingStore();
    };
  }, []);

  return (
    <>
      <Header />
      <Divider />
      <Box
        sx={{
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <AgentPlayGroundTabs />
      </Box>
      <Box sx={{ flex: 1, overflow: "auto" }}>
        <React.Suspense fallback={<TabContentLoader />}>
          <Outlet />
        </React.Suspense>
      </Box>
      {/* Global dialogs */}
      <DraftConfirmationDialog />
      <SaveAgentDialog />
      <ExecutionStopDialog />
    </>
  );
});

AgentPlaygroundView.displayName = "AgentPlaygroundView";
export default AgentPlaygroundView;
