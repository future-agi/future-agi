import { Buffer } from "node:buffer";
import { randomUUID } from "node:crypto";
import http from "node:http";
import process from "node:process";

import { getActivationStateFixture } from "../../../src/sections/onboarding-home/fixtures/activation-state.fixtures.js";

const PORT = Number(process.env.PORT || 8005);
const HOST = process.env.HOST || "127.0.0.1";
const ORG_ID = "mock-org-1";
const WORKSPACE_ID = "mock-ws-1";
const USER_ID = "mock-user-1";

const uuidFor = (value) =>
  `00000000-0000-4000-8000-${String(value).padStart(12, "0")}`;
const projectIdForIndex = (index) => uuidFor(1000 + index);
const traceIdForIndex = (index) => uuidFor(2000 + index);
const evalIdForIndex = (index) => uuidFor(3000 + index);
const runIdForIndex = (index) => uuidFor(4000 + index);
const preferenceIdForIndex = (index) => uuidFor(5000 + index);
const channelIdForIndex = (index) => uuidFor(6000 + index);
const deliveryLogIdForIndex = (index) => uuidFor(7000 + index);
const nowIso = () => new Date().toISOString();

const NOTIFICATION_CHANNELS = ["email", "slack", "webhook"];
const NOTIFICATION_FAMILIES = [
  {
    id: "product_onboarding",
    label: "Product onboarding",
    description: "First action, path recovery, and activation nudges.",
    default_channels: ["email"],
    non_critical: true,
    user_controllable: true,
    workspace_controllable: true,
  },
  {
    id: "daily_quality_digest",
    label: "Daily quality digest",
    description: "Return-loop summaries for activated workspaces.",
    default_channels: ["email"],
    non_critical: true,
    user_controllable: true,
    workspace_controllable: true,
  },
  {
    id: "usage_budget",
    label: "Usage and budget alerts",
    description: "Budget thresholds, warnings, and blocking usage states.",
    default_channels: ["email"],
    non_critical: false,
    user_controllable: false,
    workspace_controllable: true,
  },
  {
    id: "gateway_alert",
    label: "Gateway alerts",
    description: "Gateway cost, latency, errors, and guardrail activity.",
    default_channels: ["email"],
    non_critical: false,
    user_controllable: false,
    workspace_controllable: true,
  },
  {
    id: "observe_monitor",
    label: "Observe monitors",
    description: "Trace, eval, latency, reliability, and spend monitor alerts.",
    default_channels: ["email"],
    non_critical: false,
    user_controllable: false,
    workspace_controllable: true,
  },
  {
    id: "eval_quality_alert",
    label: "Eval quality alerts",
    description: "Eval failures, regressions, and quality-review reminders.",
    default_channels: ["email"],
    non_critical: false,
    user_controllable: false,
    workspace_controllable: true,
  },
  {
    id: "workspace_admin",
    label: "Workspace administration",
    description: "Invites, access, security, and account-state messages.",
    default_channels: ["email"],
    non_critical: false,
    user_controllable: false,
    workspace_controllable: true,
  },
];

const CHANNEL_TYPE_TO_DELIVERY = {
  email_list: "email",
  slack_webhook: "slack",
  webhook: "webhook",
};

const defaultState = () => ({
  activationEvents: [],
  completedFirstLoop: false,
  email: "nikhilpareekiitr@gmail.com",
  evalRuns: [],
  evalTemplates: new Map(),
  fullName: "Nikhil",
  onboarding: {
    goals: ["monitor_production_ai_app"],
    role: "Subject Matter Expert",
  },
  notificationChannelCounter: 1,
  notificationChannels: [
    {
      config: {
        webhook_url: "https://example.com/slack/onboarding-local",
      },
      display_name: "Onboarding Slack",
      id: "stub-slack-channel",
      is_active: true,
      last_test_status: "untested",
      last_tested_at: null,
      metadata: {},
      scope: "workspace",
      target_identifier: "Slack webhook configured",
      type: "slack_webhook",
    },
  ],
  notificationDeliveryCounter: 1,
  notificationDeliveryLogs: [
    {
      channel: "slack",
      created_at: nowIso(),
      family: "usage_budget",
      id: deliveryLogIdForIndex(1),
      metadata: {
        action: "warn",
        current_usage: "$812",
        scope: "workspace",
        threshold_value: "$800",
      },
      notification_key: "budget_threshold_80",
      recipient_identifier_masked: "Slack webhook configured",
      recipient_type: "slack_webhook",
      route_url: "/dashboard/settings/billing",
      sent_at: null,
      severity: "warning",
      source_id: "local-budget-1",
      source_type: "usage_budget",
      stage: "80",
      status: "suppressed",
      suppressed_reason: "channel_disabled",
    },
  ],
  notificationPreferenceCounter: 0,
  notificationPreferences: [],
  projects: new Map(),
  requests: [],
  traceReviewed: false,
  traces: new Map(),
});

