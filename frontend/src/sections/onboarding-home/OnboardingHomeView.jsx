import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import Box from "@mui/material/Box";
import Chip from "@mui/material/Chip";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuthContext } from "src/auth/hooks";
import { useWorkspace } from "src/contexts/WorkspaceContext";
import {
  appendSetupQuickStartAttributionToHref,
  normalizeSetupQuickStartAttribution,
  persistSetupQuickStartAttribution,
  readPersistedSetupQuickStartAttribution,
  SETUP_ORG_PRODUCT_LOOP_QUICK_STARTS,
} from "src/sections/auth/jwt/setup-org-quick-starts";
import { shouldShowSampleAsPrimary } from "./activation-state-utils";
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
import PathFocusPanel from "./components/PathFocusPanel";
import {
  hrefWithJourneyGuide,
  journeyCurrentStep,
} from "./components/journey-guide-utils";
import { observeFallbackJourneyPlan } from "./components/observe-fallback-journey-plan";
import {
  hasPathFocusPlan,
  PATH_FOCUS_PLANS,
} from "./components/path-focus-plan";
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

const compactEventMetadata = (metadata = {}) =>
  Object.fromEntries(
    Object.entries(metadata).filter(
      ([, value]) => value !== undefined && value !== null && value !== "",
    ),
  );

const activationPayloadEmailContext = (context = {}) =>
  compactEventMetadata({
    campaignKey: context.campaign_key ?? context.campaignKey,
    emailKey: context.email_key ?? context.emailKey,
    sendLogId: context.send_log_id ?? context.sendLogId,
    emailStatus: context.email_status ?? context.emailStatus,
    targetStage: context.target_stage ?? context.targetStage,
    targetEvent: context.target_event ?? context.targetEvent,
    linkIssuedAt: context.link_issued_at ?? context.linkIssuedAt,
    staleReason: context.stale_reason ?? context.staleReason,
    contextStatus: context.context_status ?? context.contextStatus,
  });

const activationPayloadAttributionContext = (context = {}) =>
  compactEventMetadata({
    ...activationPayloadEmailContext(context),
    ...normalizeSetupQuickStartAttribution({
      quickStartGoal: context.quick_start_goal ?? context.quickStartGoal,
      quickStartId: context.quick_start_id ?? context.quickStartId,
      quickStartPrimaryPath:
        context.quick_start_primary_path ?? context.quickStartPrimaryPath,
    }),
  });

const actionWithSetupQuickStartAttribution = (action, context) => {
  if (!action?.href) return action;
  const href = appendSetupQuickStartAttributionToHref(action.href, context);
  return href === action.href ? action : { ...action, href };
};

const SETUP_QUICK_START_ERROR_FALLBACKS = {
  agent: {
    description: "Create one agent, run a scenario, and review the result.",
    href: "/dashboard/agents?onboarding=create&tour_anchor=agent_create_button&journey_step=create_agent",
    label: "Create agent",
    title: "Prototype agent",
  },
  evals: {
    description: "Create a small eval or simulation and review failures.",
    href: "/dashboard/evaluations/create?source=onboarding&step=dataset&tour_anchor=eval_dataset_button&journey_step=create_eval_dataset",
    label: "Create dataset",
    title: "Test AI using simulation",
  },
  gateway: {
    description: "Add a provider, create a key, and send one gateway request.",
    href: "/dashboard/gateway/providers?source=onboarding&tour_anchor=gateway_provider_button&journey_step=configure_gateway_provider",
    label: "Add provider",
    title: "Set up gateway",
  },
  observe: {
    description:
      "Create an Observe project, send one trace, and review the first signal.",
    href: "/dashboard/observe?setup=true&source=onboarding&tour_anchor=observe_create_project_button&journey_step=connect_observability",
    label: "Create Observe project",
    title: "Connect your agent",
  },
  prompt: {
    description: "Create one prompt, run a test, and compare the next version.",
    href: "/dashboard/workbench/all?source=onboarding&action=create-prompt&tour_anchor=prompt_create_button&journey_step=start_prompt",
    label: "Create prompt",
    title: "Test prompts or agent prompts",
  },
  sample_preview: {
    description:
      "The sample preview did not load. Continue with real observability setup.",
    href: "/dashboard/observe?setup=true&source=onboarding&tour_anchor=observe_create_project_button&journey_step=connect_observability",
    label: "Connect observability",
    title: "Continue with real setup",
  },
  voice: {
    description: "Create or connect one voice agent and run a test call.",
    href: "/dashboard/simulate/agent-definitions/create-new-agent-definition?source=onboarding&onboarding=create-voice-agent&tour_anchor=voice_agent_button&journey_step=create_voice_agent",
    label: "Create agent",
    title: "Connect voice agent",
  },
};

