const SCHEMA_VERSION = "activation-state-2026-05-26.v1";

const routeAvailability = (overrides = {}) => ({
  home: {
    href: "/dashboard/home",
    is_available: true,
    reason: null,
  },
  choose_goal: {
    href: "/dashboard/home?mode=choose-goal",
    is_available: true,
    reason: null,
  },
  observe_setup: {
    href: "/dashboard/observe?setup=true&source=onboarding",
    is_available: true,
    reason: null,
  },
  observe_project: {
    href: "/dashboard/observe/observe-1",
    is_available: true,
    reason: null,
  },
  observe_trace_detail: {
    href: "/dashboard/observe/observe-1/trace/trace-1",
    is_available: true,
    reason: null,
  },
  observe_dashboard: {
    href: "/dashboard/observe/observe-1",
    is_available: true,
    reason: null,
  },
  sample_trace: {
    href: "/dashboard/home?sample=true",
    is_available: true,
    reason: null,
  },
  daily_quality_home: {
    href: "/dashboard/home?mode=daily-quality",
    is_available: true,
    reason: null,
  },
  get_started: {
    href: "/dashboard/get-started",
    is_available: true,
    reason: null,
  },
  workspace_list: {
    href: "/dashboard/settings/user-management",
    is_available: true,
    reason: null,
  },
  path_observe: {
    href: "/dashboard/home?path=observe",
    is_available: true,
    reason: null,
  },
  path_sample: {
    href: "/dashboard/home?path=sample",
    is_available: true,
    reason: null,
  },
  path_prompt: {
    href: "/dashboard/home?path=prompt",
    is_available: false,
    reason: "route_not_implemented",
  },
  ...overrides,
});

const action = (overrides = {}) => ({
  id: "create_observe_project",
  kind: "setup",
  title: "Connect observability",
  description: "Create an observability project and send one request.",
  href: "/dashboard/observe?setup=true&source=onboarding",
  cta_label: "Connect observability",
  estimated_minutes: 5,
  priority: 100,
  blocked: false,
  blocked_reason: null,
  requires_permission: "observe:write",
  completion_event: "observe_project_created",
  is_sample: false,
  route_available: true,
  fallback_href: "/dashboard/get-started",
  analytics: {
    event_name: "onboarding_recommended_action_clicked",
    source: "home",
    target_path: "observe",
  },
  ...overrides,
});

const fallbackAction = (overrides = {}) =>
  action({
    id: "open_get_started",
    kind: "fallback",
    title: "Open Get Started",
    description: "Use the existing setup checklist.",
    href: "/dashboard/get-started",
    cta_label: "Open Get Started",
    estimated_minutes: null,
    priority: 10,
    blocked: false,
    blocked_reason: null,
    requires_permission: null,
    completion_event: null,
    is_sample: false,
    route_available: true,
    fallback_href: "/dashboard/get-started",
    analytics: {
      event_name: "onboarding_recommended_action_clicked",
      source: "fallback",
      target_path: null,
    },
    ...overrides,
  });

const availableGoals = [
  {
    id: "monitor_production_ai_app",
    goal: "monitor_production_ai_app",
    primary_path: "observe",
    label: "Monitor a production AI app",
    description: "Connect traces and review the first quality signal.",
    estimated_minutes: 5,
    disabled: false,
    disabled_reason: null,
  },
  {
    id: "improve_prompts",
    goal: "improve_prompts",
    primary_path: "prompt",
    label: "Test and improve prompts",
    description: "Create a prompt test loop and compare output changes.",
    estimated_minutes: 6,
    disabled: false,
    disabled_reason: null,
  },
];

const sampleAction = (overrides = {}) =>
  action({
    id: "open_sample_trace",
    kind: "sample_project",
    title: "Open sample trace",
    description: "Review a sample trace while real data is pending.",
    href: "/dashboard/home?sample=true",
    cta_label: "Open sample trace",
    estimated_minutes: 2,
    priority: 30,
    blocked: false,
    blocked_reason: null,
    requires_permission: null,
    completion_event: "sample_signal_viewed",
    is_sample: true,
    route_available: true,
    fallback_href: "/dashboard/get-started",
    analytics: {
      event_name: "onboarding_recommended_action_clicked",
      source: "home",
      target_path: "sample",
    },
    ...overrides,
  });

const baseSampleProject = (overrides = {}) => ({
  available: true,
  created: false,
  status: "available",
  href: "/dashboard/home?sample=true",
  version: "sample-observe-v1",
  is_hidden: false,
  hidden_reason: null,
  entry_routes: [],
  missing_artifacts: [],
  last_opened_at: null,
  ...overrides,
});