const state = defaultState();

const resetState = () => {
  Object.assign(state, defaultState());
};

const firstProjectId = () =>
  Array.from(state.projects.keys())[0] || projectIdForIndex(1);

const firstTraceId = () =>
  Array.from(state.traces.keys())[0] || traceIdForIndex(1);

const clone = (value) => structuredClone(value);

const ok = (result = {}) => ({ status: true, result });

const notFound = (message = "Not found") => ({
  status: false,
  result: message,
});

const readJsonBody = async (req) => {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const raw = Buffer.concat(chunks).toString("utf8");
  if (!raw) return {};
  try {
    return JSON.parse(raw);
  } catch {
    return {};
  }
};

const writeJson = (res, status, payload) => {
  res.writeHead(status, {
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Headers":
      "authorization,baggage,content-type,sentry-trace,x-csrftoken,x-organization-id,x-requested-with,x-smoke-client-ip,x-workspace-id",
    "Access-Control-Allow-Methods": "GET,POST,PUT,PATCH,DELETE,OPTIONS",
    "Access-Control-Allow-Origin": "*",
    "Content-Type": "application/json",
  });
  res.end(JSON.stringify(payload));
};

const normalizePath = (pathname) =>
  pathname.endsWith("/") ? pathname : `${pathname}/`;

const replaceFixtureIds = (value) => {
  const projectId = firstProjectId();
  const traceId = firstTraceId();
  return JSON.parse(
    JSON.stringify(value)
      .replaceAll("observe-1", projectId)
      .replaceAll("trace-1", traceId),
  );
};

const getProjectRows = () =>
  Array.from(state.projects.values()).map((project) => ({
    created_at: project.created_at,
    id: project.id,
    last_trace_at: project.updated_at,
    name: project.name,
    project_id: project.id,
    project_type: "observe",
    source: "observe",
    trace_type: "observe",
    traces_count: state.traces.size,
    updated_at: project.updated_at,
  }));

const getTraceRows = () =>
  Array.from(state.traces.values()).map((trace) => ({
    duration_ms: 850,
    id: trace.id,
    input: "Summarize onboarding smoke",
    latency: 850,
    name: trace.name,
    output: "The onboarding smoke trace returned a usable answer.",
    project_id: trace.project,
    start_time: trace.created_at,
    status: "success",
    trace_id: trace.id,
  }));

const makeTraceDetail = (traceId) => {
  const trace = state.traces.get(traceId) ||
    Array.from(state.traces.values())[0] || {
      created_at: nowIso(),
      id: traceId,
      name: "Onboarding smoke real trace",
      project: firstProjectId(),
    };

  return {
    annotations: [],
    evals: [],
    observation_spans: [
      {
        children: [],
        observation_span: {
          attributes: {
            "llm.input": "Summarize onboarding smoke",
            "llm.output":
              "The onboarding smoke trace returned a usable answer.",
          },
          duration_ms: 850,
          end_time: trace.created_at,
          id: `${trace.id}-span-1`,
          input: "Summarize onboarding smoke",
          name: "openai.responses.create",
          output: "The onboarding smoke trace returned a usable answer.",
          parent_span_id: null,
          span_id: `${trace.id}-span-1`,
          start_time: trace.created_at,
          status: "success",
          trace_id: trace.id,
        },
      },
    ],
    trace: {
      created_at: trace.created_at,
      duration_ms: 850,
      id: trace.id,
      input: "Summarize onboarding smoke",
      name: trace.name,
      output: "The onboarding smoke trace returned a usable answer.",
      project: trace.project,
      status: "success",
      trace_id: trace.id,
    },
  };
};

