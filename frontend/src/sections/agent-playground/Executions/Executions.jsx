import React, { useState, useCallback, useEffect, useMemo } from "react";
import {
  Box,
  CircularProgress,
  Divider,
  Stack,
  Typography,
} from "@mui/material";
import {
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { useGetExecutions } from "src/api/agent-playground/agent-playground";
import ExecutionsList from "./ExecutionsList";
import ExecutionDetailView from "./ExecutionDetailView";
import AgentOnboardingFocusPanel from "../components/AgentOnboardingFocusPanel";
import {
  agentSetupQuickStartAttributionFromSearch,
  buildAgentBuilderHref,
} from "../agentOnboardingEvents";

export default function Executions() {
  const { agentId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [selectedExecutionId, setSelectedExecutionId] = useState(null);

  const { data, isLoading, isFetchingNextPage, fetchNextPage, hasNextPage } =
    useGetExecutions(agentId);

  const executions = useMemo(
    () =>
      (data?.pages ?? []).flatMap((page) =>
        (page.data?.result?.executions ?? []).map((e) => ({
          id: e.id,
          status: e.status?.toLowerCase(),
          startedAt: e.startedAt,
          completedAt: e.completedAt,
        })),
      ),
    [data],
  );

  const handleExecutionChange = useCallback((executionId) => {
    setSelectedExecutionId(executionId);
  }, []);

  const reviewExecution =
    executions.find((execution) =>
      ["success", "failed", "error"].includes(execution.status),
    ) || executions[0];
  const showReviewFocus = searchParams.get("onboarding") === "review-run";
  const tourAnchor = searchParams.get("tour_anchor");
  const quickStartAttribution = useMemo(
    () => agentSetupQuickStartAttributionFromSearch(location.search),
    [location.search],
  );

  const buildRoute = useCallback(
    (onboardingMode) => {
      return buildAgentBuilderHref({
        agentId,
        onboarding: onboardingMode,
        quickStartAttribution,
        versionId: searchParams.get("version"),
      });
    },
    [agentId, quickStartAttribution, searchParams],
  );

  useEffect(() => {
    const onboardingMode = new URLSearchParams(location.search).get(
      "onboarding",
    );
    if (
      onboardingMode !== "review-run" ||
      selectedExecutionId ||
      executions.length === 0
    ) {
      return;
    }
    setSelectedExecutionId(reviewExecution.id);
  }, [
    executions.length,
    location.search,
    reviewExecution,
    selectedExecutionId,
  ]);

  if (isLoading) {
    return (
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          height: "100%",
        }}
      >
        <CircularProgress size={28} />
      </Box>
    );
  }

  if (executions.length === 0) {
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
          currentStep="Review"
          description="Run the agent once before reviewing node outputs and deciding what to turn into an eval."
          hidden={!showReviewFocus}
          blocker="No run yet"
          primaryAction={{
            label: "Open builder",
            onClick: () => navigate(buildRoute("run-scenario")),
          }}
          steps={[
            { label: "Agent", complete: true },
            { label: "Scenario", complete: false },
            { label: "Review", complete: false },
          ]}
          title="Review the first agent run"
          tourAnchor={tourAnchor}
        />
        <Box
          sx={{
            flex: 1,
            minHeight: 0,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            flexDirection: "column",
            gap: 1,
          }}
        >
          <Typography typography="m3" color="text.disabled">
            No executions yet
          </Typography>
          <Typography typography="s2" color="text.disabled">
            Run your workflow from the Agent Builder to see results here
          </Typography>
        </Box>
      </Box>
    );
  }

  return (
    <Box
      sx={{
        height: "100%",
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
      }}
    >
      <Box sx={{ px: 2, pt: 2 }}>
        <AgentOnboardingFocusPanel
          currentStep="Review"
          description="Inspect the latest agent run, then use the findings to create eval coverage or improve the workflow."
          hidden={!showReviewFocus}
          primaryAction={{
            label: "Review latest run",
            onClick: () => setSelectedExecutionId(reviewExecution.id),
          }}
          secondaryAction={{
            label: "Run another scenario",
            onClick: () => navigate(buildRoute("run-scenario")),
          }}
          steps={[
            { label: "Agent", complete: true },
            { label: "Scenario", complete: true },
            { label: "Review", complete: Boolean(selectedExecutionId) },
          ]}
          title="Review the first agent run"
          tourAnchor={tourAnchor}
        />
      </Box>
      <Stack direction="row" sx={{ flex: 1, minHeight: 0 }}>
        <Box
          sx={{
            width: "230px",
            flexShrink: 0,
            height: "100%",
            display: "flex",
            flexDirection: "column",
          }}
        >
          <ExecutionsList
            executions={executions}
            selectedExecutionId={selectedExecutionId}
            onExecutionChange={handleExecutionChange}
            isFetchingNextPage={isFetchingNextPage}
            fetchNextPage={fetchNextPage}
            hasNextPage={hasNextPage}
          />
        </Box>
        <Divider orientation="vertical" />
        <ExecutionDetailView
          graphId={agentId}
          executionId={selectedExecutionId}
        />
      </Stack>
    </Box>
  );
}
