import React, { useEffect, useMemo, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuthContext } from "src/auth/hooks";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import { useActivationState } from "./hooks/useActivationState";
import { useRecordActivationEvent } from "./hooks/useRecordActivationEvent";
import { useSaveOnboardingGoal } from "./hooks/useSaveOnboardingGoal";
import { useSampleProject } from "./hooks/useSampleProject";
import {
  getGoalOptionsForState,
  getStageCopy,
  readableToken,
} from "./onboarding-home.constants";
import {
  OnboardingHomeEvents,
  trackOnboardingHomeEvent,
} from "./analytics/onboarding-events";
import DailyQualityHome from "./components/DailyQualityHome";
import FirstLoopCompletePanel from "./components/FirstLoopCompletePanel";
import FirstSignalPanel from "./components/FirstSignalPanel";
import GoalPicker from "./components/GoalPicker";
import ObserveDiagnosticsPanel from "./components/ObserveDiagnosticsPanel";
import ObserveSetupPanel from "./components/ObserveSetupPanel";
import OnboardingHomeError from "./components/OnboardingHomeError";
import OnboardingHomeSkeleton from "./components/OnboardingHomeSkeleton";
import PathCardGrid from "./components/PathCardGrid";
import ProductLoopStepper from "./components/ProductLoopStepper";
import RecommendedActionCard from "./components/RecommendedActionCard";
import SampleProjectPanel from "./components/SampleProjectPanel";
import WaitingForSignalPanel from "./components/WaitingForSignalPanel";

function Diagnostics({ state }) {
  if (!state?.featureFlags?.onboarding_debug || !state?.diagnostics) {
    return null;
  }

  return (
    <Box
      data-testid="onboarding-diagnostics"
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        p: 2,
      }}
    >
      <Typography variant="subtitle2">Diagnostics</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
        {state.diagnostics.decisionReason || "No decision reason returned."}
      </Typography>
    </Box>
  );
}

Diagnostics.propTypes = {
  state: PropTypes.object,
};

const mutationPending = (mutation) =>
  Boolean(mutation?.isPending || mutation?.isLoading);

const OBSERVE_PANEL_STAGES = new Set([
  "connect_observability",
  "waiting_for_first_trace",
  "waiting_for_first_trace_sample_available",
  "review_first_trace",
  "create_trace_evaluator",
  "activated",
  "daily_review",
]);