const makeActivationState = (url) => {
  let baseName = "newWorkspaceNoGoal";
  const source = url.searchParams.get("source") || "";

  if (state.completedFirstLoop || source === "onboarding_complete") {
    baseName = "observeFirstLoopComplete";
  } else if (
    state.traceReviewed ||
    source === "real_trace_reviewed" ||
    state.evalTemplates.size > 0
  ) {
    baseName = "observeNeedsEvaluator";
  } else if (state.traces.size > 0 || source === "real_trace_created") {
    baseName = "observeFirstTraceReady";
  }

  const payload = replaceFixtureIds(clone(getActivationStateFixture(baseName)));
  const projectId = firstProjectId();
  const traceId = firstTraceId();
  const observeProjectHref = `/dashboard/observe/${projectId}`;
  const traceReviewHref = `/dashboard/observe/${projectId}/trace/${traceId}?source=onboarding&onboarding=review-first-trace`;
  const evaluatorCreateHref = `/dashboard/evaluations/create?source=onboarding&step=data&source_type=trace_project&source_id=${projectId}&trace_id=${traceId}`;
  const evaluatorProjectFocusHref = `/dashboard/observe/${projectId}/llm-tracing?source=onboarding&onboarding=create-evaluator`;
  payload.organization_id = ORG_ID;
  payload.workspace_id = WORKSPACE_ID;
  payload.user_id = USER_ID;
  payload.server_time = nowIso();
  payload.signals = {
    ...(payload.signals || {}),
    first_observe_id: state.projects.size ? projectId : null,
    first_trace_id: state.traces.size ? traceId : null,
    observe_projects: state.projects.size,
    trace_reviews: state.traceReviewed ? 1 : 0,
    traces: state.traces.size,
    // Capability flag: this stub implements POST /accounts/test-trace/, so it
    // advertises support. The real backend sets this true only once its
    // endpoint exists, so a real user never sees a button that 404s.
    test_trace_supported: true,
  };
  payload.route_availability = {
    ...(payload.route_availability || {}),
    observe_project: {
      href: observeProjectHref,
      is_available: true,
      reason: null,
    },
    observe_trace_detail: {
      href: `/dashboard/observe/${projectId}/trace/${traceId}`,
      is_available: true,
      reason: null,
    },
    observe_trace_review: {
      href: traceReviewHref,
      is_available: true,
      reason: null,
    },
    observe_evaluator_create: {
      href: evaluatorCreateHref,
      is_available: true,
      reason: null,
    },
    observe_evaluator_project_focus: {
      href: evaluatorProjectFocusHref,
      is_available: true,
      reason: null,
    },
  };

  if (baseName === "observeFirstTraceReady") {
    payload.recommended_action = {
      ...(payload.recommended_action || {}),
      cta_label: "Review trace",
      href: traceReviewHref,
      is_sample: false,
      title: "First trace received",
    };
  }

  if (baseName === "observeNeedsEvaluator") {
    payload.recommended_action = {
      ...(payload.recommended_action || {}),
      cta_label: "Create quality check",
      href: evaluatorCreateHref,
      is_sample: false,
      title: "Create a quality check",
    };
  }

  if (baseName === "observeFirstLoopComplete") {
    payload.is_activated = true;
    payload.activated_at = nowIso();
    payload.recommended_action = {
      ...(payload.recommended_action || {}),
      cta_label: "Open observe",
      href: observeProjectHref,
      is_sample: false,
      title: "Open observe dashboard",
    };
  }

  return payload;
};

const projectSdkCode = () => ({
  installationGuide: {
    Python: "pip install futureagi traceAI-openai openai",
    TypeScript: "npm install @futureagi/tracer @traceai/openai openai",
  },
  keys: {
    Python:
      'import os\n\nos.environ["FI_API_KEY"] = "<futureagi-api-key>"\nos.environ["FI_SECRET_KEY"] = "<futureagi-secret-key>"',
    TypeScript:
      'process.env.FI_API_KEY = "<futureagi-api-key>";\nprocess.env.FI_SECRET_KEY = "<futureagi-secret-key>";',
  },
  projectAddCode: {
    Python:
      'from fi_instrumentation import register\n\ntrace_provider = register(project_name="Onboarding Workspace")',
    TypeScript:
      'import { register } from "@futureagi/tracer";\n\nconst traceProvider = register({ projectName: "Onboarding Workspace" });',
  },
});

const makeEvalTemplate = (id, payload = {}) => ({
  code: payload.code || "",
  config: {
    code: payload.code || "",
    data_injection: payload.data_injection || {},
    language: payload.code_language || "python",
    model: payload.model || "turing_large",
    required_keys: payload.required_keys || ["output"],
  },
  description: payload.description || "",
  eval_tags: payload.tags || [],
  eval_type: payload.eval_type || "code",
  id,
  instructions: payload.instructions || "",
  model: payload.model || "turing_large",
  name: payload.name || `output-quality-${firstProjectId()}`,
  output_type: payload.output_type || "percentage",
  output_type_normalized: payload.output_type || "percentage",
  pass_threshold: payload.pass_threshold ?? 0.7,
});

