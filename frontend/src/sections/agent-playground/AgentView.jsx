import { useQuery, useQueryClient } from "@tanstack/react-query";
import React, { useEffect } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import axios, { endpoints } from "src/utils/axios";
import { LoadingButton } from "@mui/lab";
import Box from "@mui/material/Box";
import EmptyLayout from "src/components/EmptyLayout/EmptyLayout";
import AgentListView from "./AgentListView";
import {
  resetAgentListGridStore,
  useAgentPlaygroundStoreShallow,
} from "./store";
import { useCreateGraph } from "../../api/agent-playground/agent-playground";
import AgentOnboardingFocusPanel from "./components/AgentOnboardingFocusPanel";
import { useRecordActivationEvent } from "src/sections/onboarding-home/hooks/useRecordActivationEvent";
import { agentSetupQuickStartAttributionFromSearch } from "./agentOnboardingEvents";

export default function AgentView() {
  const { data, isLoading } = useQuery({
    queryKey: ["agent-playground", "graphs", { page: 1 }],
    queryFn: () =>
      axios.get(endpoints.agentPlayground.listGraphs, {
        params: { page_number: 1, page_size: 1 },
      }),
  });

  const hasData = data?.data?.result?.graphs?.length > 0;

  useEffect(() => {
    return () => {
      resetAgentListGridStore();
    };
  }, []);

  if (isLoading) return null;

  return hasData ? <AgentListView /> : <AgentEmptyState />;
}

function AgentEmptyState() {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const queryClient = useQueryClient();
  const { mutate: recordActivationEvent } = useRecordActivationEvent();
  const { setCurrentAgent } = useAgentPlaygroundStoreShallow((s) => ({
    setCurrentAgent: s.setCurrentAgent,
  }));
  const showCreateFocus = searchParams.get("onboarding") === "create";
  const tourAnchor = searchParams.get("tour_anchor");
  const quickStartAttribution = agentSetupQuickStartAttributionFromSearch(
    location.search,
  );

  const { mutate: createGraph, isPending } = useCreateGraph({
    navigate,
    onboardingMode: showCreateFocus ? "run-scenario" : null,
    quickStartAttribution,
    recordActivationEvent,
    setCurrentAgent,
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["agent-playground", "graphs"],
      });
    },
  });

  return (
    <Box
      sx={{
        height: "100%",
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        p: 2,
      }}
    >
      <AgentOnboardingFocusPanel
        currentStep="Agent"
        description="Create one agent workflow, then run it once to produce the first reviewable output."
        hidden={!showCreateFocus}
        primaryAction={{
          label: "Create Agent",
          onClick: () => createGraph(),
          disabled: isPending,
        }}
        singleActionFocus={showCreateFocus}
        steps={[
          { label: "Agent", complete: false },
          { label: "Scenario", complete: false },
          { label: "Review", complete: false },
        ]}
        title="Create the first agent"
        tourAnchor={tourAnchor}
      />
      <EmptyLayout
        icon="/assets/icons/navbar/ic_agents.svg"
        title="Create your first agent"
        description="Break down complex tasks into sequential steps that build upon each other."
        sx={{ flex: 1, minHeight: 0 }}
        action={
          showCreateFocus ? null : (
            <LoadingButton
              loading={isPending}
              onClick={() => createGraph()}
              size="small"
              variant="contained"
              color="primary"
            >
              Start creating
            </LoadingButton>
          )
        }
      />
    </Box>
  );
}