const SETUP_QUICK_START_FIRST_STAGE_BY_PATH = {
  agent: "create_agent",
  evals: "create_eval_dataset",
  gateway: "configure_gateway_provider",
  observe: "connect_observability",
  prompt: "start_prompt",
  voice: "create_voice_agent",
};

const setupQuickStartFirstStage = (primaryPath) =>
  SETUP_QUICK_START_FIRST_STAGE_BY_PATH[primaryPath] || null;

const setupQuickStartErrorFallbackAction = (searchContext = {}) => {
  if (searchContext.source !== "setup_org") return null;
  const attribution = normalizeSetupQuickStartAttribution(searchContext);
  const fallback = SETUP_QUICK_START_ERROR_FALLBACKS[attribution.quickStartId];
  if (!fallback) return null;
  return {
    ...fallback,
    href: appendSetupQuickStartAttributionToHref(fallback.href, attribution),
  };
};

const setupQuickStartFallbackRecommendedAction = (
  fallback,
  searchContext = {},
) => {
  if (!fallback) return null;
  return {
    id: `setup_quick_start_${searchContext.quickStartId || "fallback"}`,
    kind: "setup",
    title: fallback.title,
    description: fallback.description,
    href: fallback.href,
    ctaLabel: fallback.label,
    estimatedMinutes: null,
    priority: 100,
    blocked: false,
    blockedReason: null,
    requiresPermission: null,
    completionEvent: null,
    isSample: false,
    routeAvailable: true,
    fallbackHref: fallback.href,
    analytics: {
      eventName: "onboarding_recommended_action_clicked",
      source: "setup_org",
      targetPath: searchContext.quickStartPrimaryPath || null,
    },
  };
};

const OBSERVE_PANEL_STAGES = new Set([
  "connect_observability",
  "waiting_for_first_trace",
  "waiting_for_first_trace_sample_available",
  "review_first_trace",
  "create_trace_evaluator",
  "activated",
  "daily_review",
]);

const SAMPLE_PRIMARY_STAGES = new Set([
  "open_sample_project",
  "review_sample_signal",
  "connect_real_data",
]);

const SAMPLE_CONNECT_REAL_DATA_STEP = {
  id: "connect_real_data",
  stage: "connect_real_data",
  tourAnchor: "sample_connect_real_data_button",
};