const makeEvalRun = (templateId, payload = {}) => {
  const isRepair = state.evalRuns.some((run) => run.template_id === templateId);
  const id = runIdForIndex(state.evalRuns.length + 1);
  const result = isRepair ? "Passed" : "Failed";
  const score = isRepair ? 0.95 : 0.45;
  return {
    created_at: nowIso(),
    detail: {
      reason: isRepair
        ? "The repair run produced a healthy summary."
        : "The first run is intentionally weak so the repair path is visible.",
      result,
      score,
    },
    eval_log_id: id,
    evaluation_id: id,
    id,
    input: payload?.config?.mapping
      ? JSON.stringify(payload.config.mapping)
      : "Trace project run",
    reason: isRepair
      ? "The repair run produced a healthy summary."
      : "The first run is intentionally weak so the repair path is visible.",
    result,
    score,
    source: "trace_project",
    status: "completed",
    template_id: templateId,
  };
};

const evalUsagePayload = (templateId) => {
  const items = state.evalRuns.filter((run) => run.template_id === templateId);
  return {
    chart: [],
    logs: {
      items,
      page: 0,
      page_size: 25,
      total: items.length,
    },
    stats: {
      failed: items.filter((run) => run.result === "Failed").length,
      passed: items.filter((run) => run.result === "Passed").length,
      total: items.length,
    },
  };
};

const maskEndpoint = (value) => {
  const text = String(value || "");
  if (!text) return "";
  if (text.length <= 12) return "****";
  return `${text.slice(0, 8)}...${text.slice(-4)}`;
};

const maskedChannelConfig = (channel) => {
  const config = channel.config || {};
  if (channel.type === "slack_webhook") {
    return config.webhook_url
      ? { webhook_url: maskEndpoint(config.webhook_url) }
      : {};
  }
  if (channel.type === "webhook") {
    return {
      ...(config.url ? { url: maskEndpoint(config.url) } : {}),
      ...(config.secret ? { secret: "****" } : {}),
    };
  }
  return {};
};

const targetIdentifierForChannel = (type, config = {}, displayName = "") => {
  if (type === "slack_webhook") {
    return config.webhook_url ? "Slack webhook configured" : "";
  }
  if (type === "webhook") return maskEndpoint(config.url || displayName);
  return "";
};

const serializeNotificationChannel = (channel) => ({
  config: maskedChannelConfig(channel),
  display_name: channel.display_name,
  id: channel.id,
  is_active: channel.is_active !== false,
  last_test_status: channel.last_test_status || "untested",
  last_tested_at: channel.last_tested_at || null,
  metadata: channel.metadata || {},
  scope: channel.scope || "workspace",
  target_identifier: channel.target_identifier || "",
  type: channel.type,
});

const serializeNotificationPreference = (preference) => ({
  channel: preference.channel,
  enabled: preference.enabled !== false,
  family: preference.family,
  frequency_cap_minutes: preference.frequency_cap_minutes ?? null,
  id: preference.id,
  mute_until: preference.mute_until ?? null,
  scope: preference.scope || "user_workspace",
  settings: preference.settings || {},
  source: "stored",
});

const activeExternalChannelExists = (channel) => {
  if (!["slack", "webhook"].includes(channel)) return true;
  return state.notificationChannels.some(
    (item) =>
      CHANNEL_TYPE_TO_DELIVERY[item.type] === channel &&
      item.is_active !== false,
  );
};

const preferenceRank = (scope) =>
  ({
    user_workspace: 4,
    user: 3,
    workspace: 2,
    organization: 1,
  })[scope] || 0;

const findStoredNotificationPreference = (familyId, channel) =>
  [...state.notificationPreferences]
    .filter(
      (preference) =>
        preference.family === familyId && preference.channel === channel,
    )
    .sort((a, b) => preferenceRank(b.scope) - preferenceRank(a.scope))[0] ||
  null;

const notificationPreferenceDecision = (family, channel) => {
  if (!activeExternalChannelExists(channel)) {
    return {
      allowed: false,
      channel,
      family: family.id,
      preference_id: null,
      reason: "channel_not_configured",
      source: "channel",
    };
  }

  const preference = findStoredNotificationPreference(family.id, channel);
  if (preference) {
    return {
      allowed: preference.enabled !== false,
      channel,
      family: family.id,
      preference_id: preference.id,
      reason: preference.enabled === false ? "user_disabled_family" : null,
      source: preference.scope || "user_workspace",
    };
  }

  if (!family.default_channels.includes(channel)) {
    return {
      allowed: false,
      channel,
      family: family.id,
      preference_id: null,
      reason: "channel_not_enabled",
      source: "default",
    };
  }

  return {
    allowed: true,
    channel,
    family: family.id,
    preference_id: null,
    reason: null,
    source: "default",
  };
};