export default function OnboardingHomeView() {
  const { user } = useAuthContext();
  const {
    currentWorkspaceId,
    currentWorkspaceDisplayName,
    isReady: workspaceReady,
  } = useWorkspace();
  const location = useLocation();
  const navigate = useNavigate();
  const recordActivationEvent = useRecordActivationEvent();
  const saveGoal = useSaveOnboardingGoal();
  const sampleProjectActions = useSampleProject();
  const [selectedGoal, setSelectedGoal] = useState(null);

  const searchContext = useMemo(() => {
    const params = new URLSearchParams(location.search);
    return {
      source: params.get("source") || "home",
      campaignKey: params.get("campaign_key"),
      emailKey: params.get("email_key"),
      targetStage: params.get("target_stage"),
      targetEvent: params.get("target_event"),
      targetRoute: params.get("target_route"),
      linkIssuedAt: params.get("link_issued_at"),
      mode: params.get("mode"),
    };
  }, [location.search]);

  const workspaceId = currentWorkspaceId || user?.default_workspace_id || null;
  const organizationId =
    user?.organization?.id || user?.organization_id || null;
  const waitingForWorkspace =
    Boolean(user?.default_workspace_id) && !workspaceId && !workspaceReady;

  const { state, isLoading, isRefetching, isError, error, refetch } =
    useActivationState({
      organizationId,
      workspaceId,
      ...searchContext,
      enabled: Boolean(user) && !waitingForWorkspace,
      requireWorkspaceContext: false,
    });

  const renderedState = saveGoal.data || state;
  const goalOptions = useMemo(
    () => getGoalOptionsForState(renderedState),
    [renderedState],
  );
  const trackContext = useMemo(() => {
    if (!renderedState) return null;
    const recommendedAction = renderedState.recommendedAction;
    return {
      request_id: renderedState.requestId,
      stage: renderedState.stage,
      activation_stage: renderedState.stage,
      goal: renderedState.goal,
      selected_goal: renderedState.goal,
      primary_path: renderedState.primaryPath,
      workspace_id: renderedState.workspaceId || workspaceId,
      organization_id: renderedState.organizationId || organizationId,
      user_id: renderedState.userId || user?.id,
      source: searchContext.source,
      recommended_action_id: recommendedAction?.id,
      target_success_event: recommendedAction?.completionEvent,
      feature_flag_variant:
        renderedState.featureFlags?.onboarding_activation_state_api === false
          ? "activation_api_off"
          : "activation_api_on",
      is_sample: false,
      permission_limited: Boolean(renderedState.permissions?.permissionLimited),
      route_available: recommendedAction?.routeAvailable,
    };
  }, [
    organizationId,
    renderedState,
    searchContext.source,
    user?.id,
    workspaceId,
  ]);
  const dailyTrackContext = useMemo(() => {
    const dailyQuality = renderedState?.dailyQuality;
    if (!trackContext || !dailyQuality) return null;
    const topSignal = dailyQuality.topSignal;
    const primaryAction = dailyQuality.primaryAction;

    return {
      ...trackContext,
      home_mode: renderedState.homeMode,
      daily_quality_mode: dailyQuality.mode,
      signal_id: topSignal?.id,
      signal_type: topSignal?.type,
      source_type: topSignal?.sourceType || primaryAction?.sourceType,
      source_id: topSignal?.sourceId || primaryAction?.sourceId,
      recommended_action_id:
        primaryAction?.id || renderedState.recommendedAction?.id,
      route: primaryAction?.route || topSignal?.route,
      route_available:
        primaryAction?.routeAvailable ??
        renderedState.recommendedAction?.routeAvailable,
      is_sample: Boolean(topSignal?.isSample || primaryAction?.isSample),
      digest_context_id: searchContext.campaignKey,
      feature_flag_state: renderedState.featureFlags
        ?.onboarding_daily_quality_home
        ? "on"
        : "off",
    };
  }, [
    renderedState?.dailyQuality,
    renderedState?.featureFlags?.onboarding_daily_quality_home,
    renderedState?.homeMode,
    renderedState?.recommendedAction?.id,
    renderedState?.recommendedAction?.routeAvailable,
    searchContext.campaignKey,
    trackContext,
  ]);

  useEffect(() => {
    setSelectedGoal(renderedState?.goal || null);
  }, [renderedState?.goal]);

  useEffect(() => {
    if (!trackContext || isError) return;
    trackOnboardingHomeEvent(OnboardingHomeEvents.homeViewed, trackContext);
  }, [isError, trackContext]);

  useEffect(() => {
    if (!trackContext || isError || !renderedState?.recommendedAction) return;
    const action = renderedState.recommendedAction;
    trackOnboardingHomeEvent(OnboardingHomeEvents.recommendedActionViewed, {
      ...trackContext,
      action_id: action.id,
      action_kind: action.kind,
      action_path: action.analytics?.targetPath,
      is_sample: action.isSample,
      completion_event: action.completionEvent,
      route_available: action.routeAvailable,
    });
  }, [isError, renderedState?.recommendedAction, trackContext]);

  useEffect(() => {
    if (!dailyTrackContext || isError) return;
    const dailyQuality = renderedState?.dailyQuality;
    trackOnboardingHomeEvent(
      OnboardingHomeEvents.dailyQualityHomeViewed,
      dailyTrackContext,
    );
    if (dailyQuality?.topSignal) {
      trackOnboardingHomeEvent(
        OnboardingHomeEvents.dailyQualityTopSignalShown,
        dailyTrackContext,
      );
    } else {
      trackOnboardingHomeEvent(
        OnboardingHomeEvents.dailyQualityEmptyStateViewed,
        dailyTrackContext,
      );
    }
    if (
      searchContext.mode === "daily-quality" ||
      searchContext.source === "onboarding_email"
    ) {
      trackOnboardingHomeEvent(
        OnboardingHomeEvents.dailyQualityDigestDestinationOpened,
        dailyTrackContext,
      );
    }
  }, [
    dailyTrackContext,
    isError,
    renderedState?.dailyQuality,
    searchContext.mode,
    searchContext.source,
  ]);

  if (isLoading || waitingForWorkspace || (!renderedState && !isError)) {
    return <OnboardingHomeSkeleton />;
  }

  if (isError) {
    return <OnboardingHomeError error={error} onRetry={refetch} />;
  }

  const copy = getStageCopy(renderedState);
  const showGoalPicker =
    renderedState.stage === "choose_goal" &&
    renderedState.featureFlags?.onboarding_goal_picker !== false;
  const isSavingGoal = mutationPending(saveGoal);

  const handleSelectGoal = (option) => {
    setSelectedGoal(option.goal);
    trackOnboardingHomeEvent(OnboardingHomeEvents.homeGoalSelected, {
      ...trackContext,
      selected_goal: option.goal,
      selected_path: option.primaryPath,
    });
  };

  const handleSaveGoal = async (option) => {
    if (!option) return;

    try {
      const nextState = await saveGoal.mutateAsync({
        goal: option.goal,
        primaryPath: option.primaryPath,
        source: "goal_picker",
        reason: renderedState.goal ? "path_change" : "first_selection",
        expectedStage: renderedState.stage,
      });
      trackOnboardingHomeEvent(OnboardingHomeEvents.homeGoalSaved, {
        ...trackContext,
        selected_goal: option.goal,
        selected_path: option.primaryPath,
        next_stage: nextState.stage,
      });
      refetch?.();
    } catch (mutationError) {
      trackOnboardingHomeEvent(OnboardingHomeEvents.homeGoalSaveFailed, {
        ...trackContext,
        selected_goal: option.goal,
        selected_path: option.primaryPath,
        reason:
          mutationError?.result?.reason ||
          mutationError?.message ||
          "unknown_error",
      });
    }
  };

  const handleActionClick = (action) => {
    if (!action) return;
    trackOnboardingHomeEvent(OnboardingHomeEvents.recommendedActionClicked, {
      ...trackContext,
      action_id: action.id,
      action_kind: action.kind,
      action_path: action.analytics?.targetPath,
      is_sample: action.isSample,
      completion_event: action.completionEvent,
    });
  };

  const handleDailyActionClick = (action, dailyAction) => {
    handleActionClick(action);
    trackOnboardingHomeEvent(OnboardingHomeEvents.dailyQualityActionOpened, {
      ...dailyTrackContext,
      recommended_action_id: dailyAction?.id || action?.id,
      route: dailyAction?.route || action?.href,
      route_available: dailyAction?.routeAvailable ?? action?.routeAvailable,
    });
    if (dailyAction && !dailyAction.routeAvailable) {
      trackOnboardingHomeEvent(
        OnboardingHomeEvents.dailyQualityRouteFallbackUsed,
        {
          ...dailyTrackContext,
          recommended_action_id: dailyAction.id,
          route: dailyAction.fallbackRoute,
          route_available: false,
        },
      );
    }
  };

  const handleDailySignalReview = (signal, dailyAction) => {
    trackOnboardingHomeEvent(OnboardingHomeEvents.dailyQualityItemReviewed, {
      ...dailyTrackContext,
      signal_id: signal.id,
      signal_type: signal.type,
      source_type: signal.sourceType,
      source_id: signal.sourceId,
      recommended_action_id: dailyAction?.id,
      route: dailyAction?.route || signal.route,
      is_sample: signal.isSample,
    });
    recordActivationEvent.mutate?.({
      eventName: "daily_quality_item_reviewed",
      primaryPath: renderedState.primaryPath,
      stage: renderedState.stage,
      source: "daily_quality_home",
      artifactType: signal.sourceType,
      artifactId: signal.sourceId,
      projectId: signal.projectId,
      isSample: signal.isSample,
      metadata: {
        signal_id: signal.id,
        signal_type: signal.type,
        source_type: signal.sourceType,
        source_id: signal.sourceId,
        recommended_action_id: dailyAction?.id,
        route: dailyAction?.route || signal.route,
        daily_quality_mode: renderedState.dailyQuality?.mode,
      },
    });
  };

  const handlePathClick = (path) => {
    trackOnboardingHomeEvent(OnboardingHomeEvents.homePathClicked, {
      ...trackContext,
      path_id: path.id,
      path_status: path.status,
    });
  };

  const handleOpenSample = async () => {
    trackOnboardingHomeEvent(OnboardingHomeEvents.sampleProjectOpenClicked, {
      ...trackContext,
      sample_status: renderedState.sampleProject?.status,
      manifest_id: renderedState.sampleProject?.manifestId,
      manifest_version: renderedState.sampleProject?.manifestVersion,
    });
    try {
      const nextState =
        await sampleProjectActions.openSampleProject.mutateAsync({
          path: "observe",
          source: "onboarding_home",
          reason: renderedState.stage,
          manifestId: renderedState.sampleProject?.manifestId,
          manifestVersion: renderedState.sampleProject?.manifestVersion,
          openAfterCreate: true,
        });
      const entryRoute =
        nextState?.sampleProject?.entryRoute ||
        nextState?.sampleProject?.entryRoutes?.[0];
      if (entryRoute) {
        navigate(entryRoute);
      }
      refetch?.();
    } catch (sampleError) {
      trackOnboardingHomeEvent(OnboardingHomeEvents.sampleProjectOpenFailed, {
        ...trackContext,
        sample_status: renderedState.sampleProject?.status,
        reason: sampleError?.message || "unknown_error",
      });
    }
  };

  const handleHideSample = async () => {
    trackOnboardingHomeEvent(OnboardingHomeEvents.sampleProjectHideClicked, {
      ...trackContext,
      sample_status: renderedState.sampleProject?.status,
    });
    await sampleProjectActions.hideSampleProject.mutateAsync({
      source: "onboarding_home",
      reason: "user_dismissed",
    });
    refetch?.();
  };

  const handleConnectRealData = () => {
    trackOnboardingHomeEvent(OnboardingHomeEvents.sampleToRealSetupClicked, {
      ...trackContext,
      sample_status: renderedState.sampleProject?.status,
    });
  };

  const dailyQualityPanel =
    renderedState.homeMode === "daily_quality" && renderedState.dailyQuality ? (
      <DailyQualityHome
        dailyQuality={renderedState.dailyQuality}
        recommendedAction={renderedState.recommendedAction}
        onActionClick={handleDailyActionClick}
        onSignalReview={handleDailySignalReview}
        canAct={!renderedState.permissions?.permissionLimited}
      />
    ) : null;

  const observePanelProps = {
    action: renderedState.recommendedAction,
    fallbackAction: renderedState.fallbackAction,
    onPrimaryClick: handleActionClick,
    onFallbackClick: handleActionClick,
    onCheckAgain: refetch,
    isChecking: Boolean(isRefetching),
  };

  const observePanel =
    renderedState.primaryPath === "observe" &&
    OBSERVE_PANEL_STAGES.has(renderedState.stage) ? (
      <>
        {renderedState.stage === "connect_observability" ? (
          <ObserveSetupPanel {...observePanelProps} />
        ) : null}
        {[
          "waiting_for_first_trace",
          "waiting_for_first_trace_sample_available",
        ].includes(renderedState.stage) ? (
          <WaitingForSignalPanel
            {...observePanelProps}
            signals={renderedState.signals}
          />
        ) : null}
        {["review_first_trace", "create_trace_evaluator"].includes(
          renderedState.stage,
        ) ? (
          <FirstSignalPanel
            {...observePanelProps}
            signals={renderedState.signals}
            stage={renderedState.stage}
          />
        ) : null}
        {["activated", "daily_review"].includes(renderedState.stage)
          ? dailyQualityPanel || (
              <FirstLoopCompletePanel
                {...observePanelProps}
                lastMeaningfulEvent={renderedState.lastMeaningfulEvent}
              />
            )
          : null}
      </>
    ) : null;

  const sampleProject = renderedState.sampleProject;
  const showSamplePanel =
    sampleProject?.available &&
    !sampleProject?.isHidden &&
    !renderedState.isActivated &&
    !showGoalPicker &&
    [
      "connect_observability",
      "waiting_for_first_trace_sample_available",
    ].includes(renderedState.stage);

  return (
    <Box
      data-testid="onboarding-home-view"
      sx={{
        width: "100%",
        minHeight: "calc(100vh - 120px)",
        bgcolor: "background.paper",
        p: { xs: 2, md: 3 },
      }}
    >
      <Stack spacing={3} sx={{ maxWidth: 1180, mx: "auto" }}>
        <Stack spacing={1}>
          <Stack
            direction="row"
            spacing={1}
            alignItems="center"
            flexWrap="wrap"
          >
            <Chip size="small" label={copy.eyebrow} />
            {renderedState.isActivated ? (
              <Chip size="small" color="success" label="Activated" />
            ) : null}
          </Stack>
          <Typography variant="h3">{copy.title}</Typography>
          <Typography variant="body1" color="text.secondary" maxWidth={760}>
            {copy.description}
          </Typography>
          {currentWorkspaceDisplayName ? (
            <Typography variant="body2" color="text.secondary">
              Workspace: {currentWorkspaceDisplayName}
            </Typography>
          ) : null}
        </Stack>

        {renderedState.stage === "feature_disabled" ? (
          <Alert severity="info" sx={{ borderRadius: 1 }}>
            The existing setup checklist is available for this workspace.
          </Alert>
        ) : null}

        {showGoalPicker ? (
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: { xs: "1fr", md: "minmax(0, 2fr) 1fr" },
              gap: 2,
              alignItems: "stretch",
            }}
          >
            <GoalPicker
              goals={goalOptions}
              selectedGoal={selectedGoal}
              onSelectGoal={handleSelectGoal}
              onSaveGoal={handleSaveGoal}
              skipHref={renderedState.fallbackAction?.href}
              isSaving={isSavingGoal}
              error={saveGoal.error}
            />
            <RecommendedActionCard
              action={renderedState.fallbackAction}
              label="Fallback"
              variant="fallback"
              onActionClick={handleActionClick}
            />
          </Box>
        ) : observePanel ? (
          <Stack spacing={2}>
            {observePanel}
            {showSamplePanel ? (
              <SampleProjectPanel
                sampleProject={sampleProject}
                activationStage={renderedState.stage}
                selectedGoal={renderedState.goal}
                onOpenSample={handleOpenSample}
                onHideSample={handleHideSample}
                onConnectRealData={handleConnectRealData}
                isOpening={mutationPending(
                  sampleProjectActions.openSampleProject,
                )}
                isHiding={mutationPending(
                  sampleProjectActions.hideSampleProject,
                )}
              />
            ) : null}
          </Stack>
        ) : dailyQualityPanel ? (
          <Stack spacing={2}>{dailyQualityPanel}</Stack>
        ) : (
          <Box
            sx={{
              display: "grid",
              gridTemplateColumns: { xs: "1fr", md: "minmax(0, 2fr) 1fr" },
              gap: 2,
              alignItems: "stretch",
            }}
          >
            <RecommendedActionCard
              action={renderedState.recommendedAction}
              label="Recommended action"
              onActionClick={handleActionClick}
            />
            <RecommendedActionCard
              action={renderedState.fallbackAction}
              label="Fallback"
              variant="fallback"
              onActionClick={handleActionClick}
            />
          </Box>
        )}

        <Box
          sx={{
            border: "1px solid",
            borderColor: "divider",
            borderRadius: 1,
            p: 2,
          }}
        >
          <Stack direction={{ xs: "column", md: "row" }} spacing={2}>
            <Box sx={{ flex: 1 }}>
              <Typography variant="subtitle2">Current stage</Typography>
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ mt: 0.5, textTransform: "capitalize" }}
              >
                {readableToken(renderedState.stage)}
              </Typography>
            </Box>
            <Box sx={{ flex: 1 }}>
              <Typography variant="subtitle2">Selected path</Typography>
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ mt: 0.5, textTransform: "capitalize" }}
              >
                {readableToken(renderedState.primaryPath)}
              </Typography>
            </Box>
            <Box sx={{ flex: 1 }}>
              <Typography variant="subtitle2">Goal</Typography>
              <Typography
                variant="body2"
                color="text.secondary"
                sx={{ mt: 0.5, textTransform: "capitalize" }}
              >
                {readableToken(renderedState.goal)}
              </Typography>
            </Box>
          </Stack>
        </Box>

        <ProductLoopStepper progress={renderedState.progress} />
        {observePanel ? (
          <ObserveDiagnosticsPanel signals={renderedState.signals} />
        ) : null}
        <PathCardGrid
          paths={renderedState.availablePaths}
          onPathClick={handlePathClick}
        />
        <Diagnostics state={renderedState} />
      </Stack>
    </Box>
  );
}