const baseState = (overrides = {}) => ({
  schema_version: SCHEMA_VERSION,
  request_id: "req_onboarding_fixture",
  server_time: "2026-05-26T15:00:00Z",
  workspace_id: "wrk_onboarding",
  organization_id: "org_onboarding",
  user_id: "usr_onboarding",
  goal: "monitor_production_ai_app",
  persona: "developer",
  primary_path: "observe",
  stage: "connect_observability",
  home_mode: "first_run",
  is_activated: false,
  activated_at: null,
  recommended_action: action(),
  fallback_action: fallbackAction(),
  progress: {
    build: "selected",
    test: "available",
    observe: "not_started",
    ship: "available",
    improve: "available",
  },
  signals: {
    provider_keys: 1,
    datasets: 0,
    evals: 0,
    eval_runs: 0,
    prompt_templates: 0,
    prompt_versions: 0,
    prompt_comparisons: 0,
    agents: 0,
    agent_prototype_runs: 0,
    observe_projects: 0,
    traces: 0,
    trace_reviews: 0,
    gateway_keys: 0,
    gateway_requests: 0,
    gateway_policies: 0,
    voice_agents: 0,
    voice_simulations: 0,
    voice_calls: 0,
    voice_reviews: 0,
    team_invites: 0,
    dashboards: 0,
    alerts: 0,
    first_trace_id: null,
    first_observe_id: null,
  },
  available_goals: availableGoals,
  available_paths: [
    {
      id: "observe",
      label: "Monitor a production AI app",
      description: "Connect traces and inspect quality signals.",
      status: "selected",
      href: "/dashboard/home?path=observe",
      is_available: true,
      blocked_reason: null,
      requires_permission: "observe:write",
      first_action_id: "create_observe_project",
    },
    {
      id: "sample",
      label: "Explore with sample data",
      description: "Use a sample workspace while real data is pending.",
      status: "available",
      href: "/dashboard/home?path=sample",
      is_available: true,
      blocked_reason: null,
      requires_permission: null,
      first_action_id: "open_sample_trace",
    },
  ],
  sample_project: baseSampleProject(),
  email_eligibility: {
    eligible: true,
    suppressed: false,
    suppression_reason: null,
    next_email_key: "connect_observability_next",
    next_email_after: "2026-05-26T15:00:00Z",
    digest_eligible: false,
    last_email_sent_at: null,
    frequency_cap_remaining: 2,
    dry_run_only: true,
  },
  permissions: {
    role: "admin",
    can_read: true,
    can_write: true,
    can_manage_workspace: true,
    missing_permissions: [],
    request_access_href: "/dashboard/settings/user-management",
    permission_limited: false,
  },
  feature_flags: {
    onboarding_activation_state_api: true,
    onboarding_goal_picker: true,
    onboarding_path_cards: true,
    onboarding_sample_project: true,
    onboarding_lifecycle_send_enabled: false,
    onboarding_daily_quality_home: true,
  },
  route_availability: routeAvailability(),
  email_context: null,
  last_meaningful_event: null,
  diagnostics: null,
  warnings: [],
  ...overrides,
});