const notificationSettingsPayload = () => ({
  can_manage_workspace: true,
  channels: state.notificationChannels.map(serializeNotificationChannel),
  decisions: NOTIFICATION_FAMILIES.flatMap((family) =>
    NOTIFICATION_CHANNELS.map((channel) =>
      notificationPreferenceDecision(family, channel),
    ),
  ),
  delivery_logs: state.notificationDeliveryLogs.slice(0, 25),
  families: clone(NOTIFICATION_FAMILIES),
  preferences: state.notificationPreferences.map(
    serializeNotificationPreference,
  ),
});

const upsertNotificationPreference = (item = {}) => {
  if (!item.family || !item.channel) return;
  const scope = item.scope || "user_workspace";
  const existing = state.notificationPreferences.find(
    (preference) =>
      preference.scope === scope &&
      preference.family === item.family &&
      preference.channel === item.channel,
  );
  const next = {
    channel: item.channel,
    enabled: item.enabled !== false,
    family: item.family,
    frequency_cap_minutes: item.frequency_cap_minutes ?? null,
    mute_until: item.mute_until ?? null,
    scope,
    settings: item.settings || {},
  };
  if (existing) {
    Object.assign(existing, next);
    return;
  }
  state.notificationPreferenceCounter += 1;
  state.notificationPreferences.push({
    ...next,
    id: preferenceIdForIndex(state.notificationPreferenceCounter),
  });
};

const upsertNotificationChannel = (item = {}) => {
  if (!item.type) return;
  const existing = item.id
    ? state.notificationChannels.find((channel) => channel.id === item.id)
    : null;
  const previousConfig = existing?.config || {};
  const config = item.config
    ? { ...previousConfig, ...item.config }
    : previousConfig;
  const next = {
    config,
    display_name:
      item.display_name || existing?.display_name || "Workspace alerts",
    id:
      existing?.id ||
      item.id ||
      channelIdForIndex(++state.notificationChannelCounter),
    is_active: item.is_active ?? existing?.is_active ?? true,
    last_test_status: existing?.last_test_status || "untested",
    last_tested_at: existing?.last_tested_at || null,
    metadata: item.metadata || existing?.metadata || {},
    scope: item.scope || existing?.scope || "workspace",
    target_identifier: targetIdentifierForChannel(
      item.type,
      config,
      item.display_name || existing?.display_name,
    ),
    type: item.type,
  };

  if (existing) {
    Object.assign(existing, next);
    return existing;
  }
  state.notificationChannels.push(next);
  return next;
};

const recordChannelTestDelivery = (channel) => {
  state.notificationDeliveryCounter += 1;
  const deliveryChannel = CHANNEL_TYPE_TO_DELIVERY[channel.type] || "webhook";
  state.notificationDeliveryLogs.unshift({
    channel: deliveryChannel,
    created_at: nowIso(),
    family: "workspace_admin",
    id: deliveryLogIdForIndex(state.notificationDeliveryCounter),
    metadata: { dry_run: true },
    notification_key: "notification_channel_test",
    recipient_identifier_masked:
      channel.target_identifier || targetIdentifierForChannel(channel.type),
    recipient_type: channel.type,
    route_url: "",
    sent_at: null,
    severity: "info",
    source_id: channel.id,
    source_type: "notification_channel",
    stage: "test",
    status: "eligible",
    suppressed_reason: null,
  });
};