const hrefWithParams = (href, values = {}) => {
  if (!href || !href.startsWith("/") || href.startsWith("//")) return href;

  const [withoutHash, hash] = href.split("#");
  const [pathname, query = ""] = withoutHash.split("?");
  const params = new URLSearchParams(query);

  Object.entries(values).forEach(([key, value]) => {
    if (value === null || value === undefined) {
      params.delete(key);
      return;
    }
    params.set(key, value);
  });

  const queryString = params.toString();
  return `${pathname}${queryString ? `?${queryString}` : ""}${
    hash ? `#${hash}` : ""
  }`;
};

const sampleConnectRealDataHref = (href) =>
  hrefWithJourneyGuide(
    hrefWithParams(href || "/dashboard/observe", {
      setup: "true",
      source: "sample_trace_review",
    }),
    SAMPLE_CONNECT_REAL_DATA_STEP,
  );

const CURRENT_EMAIL_CONTEXT_STATUSES = new Set(["current", "fresh"]);

const EMAIL_CONTEXT_RECOVERY_COPY = {
  route_unavailable: {
    title: "That link is no longer available",
    description: "Continue with the latest recommended step below.",
  },
  stage_changed: {
    title: "Your setup step changed",
    description: "Continue with the latest recommended step below.",
  },
  target_complete: {
    title: "That step is already done",
    description: "Continue with the latest recommended step below.",
  },
};

function emailContextRecoveryCopy(emailContext) {
  if (!emailContext) return null;
  const status = emailContext.contextStatus || emailContext.emailStatus;
  if (!status || CURRENT_EMAIL_CONTEXT_STATUSES.has(status)) return null;
  return (
    EMAIL_CONTEXT_RECOVERY_COPY[emailContext.staleReason] || {
      title: "We updated this link",
      description: "Continue with the latest recommended step below.",
    }
  );
}

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
  const activationEmailContextRef = useRef({});
  const ahaMomentTrackedRef = useRef(new Set());

  const searchContext = useMemo(() => {
    const params = new URLSearchParams(location.search);
    const source = params.get("source") || "home";
    const hasQuickStartQuery =
      params.has("quick_start_goal") ||
      params.has("quick_start_id") ||
      params.has("quick_start_primary_path");
    const queryQuickStartAttribution = normalizeSetupQuickStartAttribution({
      quickStartGoal: params.get("quick_start_goal"),
      quickStartId: params.get("quick_start_id"),
      quickStartPrimaryPath: params.get("quick_start_primary_path"),
    });
    const quickStartAttribution =
      queryQuickStartAttribution.quickStartId || hasQuickStartQuery
        ? queryQuickStartAttribution
        : source === "setup_org"
          ? {}
          : readPersistedSetupQuickStartAttribution();

    return {
      source,
      campaignKey: params.get("campaign_key"),
      emailKey: params.get("email_key"),
      targetStage: params.get("target_stage"),
      targetEvent: params.get("target_event"),
      targetRoute: params.get("target_route"),
      linkIssuedAt: params.get("link_issued_at"),
      sendLogId: params.get("send_log_id"),
      emailStatus: params.get("email_status") || params.get("status"),
      staleReason: params.get("stale_reason"),
      contextStatus: params.get("context_status"),
      mode: params.get("mode"),
      ...quickStartAttribution,
    };
  }, [location.search]);
  const searchActivationEmailContext = useMemo(
    () => activationPayloadEmailContext(searchContext),
    [searchContext],
  );

  useEffect(() => {
    if (searchContext.source !== "setup_org") return;
    try {
      window.localStorage?.removeItem("redirectUrl");
    } catch {
      // The setup handoff should not fail if storage is unavailable.
    }
    persistSetupQuickStartAttribution({
      quickStartGoal: searchContext.quickStartGoal,
      quickStartId: searchContext.quickStartId,
      quickStartPrimaryPath: searchContext.quickStartPrimaryPath,
    });
  }, [
    searchContext.quickStartGoal,
    searchContext.quickStartId,
    searchContext.quickStartPrimaryPath,
    searchContext.source,
  ]);

  useEffect(() => {
    if (Object.keys(searchActivationEmailContext).length === 0) return;
    activationEmailContextRef.current = {
      ...activationEmailContextRef.current,
      ...searchActivationEmailContext,
    };
  }, [searchActivationEmailContext]);

  const activationEmailContextFor = useCallback(
    (context) =>
      compactEventMetadata({
        ...activationEmailContextRef.current,
        ...activationPayloadAttributionContext(context),
      }),
    [],
  );

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
  const setupQuickStartErrorFallback = useMemo(
    () => setupQuickStartErrorFallbackAction(searchContext),
    [searchContext],
  );
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
      campaign_key: searchContext.campaignKey,
      email_key: searchContext.emailKey,
      target_stage: searchContext.targetStage,
      target_event: searchContext.targetEvent,
      send_log_id: searchContext.sendLogId,
      email_status: searchContext.emailStatus,
      link_issued_at: searchContext.linkIssuedAt,
      stale_reason: searchContext.staleReason,
      context_status: searchContext.contextStatus,
      quick_start_goal: searchContext.quickStartGoal,
      quick_start_id: searchContext.quickStartId,
      quick_start_primary_path: searchContext.quickStartPrimaryPath,
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
    searchContext.campaignKey,
    searchContext.contextStatus,
    searchContext.emailKey,
    searchContext.emailStatus,
    searchContext.linkIssuedAt,
    searchContext.quickStartGoal,
    searchContext.quickStartId,
    searchContext.quickStartPrimaryPath,
    searchContext.sendLogId,
    searchContext.source,
    searchContext.staleReason,
    searchContext.targetEvent,
    searchContext.targetStage,
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
      campaign_key: searchContext.campaignKey,
      email_key: searchContext.emailKey,
      target_stage: searchContext.targetStage,
      target_event: searchContext.targetEvent,
      send_log_id: searchContext.sendLogId,
      email_status: searchContext.emailStatus,
      link_issued_at: searchContext.linkIssuedAt,
      stale_reason: searchContext.staleReason,
      context_status: searchContext.contextStatus,
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
    searchContext.contextStatus,
    searchContext.emailKey,
    searchContext.emailStatus,
    searchContext.linkIssuedAt,
    searchContext.sendLogId,
    searchContext.staleReason,
    searchContext.targetEvent,
    searchContext.targetStage,
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
    if (!trackContext || isError || !renderedState?.isActivated) return;

    const activationEvent = renderedState.lastMeaningfulEvent;
    const ahaKey = [
      renderedState.workspaceId || workspaceId || "workspace",
      renderedState.primaryPath || "path",
      renderedState.activatedAt ||
        activationEvent?.occurredAt ||
        renderedState.stage,
    ].join(":");
    if (ahaMomentTrackedRef.current.has(ahaKey)) return;

    ahaMomentTrackedRef.current.add(ahaKey);
    trackOnboardingHomeEvent(OnboardingHomeEvents.ahaMomentReached, {
      ...trackContext,
      home_mode: renderedState.homeMode,
      activated_at: renderedState.activatedAt,
      activation_event_name: activationEvent?.name,
      activation_event_occurred_at: activationEvent?.occurredAt,
      activation_event_path: activationEvent?.path,
      daily_quality_available: Boolean(
        renderedState.routeAvailability?.daily_quality_home?.isAvailable,
      ),
      is_sample: Boolean(activationEvent?.isSample),
    });
  }, [
    isError,
    renderedState?.activatedAt,
    renderedState?.homeMode,
    renderedState?.isActivated,
    renderedState?.lastMeaningfulEvent,
    renderedState?.primaryPath,
    renderedState?.routeAvailability?.daily_quality_home?.isAvailable,
    renderedState?.stage,
    renderedState?.workspaceId,
    trackContext,
    workspaceId,
  ]);

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

  const showGoalPicker =
    renderedState?.stage === "choose_goal" &&
    renderedState?.featureFlags?.onboarding_goal_picker !== false;
  const isSetupQuickStart =
    searchContext.source === "setup_org" && Boolean(searchContext.quickStartId);
  const selectedSetupQuickStart = isSetupQuickStart
    ? SETUP_ORG_PRODUCT_LOOP_QUICK_STARTS.find(
        (option) => option.id === searchContext.quickStartId,
      )
    : null;
  const isSampleQuickStart =
    searchContext.quickStartPrimaryPath === "sample" ||
    searchContext.quickStartId === "sample_preview";
  const setupQuickStartHandoffCopy =
    isSetupQuickStart && isSampleQuickStart
      ? {
          title: "Sample data is a preview",
          description:
            "Use it to inspect screens. Real setup still starts from one product task.",
        }
      : null;
  const isFirstRunQuickStartFocus =
    Boolean(renderedState) &&
    isSetupQuickStart &&
    !renderedState.isActivated &&
    !showGoalPicker &&
    !["feature_disabled", "activated", "daily_review"].includes(
      renderedState.stage,
    );
  const quickStartPathMismatch =
    Boolean(renderedState) &&
    isSetupQuickStart &&
    !isSampleQuickStart &&
    Boolean(searchContext.quickStartPrimaryPath) &&
    renderedState.primaryPath !== searchContext.quickStartPrimaryPath;
  const quickStartMismatchAction = quickStartPathMismatch
    ? setupQuickStartFallbackRecommendedAction(
        setupQuickStartErrorFallback,
        searchContext,
      )
    : null;
  const quickStartFallbackStage = quickStartMismatchAction
    ? setupQuickStartFirstStage(searchContext.quickStartPrimaryPath)
    : null;
  const showContextPanels =
    !isFirstRunQuickStartFocus &&
    !quickStartMismatchAction &&
    ["activated", "daily_review"].includes(renderedState?.stage);
  const hideSetupQuickStartFallback =
    isFirstRunQuickStartFocus &&
    !isSampleQuickStart &&
    Boolean(renderedState.recommendedAction?.href) &&
    renderedState.recommendedAction?.routeAvailable !== false &&
    !renderedState.recommendedAction?.blocked;
  const sampleProject = renderedState?.sampleProject;
  const renderedStage = renderedState?.stage;
  const showSampleAsPrimary = Boolean(
    !quickStartPathMismatch &&
      shouldShowSampleAsPrimary(renderedState) &&
      SAMPLE_PRIMARY_STAGES.has(renderedStage),
  );
  const showSamplePanel = Boolean(
    !(isFirstRunQuickStartFocus && !isSampleQuickStart) &&
      !quickStartPathMismatch &&
      sampleProject?.available &&
      !sampleProject?.isHidden &&
      !renderedState?.isActivated &&
      !showGoalPicker &&
      (showSampleAsPrimary ||
        [
          "connect_observability",
          "waiting_for_first_trace_sample_available",
        ].includes(renderedStage)),
  );
  const sampleRealSetupHref = useMemo(() => {
    if (!sampleProject || !renderedStage) return null;
    const baseHref =
      renderedStage === "connect_real_data"
        ? sampleConnectRealDataHref(sampleProject.realSetupHref)
        : sampleProject.realSetupHref;
    return appendSetupQuickStartAttributionToHref(baseHref, trackContext);
  }, [renderedStage, sampleProject, trackContext]);
  const handleOpenSample = useCallback(
    async (options = {}) => {
      if (!renderedState) return;

      const source = options.source || "onboarding_home";
      const reason = options.reason || renderedState.stage;

      trackOnboardingHomeEvent(OnboardingHomeEvents.sampleProjectOpenClicked, {
        ...trackContext,
        is_sample: true,
        action_path: "sample",
        sample_status: sampleProject?.status,
        manifest_id: sampleProject?.manifestId,
        manifest_version: sampleProject?.manifestVersion,
      });
      try {
        const nextState =
          await sampleProjectActions.openSampleProject.mutateAsync({
            path: "observe",
            source,
            reason,
            ...activationEmailContextFor(trackContext),
            manifestId: sampleProject?.manifestId,
            manifestVersion: sampleProject?.manifestVersion,
            openAfterCreate: true,
          });
        const entryRoute =
          nextState?.sampleProject?.entryRoute ||
          nextState?.sampleProject?.entryRoutes?.[0];
        if (entryRoute) {
          navigate(
            appendSetupQuickStartAttributionToHref(entryRoute, trackContext),
          );
        }
        refetch?.();
      } catch (sampleError) {
        trackOnboardingHomeEvent(OnboardingHomeEvents.sampleProjectOpenFailed, {
          ...trackContext,
          is_sample: true,
          action_path: "sample",
          sample_status: sampleProject?.status,
          reason: sampleError?.message || "unknown_error",
        });
      }
    },
    [
      activationEmailContextFor,
      navigate,
      refetch,
      renderedState,
      sampleProject?.manifestId,
      sampleProject?.manifestVersion,
      sampleProject?.status,
      sampleProjectActions.openSampleProject,
      trackContext,
    ],
  );

  if (isLoading || waitingForWorkspace || (!renderedState && !isError)) {
    return <OnboardingHomeSkeleton />;
  }

  if (isError) {
    return (
      <OnboardingHomeError
        error={error}
        fallbackAction={setupQuickStartErrorFallback}
        onRetry={refetch}
      />
    );
  }

  const firstRunPlanPath = quickStartPathMismatch
    ? searchContext.quickStartPrimaryPath
    : renderedState.primaryPath;
  const firstRunPlanStage =
    quickStartPathMismatch && quickStartFallbackStage
      ? quickStartFallbackStage
      : renderedState.stage;
  const firstRunJourneyPlan =
    isFirstRunQuickStartFocus && !isSampleQuickStart
      ? (!quickStartPathMismatch && renderedState.journeyPlan) ||
        (firstRunPlanPath === "observe"
          ? observeFallbackJourneyPlan(firstRunPlanStage)
          : PATH_FOCUS_PLANS[firstRunPlanPath])
      : null;
  const firstRunCurrentStep = firstRunJourneyPlan
    ? journeyCurrentStep(firstRunJourneyPlan, firstRunPlanStage)
    : null;
  const firstRunCurrentStepLabel =
    firstRunCurrentStep?.label || "the highlighted action";
  const firstRunNextStepLabel =
    firstRunJourneyPlan && firstRunCurrentStep
      ? firstRunJourneyPlan.steps[
          firstRunJourneyPlan.steps.indexOf(firstRunCurrentStep) + 1
        ]?.label
      : null;

  const copy =
    isFirstRunQuickStartFocus && selectedSetupQuickStart
      ? isSampleQuickStart
        ? {
            eyebrow: "Sample preview",
            title: "Preview sample data",
            description:
              "Open the sample trace to inspect screens. Real setup still starts from one product task.",
          }
        : {
            eyebrow: "First setup",
            title: selectedSetupQuickStart.buttonLabel,
            description: firstRunNextStepLabel
              ? `${selectedSetupQuickStart.shortDescription} Start with: ${firstRunCurrentStepLabel}. Then: ${firstRunNextStepLabel}.`
              : `${selectedSetupQuickStart.shortDescription} Start with: ${firstRunCurrentStepLabel}.`,
            surfaceLabel: selectedSetupQuickStart.surfaceLabel,
          }
      : quickStartMismatchAction
        ? {
            eyebrow: "Setup",
            title: quickStartMismatchAction.title,
            description: quickStartMismatchAction.description,
          }
        : getStageCopy(renderedState);
  const isSavingGoal = mutationPending(saveGoal);
  const emailRecoveryCopy = emailContextRecoveryCopy(
    renderedState.emailContext,
  );

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
      ...activationEmailContextFor(dailyTrackContext),
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

  const handleDailyActionResolve = (dailyAction, resolution) => {
    if (!dailyAction) return;
    const isDismissed = resolution === "dismissed";
    const eventName = isDismissed
      ? "daily_quality_action_dismissed"
      : "daily_quality_action_completed";
    const analyticsEventName = isDismissed
      ? OnboardingHomeEvents.dailyQualityActionDismissed
      : OnboardingHomeEvents.dailyQualityActionCompleted;
    const route = dailyAction.route || dailyAction.fallbackRoute;

    trackOnboardingHomeEvent(analyticsEventName, {
      ...dailyTrackContext,
      recommended_action_id: dailyAction.id,
      route,
      source_type: dailyAction.sourceType,
      source_id: dailyAction.sourceId,
      resolution,
    });
    recordActivationEvent.mutate?.(
      {
        eventName,
        primaryPath: renderedState.primaryPath,
        stage: renderedState.stage,
        source: "daily_quality_home",
        ...activationEmailContextFor(dailyTrackContext),
        artifactType: dailyAction.sourceType,
        artifactId: dailyAction.sourceId,
        projectId:
          dailyAction.sourceType === "project"
            ? dailyAction.sourceId
            : undefined,
        isSample: dailyAction.isSample,
        metadata: compactEventMetadata({
          action_id: dailyAction.id,
          source_type: dailyAction.sourceType,
          source_id: dailyAction.sourceId,
          route,
          daily_quality_mode: renderedState.dailyQuality?.mode,
          resolution,
        }),
      },
      {
        onSuccess: () => refetch?.(),
      },
    );
  };

  const handleWeeklyReviewOpen = (weeklyReview) => {
    trackOnboardingHomeEvent(OnboardingHomeEvents.weeklyQualityReviewOpened, {
      ...dailyTrackContext,
      weekly_review_status: weeklyReview?.status,
      unresolved_count: weeklyReview?.unresolvedCount,
      completed_count: weeklyReview?.completedCount,
      route: weeklyReview?.route,
    });
  };

  const handlePathClick = async (path) => {
    trackOnboardingHomeEvent(OnboardingHomeEvents.homePathClicked, {
      ...trackContext,
      path_id: path.id,
      path_status: path.status,
    });
    if (!path?.isAvailable || path.status === "selected") return;

    const option = goalOptions.find(
      (goalOption) =>
        goalOption.primaryPath === path.id && goalOption.disabled !== true,
    );
    if (!option) return;

    try {
      const nextState = await saveGoal.mutateAsync({
        goal: option.goal,
        primaryPath: option.primaryPath,
        source: "path_card",
        reason: "path_change",
        expectedStage: renderedState.stage,
      });
      trackOnboardingHomeEvent(OnboardingHomeEvents.homeGoalSaved, {
        ...trackContext,
        selected_goal: option.goal,
        selected_path: option.primaryPath,
        next_stage: nextState.stage,
        source: "path_card",
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
        source: "path_card",
      });
    }
  };

  const handleHideSample = async () => {
    trackOnboardingHomeEvent(OnboardingHomeEvents.sampleProjectHideClicked, {
      ...trackContext,
      is_sample: true,
      action_path: "sample",
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
      is_sample: true,
      action_path: "sample_to_real",
      sample_status: renderedState.sampleProject?.status,
    });
  };

  const dailyQualityPanel =
    renderedState.homeMode === "daily_quality" && renderedState.dailyQuality ? (
      <DailyQualityHome
        dailyQuality={renderedState.dailyQuality}
        recommendedAction={renderedState.recommendedAction}
        onActionClick={handleDailyActionClick}
        onActionResolve={handleDailyActionResolve}
        onSignalReview={handleDailySignalReview}
        onWeeklyReviewOpen={handleWeeklyReviewOpen}
        isResolvingAction={mutationPending(recordActivationEvent)}
        canAct={!renderedState.permissions?.permissionLimited}
      />
    ) : null;

  const observePanelProps = {
    action: actionWithSetupQuickStartAttribution(
      renderedState.recommendedAction,
      trackContext,
    ),
    fallbackAction: actionWithSetupQuickStartAttribution(
      hideSetupQuickStartFallback ? null : renderedState.fallbackAction,
      trackContext,
    ),
    onPrimaryClick: handleActionClick,
    onFallbackClick: handleActionClick,
    onCheckAgain: refetch,
    isChecking: Boolean(isRefetching),
    singleActionFocus: isFirstRunQuickStartFocus,
  };

  const firstLoopCompletePanel = ["activated", "daily_review"].includes(
    renderedState.stage,
  )
    ? dailyQualityPanel || (
        <FirstLoopCompletePanel
          {...observePanelProps}
          dailyQualityRoute={
            renderedState.routeAvailability?.daily_quality_home
          }
          lastMeaningfulEvent={renderedState.lastMeaningfulEvent}
          primaryPath={renderedState.primaryPath}
        />
      )
    : null;

  const observePanel =
    renderedState.primaryPath === "observe" &&
    OBSERVE_PANEL_STAGES.has(renderedState.stage) ? (
      <>
        {renderedState.stage === "connect_observability" ? (
          <ObserveSetupPanel
            {...observePanelProps}
            journeyPlan={renderedState.journeyPlan}
            stage={renderedState.stage}
          />
        ) : null}
        {[
          "waiting_for_first_trace",
          "waiting_for_first_trace_sample_available",
        ].includes(renderedState.stage) ? (
          <WaitingForSignalPanel
            {...observePanelProps}
            journeyPlan={renderedState.journeyPlan}
            signals={renderedState.signals}
            stage={renderedState.stage}
          />
        ) : null}
        {["review_first_trace", "create_trace_evaluator"].includes(
          renderedState.stage,
        ) ? (
          <FirstSignalPanel
            {...observePanelProps}
            journeyPlan={renderedState.journeyPlan}
            signals={renderedState.signals}
            stage={renderedState.stage}
          />
        ) : null}
        {["activated", "daily_review"].includes(renderedState.stage)
          ? firstLoopCompletePanel
          : null}
      </>
    ) : null;

  const pathFocusPanel =
    renderedState.primaryPath !== "observe" &&
    !["activated", "daily_review"].includes(renderedState.stage) &&
    (renderedState.journeyPlan ||
      hasPathFocusPlan(renderedState.primaryPath)) ? (
      <PathFocusPanel
        {...observePanelProps}
        journeyPlan={renderedState.journeyPlan}
        primaryPath={renderedState.primaryPath}
        singleActionFocus={isFirstRunQuickStartFocus}
        stage={renderedState.stage}
      />
    ) : null;

  const quickStartFallbackPanel =
    quickStartMismatchAction && !isSampleQuickStart ? (
      searchContext.quickStartPrimaryPath === "observe" ? (
        <ObserveSetupPanel
          {...observePanelProps}
          action={quickStartMismatchAction}
          fallbackAction={null}
          journeyPlan={null}
          singleActionFocus
          stage={quickStartFallbackStage || "connect_observability"}
        />
      ) : hasPathFocusPlan(searchContext.quickStartPrimaryPath) ? (
        <PathFocusPanel
          {...observePanelProps}
          action={quickStartMismatchAction}
          fallbackAction={null}
          journeyPlan={null}
          primaryPath={searchContext.quickStartPrimaryPath}
          singleActionFocus
          stage={quickStartFallbackStage}
        />
      ) : null
    ) : null;

  const samplePanel = showSamplePanel ? (
    <SampleProjectPanel
      sampleProject={sampleProject}
      activationStage={renderedState.stage}
      selectedGoal={renderedState.goal}
      realSetupHref={sampleRealSetupHref}
      onOpenSample={
        isFirstRunQuickStartFocus && isSampleQuickStart
          ? () =>
              handleOpenSample({
                source: "setup_org",
                reason: searchContext.quickStartId || "sample_preview",
              })
          : handleOpenSample
      }
      onHideSample={handleHideSample}
      onConnectRealData={handleConnectRealData}
      isOpening={mutationPending(sampleProjectActions.openSampleProject)}
      isHiding={mutationPending(sampleProjectActions.hideSampleProject)}
    />
  ) : null;

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
            {copy.surfaceLabel ? (
              <Chip size="small" variant="outlined" label={copy.surfaceLabel} />
            ) : null}
            {renderedState.isActivated ? (
              <Chip size="small" color="success" label="Activated" />
            ) : null}
          </Stack>
          <Typography component="h3" variant="h4" sx={{ lineHeight: 1.15 }}>
            {copy.title}
          </Typography>
          <Typography variant="body1" color="text.secondary" maxWidth={760}>
            {copy.description}
          </Typography>
          {currentWorkspaceDisplayName ? (
            <Typography variant="body2" color="text.secondary">
              Workspace: {currentWorkspaceDisplayName}
            </Typography>
          ) : null}
          {setupQuickStartHandoffCopy ? (
            <Alert
              severity="info"
              data-testid="setup-quick-start-handoff-alert"
              sx={{ borderRadius: 1, mt: 1, maxWidth: 760 }}
            >
              <Typography variant="subtitle2" component="div">
                {setupQuickStartHandoffCopy.title}
              </Typography>
              <Typography variant="body2" sx={{ mt: 0.25 }}>
                {setupQuickStartHandoffCopy.description}
              </Typography>
            </Alert>
          ) : null}
        </Stack>

        {renderedState.stage === "feature_disabled" ? (
          <Alert severity="info" sx={{ borderRadius: 1 }}>
            The existing setup checklist is available for this workspace.
          </Alert>
        ) : null}

        {emailRecoveryCopy ? (
          <Alert
            severity="info"
            data-testid="lifecycle-email-context-alert"
            sx={{ borderRadius: 1 }}
          >
            <Typography variant="subtitle2" component="div">
              {emailRecoveryCopy.title}
            </Typography>
            <Typography variant="body2" sx={{ mt: 0.25 }}>
              {emailRecoveryCopy.description}
            </Typography>
          </Alert>
        ) : null}

        {quickStartMismatchAction ? (
          quickStartFallbackPanel || (
            <Box
              sx={{
                display: "grid",
                gridTemplateColumns: { xs: "1fr", md: "minmax(0, 2fr) 1fr" },
                gap: 2,
                alignItems: "stretch",
              }}
            >
              <RecommendedActionCard
                action={quickStartMismatchAction}
                label="Next step"
                onActionClick={handleActionClick}
              />
            </Box>
          )
        ) : showGoalPicker ? (
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
              skipLabel={
                renderedState.fallbackAction?.ctaLabel ||
                renderedState.fallbackAction?.title
              }
              isSaving={isSavingGoal}
              error={saveGoal.error}
            />
            <RecommendedActionCard
              action={renderedState.fallbackAction}
              label="Other setup option"
              variant="fallback"
              onActionClick={handleActionClick}
            />
          </Box>
        ) : showSampleAsPrimary && samplePanel ? (
          <Stack spacing={2}>{samplePanel}</Stack>
        ) : observePanel ? (
          <Stack spacing={2}>
            {observePanel}
            {samplePanel}
          </Stack>
        ) : pathFocusPanel ? (
          <Stack spacing={2}>{pathFocusPanel}</Stack>
        ) : firstLoopCompletePanel ? (
          <Stack spacing={2}>{firstLoopCompletePanel}</Stack>
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
              label="Other setup option"
              variant="fallback"
              onActionClick={handleActionClick}
            />
          </Box>
        )}

        {showContextPanels ? (
          <Box
            data-testid="onboarding-state-summary"
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
        ) : null}

        {showContextPanels ? (
          <ProductLoopStepper
            fallbackAction={renderedState.fallbackAction}
            goal={renderedState.goal}
            onActionClick={handleActionClick}
            primaryPath={renderedState.primaryPath}
            progress={renderedState.progress}
            recommendedAction={renderedState.recommendedAction}
            stage={renderedState.stage}
          />
        ) : null}
        {showContextPanels && observePanel ? (
          <ObserveDiagnosticsPanel signals={renderedState.signals} />
        ) : null}
        {showContextPanels ? (
          <PathCardGrid
            isChangingPath={isSavingGoal}
            paths={renderedState.availablePaths}
            onPathClick={handlePathClick}
          />
        ) : null}
        {showContextPanels ? <Diagnostics state={renderedState} /> : null}
      </Stack>
    </Box>
  );
}