export const activationStateFixtures = {
  newWorkspaceNoGoal: baseState({
    goal: null,
    primary_path: null,
    stage: "choose_goal",
    recommended_action: action({
      id: "choose_onboarding_goal",
      kind: "choose_goal",
      title: "Choose your first goal",
      description: "Pick the job FutureAGI should guide first.",
      href: "/dashboard/home?mode=choose-goal",
      cta_label: "Choose goal",
      estimated_minutes: 1,
      requires_permission: null,
      completion_event: "onboarding_goal_selected",
      analytics: {
        event_name: "onboarding_recommended_action_clicked",
        source: "home",
        target_path: null,
      },
    }),
    progress: {
      build: "not_started",
      test: "available",
      observe: "not_started",
      ship: "available",
      improve: "available",
    },
  }),

  observeNoSetup: baseState(),

  observeWaitingForTrace: baseState({
    stage: "waiting_for_first_trace",
    recommended_action: action({
      id: "send_first_trace",
      kind: "send_signal",
      title: "Send your first trace",
      description: "Send one production or test trace to unlock review.",
      href: "/dashboard/observe/observe-1",
      cta_label: "Send trace",
      completion_event: "trace_received",
    }),
    progress: {
      build: "selected",
      test: "available",
      observe: "in_progress",
      ship: "available",
      improve: "available",
    },
    signals: {
      ...baseState().signals,
      observe_projects: 1,
      first_observe_id: "observe-1",
    },
  }),

  observeWaitingWithSample: baseState({
    stage: "waiting_for_first_trace_sample_available",
    recommended_action: action({
      id: "send_first_trace",
      kind: "send_signal",
      title: "Send your first trace",
      description: "Send one production or test trace to unlock review.",
      href: "/dashboard/observe/observe-1",
      cta_label: "Send trace",
      completion_event: "trace_received",
    }),
    fallback_action: sampleAction(),
    progress: {
      build: "selected",
      test: "available",
      observe: "in_progress",
      ship: "available",
      improve: "available",
    },
    signals: {
      ...baseState().signals,
      observe_projects: 1,
      first_observe_id: "observe-1",
    },
  }),

  observeFirstTraceReady: baseState({
    stage: "review_first_trace",
    recommended_action: action({
      id: "review_first_trace",
      kind: "review",
      title: "Review the first trace",
      description: "Inspect latency, cost, and quality signal context.",
      href: "/dashboard/observe/observe-1/trace/trace-1",
      cta_label: "Review trace",
      estimated_minutes: 3,
      requires_permission: null,
      completion_event: "trace_reviewed",
    }),
    signals: {
      ...baseState().signals,
      observe_projects: 1,
      traces: 1,
      first_observe_id: "observe-1",
      first_trace_id: "trace-1",
    },
  }),

  observeNeedsEvaluator: baseState({
    stage: "create_trace_evaluator",
    recommended_action: action({
      id: "create_trace_evaluator",
      kind: "improve",
      title: "Create an evaluator",
      description: "Turn the reviewed trace into a repeatable quality check.",
      href: "/dashboard/observe/observe-1",
      cta_label: "Create evaluator",
      completion_event: "first_quality_loop_completed",
    }),
    progress: {
      build: "selected",
      test: "available",
      observe: "complete",
      ship: "available",
      improve: "in_progress",
    },
    signals: {
      ...baseState().signals,
      observe_projects: 1,
      traces: 1,
      trace_reviews: 1,
      first_observe_id: "observe-1",
      first_trace_id: "trace-1",
    },
  }),

  observeFirstLoopComplete: baseState({
    stage: "activated",
    home_mode: "first_run",
    is_activated: true,
    activated_at: "2026-05-26T15:10:00Z",
    recommended_action: action({
      id: "open_observe_dashboard",
      kind: "review",
      title: "Open observe dashboard",
      description: "Review the current quality loop.",
      href: "/dashboard/observe/observe-1",
      cta_label: "Open observe",
      estimated_minutes: null,
      requires_permission: null,
      completion_event: null,
    }),
    progress: {
      build: "complete",
      test: "available",
      observe: "complete",
      ship: "available",
      improve: "complete",
    },
    email_eligibility: {
      ...baseState().email_eligibility,
      eligible: false,
      suppressed: true,
      suppression_reason: "activated",
      next_email_key: null,
      next_email_after: null,
    },
    last_meaningful_event: {
      name: "first_quality_loop_completed",
      occurred_at: "2026-05-26T15:10:00Z",
      is_sample: false,
      path: "observe",
      metadata: {},
    },
  }),

  dailyQualityObserve: baseState({
    stage: "daily_review",
    home_mode: "daily_quality",
    is_activated: true,
    activated_at: "2026-05-26T15:10:00Z",
    recommended_action: action({
      id: "review_daily_quality",
      kind: "daily_quality",
      title: "Review today's quality signal",
      description: "Open the daily quality view and resolve the top item.",
      href: "/dashboard/home?mode=daily-quality",
      cta_label: "Review signal",
      estimated_minutes: 4,
      requires_permission: null,
      completion_event: "daily_quality_item_reviewed",
    }),
    progress: {
      build: "complete",
      test: "available",
      observe: "complete",
      ship: "available",
      improve: "complete",
    },
  }),

  sampleTraceReady: baseState({
    primary_path: "sample",
    stage: "review_sample_signal",
    recommended_action: sampleAction(),
    sample_project: baseSampleProject({
      created: true,
      status: "ready",
      entry_routes: ["/dashboard/home?sample=true"],
    }),
    last_meaningful_event: {
      name: "sample_signal_viewed",
      occurred_at: "2026-05-26T15:10:00Z",
      is_sample: true,
      path: "sample",
      metadata: {},
    },
  }),

  sampleUnavailable: baseState({
    stage: "waiting_for_first_trace",
    fallback_action: fallbackAction(),
    sample_project: baseSampleProject({
      available: false,
      status: "unavailable",
      href: null,
      version: null,
      is_hidden: true,
      hidden_reason: "feature_disabled",
    }),
    feature_flags: {
      ...baseState().feature_flags,
      onboarding_sample_project: false,
    },
    route_availability: routeAvailability({
      sample_trace: {
        href: "/dashboard/home?sample=true",
        is_available: false,
        reason: "feature_disabled",
      },
      path_sample: {
        href: "/dashboard/home?path=sample",
        is_available: false,
        reason: "feature_disabled",
      },
    }),
  }),

  permissionLimitedViewer: baseState({
    stage: "permission_limited",
    home_mode: "fallback",
    recommended_action: action({
      id: "request_workspace_access",
      kind: "request_access",
      title: "Request workspace access",
      description: "Ask an admin for access before making onboarding changes.",
      href: "/dashboard/settings/user-management",
      cta_label: "Request access",
      estimated_minutes: null,
      requires_permission: null,
      completion_event: null,
      analytics: {
        event_name: "onboarding_recommended_action_clicked",
        source: "permission_limited",
        target_path: null,
      },
    }),
    permissions: {
      role: "viewer",
      can_read: true,
      can_write: false,
      can_manage_workspace: false,
      missing_permissions: ["workspace:write", "workspace:manage"],
      request_access_href: "/dashboard/settings/user-management",
      permission_limited: true,
    },
  }),

  featureDisabled: baseState({
    stage: "feature_disabled",
    home_mode: "fallback",
    goal: null,
    primary_path: null,
    recommended_action: fallbackAction(),
    fallback_action: fallbackAction(),
    feature_flags: {
      onboarding_activation_state_api: false,
    },
    email_eligibility: {
      ...baseState().email_eligibility,
      eligible: false,
      suppressed: true,
      suppression_reason: "feature_disabled",
      next_email_key: null,
      next_email_after: null,
    },
  }),

  selectedPathUnavailable: baseState({
    goal: "improve_prompts",
    primary_path: "prompt",
    stage: "selected_path_unavailable",
    home_mode: "fallback",
    recommended_action: action({
      id: "choose_available_path",
      kind: "fallback",
      title: "Choose an available path",
      description:
        "Start with the observe path while this path is unavailable.",
      href: "/dashboard/observe?setup=true&source=onboarding",
      cta_label: "Start with observe",
      requires_permission: "observe:write",
      completion_event: null,
    }),
  }),

  staleEmailLink: baseState({
    stage: "review_first_trace",
    recommended_action: action({
      id: "review_first_trace",
      kind: "review",
      title: "Review the first trace",
      description: "Inspect latency, cost, and quality signal context.",
      href: "/dashboard/observe/observe-1/trace/trace-1",
      cta_label: "Review trace",
      estimated_minutes: 3,
      requires_permission: null,
      completion_event: "trace_reviewed",
    }),
    email_context: {
      campaign_key: "observe_waiting_for_first_trace",
      email_key: "observe_waiting_for_first_trace_1",
      target_stage: "waiting_for_first_trace",
      target_event: "trace_received",
      target_route: "/dashboard/observe/observe-1",
      context_status: "stale",
      stale_reason: "stage_changed",
      resolved_href: "/dashboard/observe/observe-1/trace/trace-1",
    },
    warnings: ["email_context_stale"],
  }),

  apiErrorFallback: baseState({
    request_id: "local_api_error_fallback",
    workspace_id: null,
    organization_id: null,
    user_id: "unknown",
    goal: null,
    primary_path: null,
    stage: "feature_disabled",
    home_mode: "fallback",
    recommended_action: fallbackAction({
      analytics: {
        event_name: "onboarding_recommended_action_clicked",
        source: "api_error",
        target_path: null,
      },
    }),
    fallback_action: fallbackAction({
      analytics: {
        event_name: "onboarding_recommended_action_clicked",
        source: "api_error",
        target_path: null,
      },
    }),
    sample_project: baseSampleProject({
      available: false,
      status: "unavailable",
      href: null,
      version: null,
      is_hidden: true,
      hidden_reason: "api_error",
    }),
    email_eligibility: {
      ...baseState().email_eligibility,
      eligible: false,
      suppressed: true,
      suppression_reason: "feature_disabled",
      next_email_key: null,
      next_email_after: null,
      frequency_cap_remaining: 0,
    },
    feature_flags: {
      onboarding_activation_state_api: false,
    },
    warnings: ["activation_state_request_failed"],
  }),
};

export const activationStateFixtureList = Object.entries(
  activationStateFixtures,
).map(([name, state]) => ({
  name,
  state,
}));

export const getActivationStateFixture = (name) => {
  const fixture = activationStateFixtures[name];
  if (!fixture) {
    throw new Error(`Unknown activation-state fixture: ${name}`);
  }
  return JSON.parse(JSON.stringify(fixture));
};

export const makeActivationStateFixture = (name, overrides = {}) => ({
  ...getActivationStateFixture(name),
  ...overrides,
});