const handleAuthRoute = async (req, res, path) => {
  if (path === "/accounts/signup/" && req.method === "POST") {
    const body = await readJsonBody(req);
    resetState();
    state.email = body.email || state.email;
    state.fullName = body.full_name || body.fullName || state.fullName;
    writeJson(
      res,
      200,
      ok({ message: "User Created Successfully", user_id: USER_ID }),
    );
    return true;
  }

  if (path === "/accounts/token/" && req.method === "POST") {
    const tokenPayload = Buffer.from(
      JSON.stringify({
        exp: Math.floor(Date.now() / 1000) + 86400,
        sub: USER_ID,
        user_id: USER_ID,
      }),
    ).toString("base64url");
    writeJson(res, 200, {
      access: `eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.${tokenPayload}.mock`,
      refresh: "mock-refresh-token",
    });
    return true;
  }

  if (path === "/accounts/token/refresh/" && req.method === "POST") {
    writeJson(res, 200, {
      access: `mock-access-${Date.now()}`,
      refresh: "mock-refresh-token",
    });
    return true;
  }

  if (path === "/accounts/user-info/" && req.method === "GET") {
    writeJson(res, 200, {
      default_workspace_display_name: "Onboarding Workspace",
      default_workspace_id: WORKSPACE_ID,
      default_workspace_name: "Onboarding Workspace",
      default_workspace_role: "Admin",
      effective_level: 100,
      email: state.email,
      full_name: state.fullName,
      goals: state.onboarding.goals,
      id: USER_ID,
      name: state.fullName,
      onboarding_completed: Boolean(state.onboarding.completed),
      org_level: 100,
      organization: {
        display_name: "Future AGI",
        id: ORG_ID,
        name: "futureagi",
      },
      organization_id: ORG_ID,
      organization_role: "Owner",
      remember_me: true,
      requires_org_setup: false,
      role: state.onboarding.role,
      ws_level: 90,
    });
    return true;
  }

  if (path === "/accounts/organizations/" && req.method === "GET") {
    writeJson(
      res,
      200,
      ok({
        organizations: [
          {
            display_name: "Future AGI",
            id: ORG_ID,
            name: "futureagi",
          },
        ],
      }),
    );
    return true;
  }

  if (path === "/accounts/workspace/list/" && req.method === "GET") {
    writeJson(
      res,
      200,
      ok({
        workspaces: [
          {
            display_name: "Onboarding Workspace",
            id: WORKSPACE_ID,
            name: "Onboarding Workspace",
          },
        ],
      }),
    );
    return true;
  }

  if (path === "/accounts/onboarding/" && req.method === "POST") {
    const body = await readJsonBody(req);
    state.onboarding = {
      completed: true,
      goals: Array.isArray(body.goals) ? body.goals : state.onboarding.goals,
      role: body.role || state.onboarding.role,
    };
    writeJson(res, 200, ok(state.onboarding));
    return true;
  }

  if (path === "/accounts/team/users/" && req.method === "POST") {
    writeJson(res, 200, ok({ users: [] }));
    return true;
  }

  if (path === "/accounts/activation-events/" && req.method === "POST") {
    const body = await readJsonBody(req);
    state.activationEvents.push(body);
    if (body.event_name === "trace_detail_opened") state.traceReviewed = true;
    if (body.event_name === "first_quality_loop_completed") {
      state.completedFirstLoop = true;
    }
    writeJson(res, 200, ok(makeActivationState(new URL("http://local/"))));
    return true;
  }

  if (path === "/accounts/activation-state/" && req.method === "GET") {
    const url = new URL(req.url, `http://${HOST}:${PORT}`);
    writeJson(res, 200, ok(makeActivationState(url)));
    return true;
  }

  if (path === "/accounts/sample-project/" && req.method === "POST") {
    writeJson(res, 200, ok(makeActivationState(new URL("http://local/"))));
    return true;
  }

  if (path === "/accounts/test-trace/" && req.method === "POST") {
    await readJsonBody(req);
    // Ensure an Observe project exists so the activation state can advance from
    // "waiting" to "trace-ready/review" once the test trace is recorded.
    if (state.projects.size === 0) {
      const projectId = projectIdForIndex(1);
      state.projects.set(projectId, {
        created_at: nowIso(),
        id: projectId,
        name: "Onboarding Workspace",
        project_id: projectId,
        source: "observe",
        trace_type: "observe",
        updated_at: nowIso(),
      });
    }
    const id = traceIdForIndex(state.traces.size + 1);
    // The trace is clearly a TEST trace and is excluded from real activation.
    // It advances waiting -> review_first_trace, but does NOT set
    // state.traceReviewed or state.completedFirstLoop, so it never counts as
    // first_quality_loop_completed / real activation.
    state.traces.set(id, {
      created_at: nowIso(),
      id,
      is_test: true,
      name: "Onboarding test trace",
      project: firstProjectId(),
      trace_id: id,
    });
    writeJson(res, 200, ok(makeActivationState(new URL("http://local/"))));
    return true;
  }

  if (path === "/accounts/notification-preferences/" && req.method === "GET") {
    writeJson(res, 200, ok(notificationSettingsPayload()));
    return true;
  }

  if (
    path === "/accounts/notification-preferences/" &&
    req.method === "PATCH"
  ) {
    const body = await readJsonBody(req);
    (body.preferences || []).forEach(upsertNotificationPreference);
    (body.channels || []).forEach(upsertNotificationChannel);
    writeJson(res, 200, ok(notificationSettingsPayload()));
    return true;
  }

  const channelTestMatch = path.match(
    /^\/accounts\/notification-channels\/([^/]+)\/test\/$/,
  );
  if (channelTestMatch && req.method === "POST") {
    const channelId = decodeURIComponent(channelTestMatch[1]);
    const channel = state.notificationChannels.find(
      (item) => item.id === channelId,
    );
    if (!channel) {
      writeJson(res, 404, notFound("Notification channel not found."));
      return true;
    }
    channel.last_tested_at = nowIso();
    channel.last_test_status = "ready";
    recordChannelTestDelivery(channel);
    writeJson(res, 200, ok({ channel: serializeNotificationChannel(channel) }));
    return true;
  }

  return false;
};

const handleProjectRoute = async (req, res, path) => {
  if (path === "/tracer/saved-views/" && req.method === "GET") {
    writeJson(res, 200, ok({ custom_views: [] }));
    return true;
  }

  if (path === "/tracer/project/project_sdk_code/" && req.method === "GET") {
    writeJson(res, 200, ok(projectSdkCode()));
    return true;
  }

  if (path === "/tracer/project/" && req.method === "POST") {
    const body = await readJsonBody(req);
    const id = projectIdForIndex(state.projects.size + 1);
    const project = {
      created_at: nowIso(),
      id,
      name: body.name || `Observe Project ${state.projects.size + 1}`,
      project_id: id,
      source: "observe",
      trace_type: "observe",
      updated_at: nowIso(),
    };
    state.projects.set(id, project);
    writeJson(res, 200, ok({ ...project, project_id: id }));
    return true;
  }

  if (
    [
      "/tracer/project/list_projects/",
      "/tracer/project/list_project_ids/",
    ].includes(path) &&
    req.method === "GET"
  ) {
    const rows = getProjectRows();
    writeJson(
      res,
      200,
      ok({
        metadata: {
          total_pages: rows.length ? 1 : 0,
          total_rows: rows.length,
        },
        projects: rows,
        table: rows,
      }),
    );
    return true;
  }

  const projectMatch = path.match(/^\/tracer\/project\/([^/]+)\/$/);
  if (projectMatch && req.method === "GET") {
    const projectId = decodeURIComponent(projectMatch[1]);
    const project =
      state.projects.get(projectId) || state.projects.get(firstProjectId());
    writeJson(res, project ? 200 : 404, project ? ok(project) : notFound());
    return true;
  }

  return false;
};

const handleTraceRoute = async (req, res, path) => {
  if (path === "/tracer/trace/" && req.method === "POST") {
    const body = await readJsonBody(req);
    const id = traceIdForIndex(state.traces.size + 1);
    const trace = {
      created_at: nowIso(),
      id,
      name: body.name || `Trace ${state.traces.size + 1}`,
      project: body.project || firstProjectId(),
      trace_id: id,
    };
    state.traces.set(id, trace);
    writeJson(res, 200, ok(trace));
    return true;
  }

  if (
    [
      "/tracer/trace/list_traces/",
      "/tracer/trace/list_traces_of_session/",
    ].includes(path) &&
    req.method === "GET"
  ) {
    const rows = getTraceRows();
    writeJson(
      res,
      200,
      ok({
        config: [
          { field: "trace_id", headerName: "Trace ID" },
          { field: "name", headerName: "Name" },
          { field: "output", headerName: "Output" },
        ],
        metadata: {
          total_pages: rows.length ? 1 : 0,
          total_rows: rows.length,
        },
        table: rows,
      }),
    );
    return true;
  }

  if (
    path === "/tracer/observation-span/get_eval_attributes_list/" &&
    req.method === "GET"
  ) {
    writeJson(res, 200, ok(["input", "output", "trace_id", "name"]));
    return true;
  }

  if (
    path === "/tracer/observation-span/list_spans_observe/" &&
    req.method === "GET"
  ) {
    const traceId = firstTraceId();
    const rows = state.traces.size
      ? [
          {
            id: `${traceId}-span-1`,
            input: "Summarize onboarding smoke",
            name: "openai.responses.create",
            output: "The onboarding smoke trace returned a usable answer.",
            span_id: `${traceId}-span-1`,
            trace_id: traceId,
          },
        ]
      : [];
    writeJson(
      res,
      200,
      ok({
        config: [
          { field: "span_id", headerName: "Span ID" },
          { field: "name", headerName: "Name" },
          { field: "output", headerName: "Output" },
        ],
        metadata: {
          total_pages: rows.length ? 1 : 0,
          total_rows: rows.length,
        },
        table: rows,
      }),
    );
    return true;
  }

  const traceMatch = path.match(/^\/tracer\/trace\/([^/]+)\/$/);
  if (traceMatch && req.method === "GET") {
    writeJson(res, 200, ok(makeTraceDetail(decodeURIComponent(traceMatch[1]))));
    return true;
  }

  return false;
};

const handleEvalRoute = async (req, res, path) => {
  if (
    path === "/model-hub/eval-templates/create-v2/" &&
    req.method === "POST"
  ) {
    const body = await readJsonBody(req);
    const id = evalIdForIndex(state.evalTemplates.size + 1);
    const template = makeEvalTemplate(id, body);
    state.evalTemplates.set(id, template);
    writeJson(res, 200, ok(template));
    return true;
  }

  const detailMatch = path.match(
    /^\/model-hub\/eval-templates\/([^/]+)\/detail\/$/,
  );
  if (detailMatch && req.method === "GET") {
    const id = decodeURIComponent(detailMatch[1]);
    const template = state.evalTemplates.get(id) || makeEvalTemplate(id);
    state.evalTemplates.set(id, template);
    writeJson(res, 200, ok(template));
    return true;
  }

  const versionsMatch = path.match(
    /^\/model-hub\/eval-templates\/([^/]+)\/versions\/$/,
  );
  if (versionsMatch && req.method === "GET") {
    writeJson(res, 200, ok({ versions: [] }));
    return true;
  }

  const updateMatch = path.match(
    /^\/model-hub\/eval-templates\/([^/]+)\/update\/$/,
  );
  if (updateMatch && ["PUT", "PATCH", "POST"].includes(req.method)) {
    const id = decodeURIComponent(updateMatch[1]);
    const body = await readJsonBody(req);
    const template = makeEvalTemplate(id, {
      ...(state.evalTemplates.get(id) || {}),
      ...body,
    });
    state.evalTemplates.set(id, template);
    writeJson(res, 200, ok(template));
    return true;
  }

  if (path === "/model-hub/eval-playground/" && req.method === "POST") {
    const body = await readJsonBody(req);
    const templateId =
      body.template_id || Array.from(state.evalTemplates.keys())[0];
    const run = makeEvalRun(templateId, body);
    state.evalRuns.push(run);
    writeJson(
      res,
      200,
      ok({
        log_id: run.id,
        output: run.score,
        reason: run.reason,
        result: run.result,
        score: run.score,
      }),
    );
    return true;
  }

  const usageMatch = path.match(
    /^\/model-hub\/eval-templates\/([^/]+)\/usage\/$/,
  );
  if (usageMatch && req.method === "GET") {
    writeJson(
      res,
      200,
      ok(evalUsagePayload(decodeURIComponent(usageMatch[1]))),
    );
    return true;
  }

  if (path === "/model-hub/eval-templates/list/" && req.method === "GET") {
    writeJson(
      res,
      200,
      ok({ items: Array.from(state.evalTemplates.values()) }),
    );
    return true;
  }

  return false;
};

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${HOST}:${PORT}`);
  const path = normalizePath(url.pathname);
  state.requests.push({
    headers: {
      access_control_request_headers:
        req.headers["access-control-request-headers"] || null,
    },
    method: req.method,
    path,
    search: url.search,
  });

  if (req.method === "OPTIONS") {
    writeJson(res, 200, {});
    return;
  }

  try {
    if (path === "/health/" || path === "/api/public/health/") {
      writeJson(res, 200, ok({}));
      return;
    }

    if (path === "/__debug/requests/" && req.method === "GET") {
      writeJson(res, 200, ok({ requests: state.requests }));
      return;
    }

    if (await handleAuthRoute(req, res, path)) return;
    if (await handleProjectRoute(req, res, path)) return;
    if (await handleTraceRoute(req, res, path)) return;
    if (await handleEvalRoute(req, res, path)) return;

    writeJson(res, 200, ok({ request_id: randomUUID() }));
  } catch (error) {
    writeJson(res, 500, {
      status: false,
      result: error?.message || "Local onboarding API error",
    });
  }
});

server.listen(PORT, HOST, () => {
  console.log(`Onboarding local API listening at http://${HOST}:${PORT}`);
});
