import { verifyConversationProofPack } from "./proofPack";

/**
 * Cloud-only client for the Omega Squad surface.
 *
 * This module intentionally does not import the local Omega server or the shared
 * Axios instance. Squad is a tenant-facing view of a remote Omega deployment;
 * when that deployment is not configured, every operation returns a typed
 * `available: false` result and no localhost request is attempted.
 */

/** @typedef {"bearer" | "cookie"} OmegaSquadAuthMode */

/**
 * @typedef {Object} OmegaSquadConfig
 * @property {string} baseUrl
 * @property {() => (string | null | undefined | Promise<string | null | undefined>)} getToken
 * @property {OmegaSquadAuthMode} authMode
 * @property {string | null} organizationId
 * @property {string | null} workspaceId
 * @property {boolean} allowHostToken
 * @property {boolean} [scopeReady]
 * @property {() => boolean} [isScopeCurrent]
 */

/**
 * @typedef {Object} OmegaUnavailable
 * @property {false} available
 * @property {string} code
 * @property {string} reason
 * @property {number} [status]
 */

const REMOTE_CONFIG_KEYS = [
  "baseUrl",
  "apiUrl",
  "omegaBaseUrl",
  "VITE_OMEGA_SQUAD_API_URL",
  "VITE_OMEGA_BASE_URL",
];

const WORKFORCE_SCHEMA_VERSION = "omega-workforce/v1";
const WORKFORCE_STATUS_VALUES = [
  "idle",
  "working",
  "paused",
  "needs_attention",
  "offline",
];
const REMOTE_SANDBOX_PROVIDER_KINDS = new Set([
  "container",
  "docker",
  "microvm",
  "daytona",
  "e2b",
  "modal",
  "runloop",
  "remote",
]);
const SANDBOX_LIFECYCLE_ACTIONS = new Set(["hibernate", "resume", "destroy"]);
// `interrupt`/`continue` are task-scoped pause/resume verbs in Omega EE. They
// deliberately coexist with the process-scoped `pause`/`resume` controls.
const CONVERSATION_CONTROL_ACTIONS = new Set([
  "cancel",
  "interrupt",
  "continue",
  "pause",
  "resume",
  "stop",
  "kill",
  "retry",
]);

function runtimeConfig() {
  if (typeof window === "undefined") return {};
  return window.__OMEGA_SQUAD_CONFIG__ || {};
}

function buildTimeConfig() {
  // `import.meta.env` is provided by Vite. Keeping this helper small also makes
  // the client usable in a browser test without importing the host API config,
  // whose development fallback intentionally points at localhost.
  return import.meta.env || {};
}

function firstNonEmpty(...values) {
  return (
    values.find(
      (value) => typeof value === "string" && value.trim().length > 0,
    ) || ""
  );
}

function readBoolean(value, fallback = false) {
  if (typeof value === "boolean") return value;
  if (typeof value === "string") {
    if (value.toLowerCase() === "true") return true;
    if (value.toLowerCase() === "false") return false;
  }
  return fallback;
}

function isLocalHost(hostname) {
  const host = hostname.toLowerCase().replace(/^\[|\]$/g, "");
  return (
    host === "localhost" ||
    host.endsWith(".localhost") ||
    host === "127.0.0.1" ||
    host === "0.0.0.0" ||
    host === "::1" ||
    host.endsWith(".local")
  );
}

/**
 * Normalize a remote base URL and reject loopback/local targets. An empty
 * result is deliberate: callers must surface configuration as unavailable
 * instead of silently switching to a local runtime.
 *
 * @param {unknown} value
 * @returns {string}
 */
export function normalizeRemoteBaseUrl(value) {
  if (typeof value !== "string" || value.trim().length === 0) return "";
  try {
    const parsed = new URL(value.trim());
    // Squad is a cloud control-plane surface. Plain HTTP would expose tenant
    // scope headers and short-lived sessions on the wire, even when the host
    // is not loopback, so it is rejected just like a local URL.
    if (parsed.protocol !== "https:") return "";
    if (parsed.username || parsed.password || parsed.search || parsed.hash)
      return "";
    if (isLocalHost(parsed.hostname)) return "";
    return parsed.href.replace(/\/+$/, "");
  } catch {
    return "";
  }
}

/**
 * Read host-authenticated Squad configuration. The default token reader only
 * reads the host's current session token; it never writes a credential. A
 * deployment may inject `getToken` or a session object through
 * `window.__OMEGA_SQUAD_CONFIG__` when its auth provider is not JWT.
 *
 * @param {Partial<OmegaSquadConfig>} [overrides]
 * @returns {OmegaSquadConfig}
 */
export function readOmegaSquadConfig(overrides = {}) {
  const runtime = runtimeConfig();
  const env = buildTimeConfig();
  const configuredBaseUrl = firstNonEmpty(
    overrides.baseUrl,
    ...REMOTE_CONFIG_KEYS.map((key) => runtime[key]),
    env.VITE_OMEGA_SQUAD_API_URL,
    env.VITE_OMEGA_BASE_URL,
  );
  const baseUrl = normalizeRemoteBaseUrl(configuredBaseUrl);

  const injectedToken = overrides.getToken || runtime.getToken;
  const configuredAllowHostToken =
    overrides.allowHostToken ?? runtime.allowHostToken;
  const allowHostToken =
    configuredAllowHostToken === undefined
      ? env.VITE_OMEGA_SQUAD_ALLOW_HOST_TOKEN === "true"
      : readBoolean(configuredAllowHostToken);
  const getToken =
    typeof injectedToken === "function"
      ? injectedToken
      : () => {
          const session =
            typeof window !== "undefined"
              ? window.__OMEGA_SQUAD_SESSION__
              : undefined;
          if (typeof session?.token === "string" && session.token.length > 0)
            return session.token;
          if (
            typeof session?.accessToken === "string" &&
            session.accessToken.length > 0
          )
            return session.accessToken;
          // A FutureAGI JWT is a different auth contract from an Omega session. It
          // is only forwarded when the deployment explicitly enables the adapter.
          if (!allowHostToken) return null;
          try {
            const stores = [];
            const windowStorage =
              typeof window !== "undefined" ? window.localStorage : null;
            if (windowStorage) stores.push(windowStorage);
            if (
              typeof globalThis.localStorage !== "undefined" &&
              globalThis.localStorage !== windowStorage
            ) {
              stores.push(globalThis.localStorage);
            }
            for (const storage of stores) {
              const value = storage.getItem("accessToken");
              if (value) return value;
            }
            return null;
          } catch {
            return null;
          }
        };

  const configuredAuthMode =
    overrides.authMode || runtime.authMode || env.VITE_OMEGA_SQUAD_AUTH_MODE;
  const authMode = configuredAuthMode === "cookie" ? "cookie" : "bearer";
  // Host selectors must come from the current UI contexts, never runtime or
  // storage defaults. The issuer validates these selections against fresh
  // memberships; they are not authorization claims. Native Omega sessions may
  // continue to supply their own scope labels through runtime configuration.
  const organizationId = allowHostToken
    ? overrides.organizationId
    : overrides.organizationId ?? runtime.organizationId;
  const workspaceId = allowHostToken
    ? overrides.workspaceId
    : overrides.workspaceId ?? runtime.workspaceId;

  return {
    baseUrl,
    getToken,
    authMode,
    organizationId: organizationId || null,
    workspaceId: workspaceId || null,
    allowHostToken,
    scopeReady: overrides.scopeReady === true,
    isScopeCurrent: overrides.isScopeCurrent,
  };
}

function unavailable(code, reason, status) {
  return {
    available: false,
    code,
    reason,
    ...(typeof status === "number" ? { status } : {}),
  };
}

const RECEIPT_HEADER_NAMES = [
  "x-request-id",
  "x-correlation-id",
  "x-operation-id",
  "x-omega-operation-id",
  "x-omega-receipt-id",
  "x-idempotency-key",
  "x-omega-idempotency-key",
  "etag",
  "location",
  "retry-after",
];

function responseMetadata(response) {
  const headers = {};
  for (const name of RECEIPT_HEADER_NAMES) {
    const value = response.headers?.get?.(name);
    if (value) headers[name] = value;
  }
  return {
    status: response.status,
    ...(response.statusText ? { statusText: response.statusText } : {}),
    ...(Object.keys(headers).length > 0 ? { headers } : {}),
  };
}

function responseError(response, body) {
  const bodyRecord = body && typeof body === "object" ? body : {};
  const nestedError =
    bodyRecord.error &&
    typeof bodyRecord.error === "object" &&
    !Array.isArray(bodyRecord.error)
      ? bodyRecord.error
      : null;
  const explicitCode =
    typeof bodyRecord.code === "string"
      ? bodyRecord.code
      : typeof bodyRecord.error === "string"
        ? bodyRecord.error
        : typeof nestedError?.code === "string"
          ? nestedError.code
          : null;
  const code = explicitCode
    ? explicitCode
    : response.status === 401 || response.status === 403
      ? "AUTH_REQUIRED"
      : `HTTP_${response.status}`;
  const reason =
    typeof bodyRecord.message === "string"
      ? bodyRecord.message
      : typeof bodyRecord.detail === "string"
        ? bodyRecord.detail
        : typeof nestedError?.message === "string"
          ? nestedError.message
          : `Omega remote request failed (${response.status}).`;
  return {
    ...unavailable(code, reason, response.status),
    transport: responseMetadata(response),
  };
}

async function parseResponse(response) {
  const contentType = response.headers?.get?.("content-type") || "";
  if (contentType.includes("application/json")) return response.json();
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { message: text };
  }
}

function endpoint(baseUrl, path, query) {
  const url = new URL(`${baseUrl}${path}`);
  if (query) {
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== "")
        url.searchParams.set(key, String(value));
    });
  }
  return url.toString();
}

/**
 * @typedef {Object} OmegaSquadClient
 * @property {() => Promise<Object | OmegaUnavailable>} getCapabilities
 * @property {() => Promise<Object | OmegaUnavailable>} listWorkforce
 * @property {() => Promise<Object | OmegaUnavailable>} listWorkforceModels
 * @property {() => Promise<Object | OmegaUnavailable>} listWorkforceSharingCandidates
 * @property {(id: string) => Promise<Object | OmegaUnavailable>} getWorkforceAgent
 * @property {(input: Object, idempotencyKey: string) => Promise<Object | OmegaUnavailable>} draftWorkforceAgent
 * @property {(input: Object, idempotencyKey?: string) => Promise<Object | OmegaUnavailable>} createWorkforceAgent
 * @property {(id: string, input: Object, idempotencyKey?: string) => Promise<Object | OmegaUnavailable>} updateWorkforceAgent
 * @property {(id: string, action: string, idempotencyKey?: string) => Promise<Object | OmegaUnavailable>} controlWorkforceAgent
 * @property {(id: string) => Promise<Object | OmegaUnavailable>} listWorkforceAgentRevisions
 * @property {(id: string, version: number, input?: Object, idempotencyKey?: string) => Promise<Object | OmegaUnavailable>} rollbackWorkforceAgentRevision
 * @property {(id: string) => Promise<Object | OmegaUnavailable>} listWorkforceAgentDeployments
 * @property {(id: string, version: number, input?: Object, idempotencyKey?: string) => Promise<Object | OmegaUnavailable>} rollbackWorkforceAgentDeployment
 * @property {(id: string) => Promise<Object | OmegaUnavailable>} listWorkforceAgentRuns
 * @property {(id: string, runId: string) => Promise<Object | OmegaUnavailable>} getWorkforceAgentRun
 * @property {(id: string, runId: string) => Promise<Object | OmegaUnavailable>} getWorkforceAgentRunIdentity
 * @property {() => Promise<Object | OmegaUnavailable>} listAgentTeams
 * @property {(input: Object, idempotencyKey?: string) => Promise<Object | OmegaUnavailable>} createAgentTeam
 * @property {(id: string, input: Object, idempotencyKey?: string) => Promise<Object | OmegaUnavailable>} updateAgentTeam
 * @property {(id: string, idempotencyKey?: string) => Promise<Object | OmegaUnavailable>} deleteAgentTeam
 * @property {(query?: Object) => Promise<Object | OmegaUnavailable>} listConversations
 * @property {(input: string, options?: Object) => Promise<Object | OmegaUnavailable>} createConversation
 * @property {(id: string) => Promise<Object | OmegaUnavailable>} getConversation
 * @property {(id: string, options?: Object) => Promise<Object | OmegaUnavailable>} openConversationEvents
 * @property {(id: string, options?: Object) => Promise<Object | OmegaUnavailable>} getConversationEvents
 * @property {(id: string) => Promise<Object | OmegaUnavailable>} getConversationEvidence
 * @property {(id: string) => Promise<Object | OmegaUnavailable>} getConversationProofPack
 * @property {(id: string) => Promise<Object | OmegaUnavailable>} getConversationIsolationReceipt
 * @property {(id: string, input: string, options?: Object) => Promise<Object | OmegaUnavailable>} sendConversationMessage
 * @property {(id: string) => Promise<Object | OmegaUnavailable>} getConversationControlStatus
 * @property {(id: string) => Promise<Object | OmegaUnavailable>} getConversationTree
 * @property {(id: string, query?: Object) => Promise<Object | OmegaUnavailable>} getConversationObservations
 * @property {(id: string) => Promise<Object | OmegaUnavailable>} listConversationApprovals
 * @property {(id: string, action: string, input?: Object, idempotencyKey?: string) => Promise<Object | OmegaUnavailable>} controlConversation
 * @property {(id: string, input?: Object) => Promise<Object | OmegaUnavailable>} rateConversation
 * @property {(id: string, input?: Object) => Promise<Object | OmegaUnavailable>} forkConversation
 * @property {() => Promise<Object | OmegaUnavailable>} listApprovals
 * @property {(id: string, input: Object, idempotencyKey?: string) => Promise<Object | OmegaUnavailable>} resolveApproval
 * @property {() => Promise<Object | OmegaUnavailable>} listEnvironments
 * @property {(id: string) => Promise<Object | OmegaUnavailable>} getEnvironment
 * @property {(id: string) => Promise<Object | OmegaUnavailable>} listEnvironmentRevisions
 * @property {(input: Object, idempotencyKey?: string) => Promise<Object | OmegaUnavailable>} createEnvironment
 * @property {(id: string, input: Object, idempotencyKey?: string) => Promise<Object | OmegaUnavailable>} reviseEnvironment
 * @property {(id: string, input: Object, idempotencyKey?: string) => Promise<Object | OmegaUnavailable>} controlEnvironment
 * @property {(query?: Object) => Promise<Object | OmegaUnavailable>} listSchedules
 * @property {(input: Object, idempotencyKey?: string) => Promise<Object | OmegaUnavailable>} createSchedule
 * @property {(id: string) => Promise<Object | OmegaUnavailable>} getSchedule
 * @property {(id: string, input: Object, idempotencyKey?: string) => Promise<Object | OmegaUnavailable>} updateSchedule
 * @property {(id: string, idempotencyKey?: string) => Promise<Object | OmegaUnavailable>} deleteSchedule
 * @property {(id: string, input?: Object, idempotencyKey?: string) => Promise<Object | OmegaUnavailable>} runSchedule
 * @property {(query?: Object) => Promise<Object | OmegaUnavailable>} listConnectors
 * @property {(id: string) => Promise<Object | OmegaUnavailable>} getConnector
 * @property {(input: Object, idempotencyKey?: string) => Promise<Object | OmegaUnavailable>} buildConnector
 * @property {(id: string, input?: Object, idempotencyKey?: string) => Promise<Object | OmegaUnavailable>} connectConnector
 * @property {(id: string, input?: Object) => Promise<Object | OmegaUnavailable>} testConnector
 * @property {() => Promise<Object | OmegaUnavailable>} listCustomConnectors
 * @property {(input: Object, idempotencyKey: string) => Promise<Object | OmegaUnavailable>} createCustomConnector
 * @property {(id: string) => Promise<Object | OmegaUnavailable>} getCustomConnector
 * @property {(id: string) => Promise<Object | OmegaUnavailable>} getCustomConnectorReview
 * @property {(id: string) => Promise<Object | OmegaUnavailable>} getCustomConnectorRunCapability
 * @property {(id: string, input?: Object, idempotencyKey?: string) => Promise<Object | OmegaUnavailable>} connectCustomConnector
 * @property {(id: string, input?: Object, idempotencyKey?: string) => Promise<Object | OmegaUnavailable>} disconnectCustomConnector
 * @property {(id: string) => Promise<Object | OmegaUnavailable>} getConnectorOAuth
 * @property {(id: string, input?: Object, idempotencyKey?: string) => Promise<Object | OmegaUnavailable>} startConnectorOAuth
 * @property {(query?: Object) => Promise<Object | OmegaUnavailable>} listCredentialMetadata
 * @property {(id: string) => Promise<Object | OmegaUnavailable>} getCredentialMetadata
 * @property {(id: string) => Promise<Object | OmegaUnavailable>} listCredentialBindings
 * @property {(id: string, query?: Object) => Promise<Object | OmegaUnavailable>} getConnectorDataOverview
 * @property {(id: string, query?: Object) => Promise<Object | OmegaUnavailable>} listConnectorSources
 * @property {(id: string, query?: Object) => Promise<Object | OmegaUnavailable>} listConnectorSyncs
 * @property {(id: string, query?: Object) => Promise<Object | OmegaUnavailable>} listConnectorSyncSchedules
 * @property {() => Promise<Object | OmegaUnavailable>} listMemoryScopes
 * @property {(query?: Object) => Promise<Object | OmegaUnavailable>} getMemoryEvents
 * @property {(id: string) => Promise<Object | OmegaUnavailable>} getMemoryEvent
 * @property {(id: string) => Promise<Object | OmegaUnavailable>} getMemoryLineage
 * @property {(query?: Object) => Promise<Object | OmegaUnavailable>} getMemoryRecall
 * @property {(query?: Object) => Promise<Object | OmegaUnavailable>} getMemoryGraph
 * @property {(query?: Object) => Promise<Object | OmegaUnavailable>} getMemoryTimeline
 * @property {(query?: Object) => Promise<Object | OmegaUnavailable>} getMemoryConsolidation
 * @property {(query?: Object) => Promise<Object | OmegaUnavailable>} exportMemory
 * @property {(query?: {limit?: number, cursor?: string}) => Promise<Object | OmegaUnavailable>} getTenantAudit
 * @property {(snapshot?: string | null) => Promise<Object | OmegaUnavailable>} getTenantAuditIntegrity
 * @property {() => Promise<Object | OmegaUnavailable>} listWorkspaceRatings
 * @property {() => Promise<Object | OmegaUnavailable>} listBillingPlans
 * @property {() => Promise<Object | OmegaUnavailable>} getBillingRateCard
 * @property {() => Promise<Object | OmegaUnavailable>} getBillingMeter
 * @property {() => Promise<Object | OmegaUnavailable>} listCreditPacks
 * @property {(query?: Object) => Promise<Object | OmegaUnavailable>} listBillingAccounts
 * @property {(id: string) => Promise<Object | OmegaUnavailable>} getBillingSubscription
 * @property {(id: string) => Promise<Object | OmegaUnavailable>} getBillingSeats
 * @property {(id: string, query?: Object) => Promise<Object | OmegaUnavailable>} getBillingSeatQuote
 * @property {(id: string, query?: Object) => Promise<Object | OmegaUnavailable>} getBillingUsage
 * @property {(id: string, query?: Object) => Promise<Object | OmegaUnavailable>} getBillingUsageAttribution
 * @property {(id: string, query?: Object) => Promise<Object | OmegaUnavailable>} getBillingUsageRecords
 * @property {(id: string, query?: Object) => Promise<Object | OmegaUnavailable>} getBillingInvoices
 * @property {(id: string, query?: Object) => Promise<Object | OmegaUnavailable>} getBillingMeterReconciliation
 * @property {(id: string, query?: Object) => Promise<Object | OmegaUnavailable>} getBillingInvoice
 * @property {(id: string, query?: Object) => Promise<Object | OmegaUnavailable>} getBillingCredits
 * @property {(id: string) => Promise<Object | OmegaUnavailable>} getBillingIntegrity
 * @property {() => Promise<Object | OmegaUnavailable>} listSandboxes
 * @property {(id: string) => Promise<Object | OmegaUnavailable>} getSandbox
 * @property {(id: string, query?: Object) => Promise<Object | OmegaUnavailable>} listSandboxFiles
 * @property {(id: string, query?: Object) => Promise<Object | OmegaUnavailable>} readSandboxFile
 * @property {(id: string, input: Object, query?: Object) => Promise<Object | OmegaUnavailable>} writeSandboxFile
 * @property {(id: string, action: "hibernate" | "resume" | "destroy", input?: Object) => Promise<Object | OmegaUnavailable>} controlSandbox
 * @property {(id: string) => Promise<Object | OmegaUnavailable>} listSandboxServices
 * @property {(id: string, input: Object) => Promise<Object | OmegaUnavailable>} startSandboxService
 * @property {(id: string, serviceId: string) => Promise<Object | OmegaUnavailable>} stopSandboxService
 * @property {(id: string, serviceId: string, query?: Object) => Promise<Object | OmegaUnavailable>} getSandboxServiceLogs
 * @property {(id: string, serviceId: string) => Promise<Object | OmegaUnavailable>} mintSandboxPreview
 * @property {() => Promise<Object | OmegaUnavailable>} getMemoryStatus
 * @property {() => Promise<Object | OmegaUnavailable>} getSocietyStatus
 * @property {() => Promise<Object | OmegaUnavailable>} getFleetHealth
 * @property {(query?: Object) => Promise<Object | OmegaUnavailable>} listChannels
 * @property {(channelId: string, query?: Object) => Promise<Object | OmegaUnavailable>} listChannelSubscriptions
 * @property {(channelId: string, input: Object, idempotencyKey?: string) => Promise<Object | OmegaUnavailable>} createChannelSubscription
 * @property {(channelId: string, subscriptionId: string, input?: Object) => Promise<Object | OmegaUnavailable>} testChannelSubscription
 * @property {(channelId: string, subscriptionId: string, input: Object, idempotencyKey?: string) => Promise<Object | OmegaUnavailable>} rotateChannelSubscriptionSecret
 * @property {(channelId: string, subscriptionId: string, idempotencyKey?: string) => Promise<Object | OmegaUnavailable>} deleteChannelSubscription
 * @property {(channelId: string, query?: Object) => Promise<Object | OmegaUnavailable>} listChannelEvents
 * @property {(channelId: string, eventKey: string, query?: Object) => Promise<Object | OmegaUnavailable>} listChannelEventDeliveries
 * @property {(channelId: string, query?: Object) => Promise<Object | OmegaUnavailable>} getChannelDeliverySlo
 * @property {(channelId: string, eventKey: string, deliveryId: string, idempotencyKey?: string) => Promise<Object | OmegaUnavailable>} redriveChannelDelivery
 */

/**
 * Create a typed remote client. The optional `fetchImpl` is intended for tests;
 * production callers use the browser's `fetch` and pass no second argument.
 *
 * @param {OmegaSquadConfig} config
 * @param {{fetchImpl?: typeof fetch}} [options]
 * @returns {OmegaSquadClient}
 */
export function createOmegaSquadClient(config, options = {}) {
  const suppliedConfig = config || readOmegaSquadConfig();
  // Re-validate here as well as in `readOmegaSquadConfig`: callers can inject a
  // config object directly, and that path must not bypass the cloud-only
  // loopback guard.
  const safeConfig = {
    ...suppliedConfig,
    baseUrl: normalizeRemoteBaseUrl(suppliedConfig.baseUrl),
  };
  const fetchImpl = options.fetchImpl || globalThis.fetch;
  const scopeUnavailable = () =>
    unavailable(
      "SCOPE_UNAVAILABLE",
      "Select an organization and workspace after your session is ready to use Squad.",
    );
  const hasCurrentScope = () =>
    !safeConfig.allowHostToken ||
    (safeConfig.scopeReady === true &&
      typeof safeConfig.organizationId === "string" &&
      safeConfig.organizationId.trim().length > 0 &&
      typeof safeConfig.workspaceId === "string" &&
      safeConfig.workspaceId.trim().length > 0 &&
      (!safeConfig.isScopeCurrent || safeConfig.isScopeCurrent()));

  const request = async (path, requestOptions = {}) => {
    if (!hasCurrentScope()) return scopeUnavailable();
    if (!safeConfig.baseUrl) {
      return unavailable(
        "NOT_CONFIGURED",
        "A remote Omega deployment URL is required for Squad.",
      );
    }
    if (typeof fetchImpl !== "function") {
      return unavailable(
        "FETCH_UNAVAILABLE",
        "This browser cannot make remote Omega requests.",
      );
    }

    let token = null;
    try {
      token = await safeConfig.getToken?.();
    } catch {
      return unavailable(
        "AUTH_UNAVAILABLE",
        "The host session could not provide an Omega credential.",
      );
    }
    if (!hasCurrentScope()) return scopeUnavailable();

    const headers = {
      Accept: "application/json",
      ...(requestOptions.headers || {}),
    };
    if (
      safeConfig.authMode === "bearer" &&
      typeof token === "string" &&
      token.length > 0
    ) {
      headers.Authorization = `Bearer ${token}`;
    }
    if (safeConfig.organizationId)
      headers["X-Organization-Id"] = safeConfig.organizationId;
    if (safeConfig.workspaceId)
      headers["X-Workspace-Id"] = safeConfig.workspaceId;
    const init = {
      method: requestOptions.method || "GET",
      headers,
      // Bearer mode must not send ambient browser cookies to a different cloud origin.
      // Cookie mode is explicit and requires the deployment's same-site gateway/CORS policy.
      credentials: safeConfig.authMode === "cookie" ? "include" : "omit",
    };
    if (requestOptions.signal) init.signal = requestOptions.signal;
    if (requestOptions.body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(requestOptions.body);
    }

    let response;
    try {
      response = await fetchImpl(
        endpoint(safeConfig.baseUrl, path, requestOptions.query),
        init,
      );
    } catch {
      return unavailable(
        "NETWORK_ERROR",
        "The remote Omega control plane could not be reached.",
      );
    }
    if (!hasCurrentScope()) {
      // Closing a superseded stream is best effort; a transport cancellation
      // failure must not turn a scope refusal into an unhandled exception.
      try {
        await response.body?.cancel?.();
      } catch {
        /* already closed */
      }
      return scopeUnavailable();
    }
    // Event streams must remain unread so the caller can consume the response body with a
    // ReadableStream/SSE parser. Error responses still get parsed below so callers receive
    // the same typed error envelope as every JSON method.
    const transport = responseMetadata(response);
    if (requestOptions.returnResponse && response.ok)
      return { available: true, response, transport };
    const body = await parseResponse(response);
    if (!hasCurrentScope()) return scopeUnavailable();
    if (!response.ok) return responseError(response, body);
    return {
      available: true,
      ...(body && typeof body === "object" && !Array.isArray(body)
        ? body
        : { value: body }),
      // A durable Omega mutation receipt may be in the JSON body, response
      // headers, or both. Preserve the transport evidence so an ambiguous
      // browser result can be reconciled without inventing success locally.
      transport,
    };
  };

  const getConversationProofPack = async (id) => {
    const result = await request(
      `/workspace/conversations/${encodeURIComponent(id)}/proof-pack`,
    );
    if (!result.available) return result;
    const responseBody = Object.fromEntries(
      Object.entries(result).filter(
        ([key]) => key !== "available" && key !== "transport",
      ),
    );
    const proofPack =
      responseBody.proofPack && typeof responseBody.proofPack === "object"
        ? responseBody.proofPack
        : responseBody;
    const verification = await verifyConversationProofPack(proofPack, {
      taskId: id,
    });
    if (!verification.available) return verification;
    if (!verification.ok) {
      return {
        available: false,
        code: "PROOF_UNVERIFIED",
        reason: "The remote proof pack failed its integrity checks.",
        verification,
        isolation: verification.isolation,
      };
    }
    return { ...result, proofPack, verification };
  };

  const getConversationIsolationReceipt = async (id) => {
    const result = await getConversationProofPack(id);
    if (!result.available) {
      if (
        result.code === "PROOF_UNVERIFIED" &&
        result.isolation?.status === "invalid"
      ) {
        return {
          ...unavailable(
            "ISOLATION_UNVERIFIED",
            "The remote proof pack did not prove per-task isolation cleanup.",
          ),
          verification: result.verification,
          isolation: result.isolation,
        };
      }
      return result;
    }
    const isolation = result.verification?.isolation;
    if (!isolation || isolation.status === "unknown") {
      return unavailable(
        "ISOLATION_UNVERIFIED",
        "The remote proof pack did not include observed per-task isolation evidence.",
      );
    }
    if (isolation.status !== "verified") {
      return unavailable(
        "ISOLATION_UNVERIFIED",
        "The remote proof pack did not prove per-task isolation cleanup.",
      );
    }
    return {
      available: true,
      receipt: isolation.receipt,
      verification: isolation,
      proofPack: result.proofPack,
    };
  };

  return {
    getCapabilities: () => request("/workspace/capabilities"),
    listOrganizations: async () => {
      const result = await request("/auth/orgs");
      return result.available
        ? { ...result, orgs: Array.isArray(result.orgs) ? result.orgs : [] }
        : result;
    },
    listOrganizationMembers: async (orgId) => {
      if (!orgId)
        return unavailable("INVALID_INPUT", "An organization id is required.");
      const result = await request(
        `/auth/orgs/${encodeURIComponent(orgId)}/members`,
      );
      return result.available
        ? {
            ...result,
            members: Array.isArray(result.members) ? result.members : [],
          }
        : result;
    },
    listOrganizationInvites: async (orgId) => {
      if (!orgId)
        return unavailable("INVALID_INPUT", "An organization id is required.");
      const result = await request(
        `/auth/orgs/${encodeURIComponent(orgId)}/invites`,
      );
      return result.available
        ? {
            ...result,
            invites: Array.isArray(result.invites) ? result.invites : [],
          }
        : result;
    },
    getOrganizationActivity: (orgId, query) => {
      if (!orgId)
        return Promise.resolve(
          unavailable("INVALID_INPUT", "An organization id is required."),
        );
      return request(`/auth/orgs/${encodeURIComponent(orgId)}/activity`, {
        query,
      });
    },
    getOrganizationPlan: (orgId) => {
      if (!orgId)
        return Promise.resolve(
          unavailable("INVALID_INPUT", "An organization id is required."),
        );
      return request(`/auth/orgs/${encodeURIComponent(orgId)}/plan`);
    },
    createOrganizationInvite: (orgId, input, idempotencyKey) => {
      if (!orgId)
        return Promise.resolve(
          unavailable("INVALID_INPUT", "An organization id is required."),
        );
      return request(`/auth/orgs/${encodeURIComponent(orgId)}/invites`, {
        method: "POST",
        ...(idempotencyKey
          ? {
              headers: {
                "Idempotency-Key": idempotencyKey,
                "Cache-Control": "no-store",
              },
            }
          : {}),
        body: input,
      });
    },
    revokeOrganizationInvite: (orgId, inviteId, idempotencyKey) => {
      if (!orgId || !inviteId)
        return Promise.resolve(
          unavailable(
            "INVALID_INPUT",
            "An organization and invite id are required.",
          ),
        );
      return request(
        `/auth/orgs/${encodeURIComponent(orgId)}/invites/${encodeURIComponent(inviteId)}`,
        {
          method: "DELETE",
          ...(idempotencyKey
            ? { headers: { "Idempotency-Key": idempotencyKey } }
            : {}),
        },
      );
    },
    updateOrganizationMemberRole: (orgId, accountId, role, idempotencyKey) => {
      if (!orgId || !accountId)
        return Promise.resolve(
          unavailable(
            "INVALID_INPUT",
            "An organization and account id are required.",
          ),
        );
      return request(
        `/auth/orgs/${encodeURIComponent(orgId)}/members/${encodeURIComponent(accountId)}`,
        {
          method: "PATCH",
          ...(idempotencyKey
            ? { headers: { "Idempotency-Key": idempotencyKey } }
            : {}),
          body: { role },
        },
      );
    },
    removeOrganizationMember: (orgId, accountId, idempotencyKey) => {
      if (!orgId || !accountId)
        return Promise.resolve(
          unavailable(
            "INVALID_INPUT",
            "An organization and account id are required.",
          ),
        );
      return request(
        `/auth/orgs/${encodeURIComponent(orgId)}/members/${encodeURIComponent(accountId)}`,
        {
          method: "DELETE",
          ...(idempotencyKey
            ? { headers: { "Idempotency-Key": idempotencyKey } }
            : {}),
        },
      );
    },
    /**
     * Customer-facing workforce routes derive account/org/workspace ownership on the
     * Omega server from the authenticated session. Scope headers are attached once by
     * request() (when the host injected a provider-derived scope); callers cannot select
     * a different tenant per operation.
     */
    listWorkforce: async () => {
      const result = await request("/workspace/workforce");
      if (!result.available) return result;
      return {
        ...result,
        agents: Array.isArray(result.agents) ? result.agents : [],
        teams: Array.isArray(result.teams) ? result.teams : [],
        connectorBindings: Array.isArray(result.connectorBindings)
          ? result.connectorBindings
          : [],
      };
    },
    listWorkforceModels: () => request("/workspace/workforce/models"),
    listWorkspaceSkills: () => request("/workspace/skills"),
    getWorkspaceSkill: (id) =>
      request(`/workspace/skills/${encodeURIComponent(id)}`),
    listWorkspaceSkillRevisions: (id) =>
      request(`/workspace/skills/${encodeURIComponent(id)}/revisions`),
    getWorkspaceSkillRevision: (id, revision) =>
      request(
        `/workspace/skills/${encodeURIComponent(id)}/revisions/${encodeURIComponent(revision)}`,
      ),
    createWorkspaceSkill: (input, idempotencyKey) => {
      if (typeof idempotencyKey !== "string" || !idempotencyKey.trim())
        return Promise.resolve(
          unavailable(
            "INVALID_INPUT",
            "Creating a workspace skill requires a request key.",
          ),
        );
      return request("/workspace/skills", {
        method: "POST",
        body: input,
        headers: { "Idempotency-Key": idempotencyKey },
      });
    },
    updateWorkspaceSkill: (id, input) =>
      request(`/workspace/skills/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: input,
      }),
    archiveWorkspaceSkill: (id, input) =>
      request(`/workspace/skills/${encodeURIComponent(id)}/archive`, {
        method: "POST",
        body: input,
      }),
    listWorkforceSharingCandidates: ({ query } = {}) =>
      request(
        `/workspace/workforce/sharing-candidates${typeof query === "string" && query.trim() ? `?query=${encodeURIComponent(query.trim())}` : ""}`,
      ),
    getWorkforceAgent: async (id) => {
      const result = await request(
        `/workspace/workforce/agents/${encodeURIComponent(id)}`,
      );
      return result.available
        ? { ...result, agent: result.agent || result }
        : result;
    },
    /**
     * Read the tenant-scoped tool-policy projection for an agent. The server
     * owns the available catalog and effective/operator floor; the browser
     * must not infer either from its workforce list.
     */
    getWorkforceAgentTools: (id) => {
      if (!id)
        return Promise.resolve(
          unavailable("INVALID_INPUT", "An agent id is required."),
        );
      return request(
        `/workspace/workforce/agents/${encodeURIComponent(id)}/tools`,
      );
    },
    draftWorkforceAgent: (input, idempotencyKey) => {
      if (typeof idempotencyKey !== "string" || !idempotencyKey.trim()) {
        return Promise.resolve(
          unavailable(
            "INVALID_INPUT",
            "A request key is required to draft an agent.",
          ),
        );
      }
      return request("/workspace/workforce/agent-drafts", {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey },
        body: input,
      });
    },
    createWorkforceAgent: (input, idempotencyKey) =>
      request("/workspace/workforce/agents", {
        method: "POST",
        ...(typeof idempotencyKey === "string" && idempotencyKey.length > 0
          ? { headers: { "Idempotency-Key": idempotencyKey } }
          : {}),
        body: input,
      }),
    updateWorkforceAgent: (id, input, idempotencyKey) =>
      request(`/workspace/workforce/agents/${encodeURIComponent(id)}`, {
        method: "PATCH",
        ...(typeof idempotencyKey === "string" && idempotencyKey.length > 0
          ? { headers: { "Idempotency-Key": idempotencyKey } }
          : {}),
        body: input,
      }),
    controlWorkforceAgent: (id, action, idempotencyKey) =>
      request(`/workspace/workforce/agents/${encodeURIComponent(id)}/control`, {
        method: "POST",
        ...(typeof idempotencyKey === "string" && idempotencyKey.length > 0
          ? { headers: { "Idempotency-Key": idempotencyKey } }
          : {}),
        body: { action },
      }),
    listWorkforceAgentRevisions: (id) =>
      request(
        `/workspace/workforce/agents/${encodeURIComponent(id)}/revisions`,
      ),
    rollbackWorkforceAgentRevision: (id, version, input = {}, idempotencyKey) =>
      request(
        `/workspace/workforce/agents/${encodeURIComponent(id)}/revisions/${encodeURIComponent(version)}/rollback`,
        {
          method: "POST",
          ...(typeof idempotencyKey === "string" && idempotencyKey.length > 0
            ? { headers: { "Idempotency-Key": idempotencyKey } }
            : {}),
          body: input,
        },
      ),
    listWorkforceAgentDeployments: (id) =>
      request(
        `/workspace/workforce/agents/${encodeURIComponent(id)}/deployments`,
      ),
    rollbackWorkforceAgentDeployment: (
      id,
      version,
      input = {},
      idempotencyKey,
    ) =>
      request(
        `/workspace/workforce/agents/${encodeURIComponent(id)}/deployments/${encodeURIComponent(version)}/rollback`,
        {
          method: "POST",
          ...(typeof idempotencyKey === "string" && idempotencyKey.length > 0
            ? { headers: { "Idempotency-Key": idempotencyKey } }
            : {}),
          body: input,
        },
      ),
    listWorkforceAgentRuns: (id) =>
      request(`/workspace/workforce/agents/${encodeURIComponent(id)}/runs`),
    getWorkforceAgentRun: (id, runId) =>
      request(
        `/workspace/workforce/agents/${encodeURIComponent(id)}/runs/${encodeURIComponent(runId)}`,
      ),
    getWorkforceAgentRunIdentity: (id, runId) =>
      request(
        `/workspace/workforce/agents/${encodeURIComponent(id)}/runs/${encodeURIComponent(runId)}/identity`,
      ),
    listAgentTeams: async () => {
      const result = await request("/workspace/agent-teams");
      return result.available
        ? { ...result, teams: Array.isArray(result.teams) ? result.teams : [] }
        : result;
    },
    createAgentTeam: (input, idempotencyKey) =>
      request("/workspace/agent-teams", {
        method: "POST",
        ...(idempotencyKey
          ? { headers: { "Idempotency-Key": idempotencyKey } }
          : {}),
        body: input,
      }),
    updateAgentTeam: (id, input, idempotencyKey) => {
      if (!id)
        return Promise.resolve(
          unavailable("INVALID_INPUT", "An agent team id is required."),
        );
      return request(`/workspace/agent-teams/${encodeURIComponent(id)}`, {
        method: "PATCH",
        ...(idempotencyKey
          ? { headers: { "Idempotency-Key": idempotencyKey } }
          : {}),
        body: input,
      });
    },
    deleteAgentTeam: (id, idempotencyKey) => {
      if (!id)
        return Promise.resolve(
          unavailable("INVALID_INPUT", "An agent team id is required."),
        );
      return request(`/workspace/agent-teams/${encodeURIComponent(id)}`, {
        method: "DELETE",
        ...(idempotencyKey
          ? { headers: { "Idempotency-Key": idempotencyKey } }
          : {}),
      });
    },
    listConversations: async (query) => {
      const result = await request("/workspace/conversations", { query });
      return result.available
        ? {
            ...result,
            conversations: Array.isArray(result.conversations)
              ? result.conversations
              : [],
          }
        : result;
    },
    createConversation: (input, options = {}) => {
      const { idempotencyKey, ...conversationOptions } = options;
      return request("/workspace/conversations", {
        method: "POST",
        ...(typeof idempotencyKey === "string" && idempotencyKey.length > 0
          ? { headers: { "Idempotency-Key": idempotencyKey } }
          : {}),
        body: { input, ...conversationOptions },
      });
    },
    getConversation: (id) =>
      request(`/workspace/conversations/${encodeURIComponent(id)}`),
    openConversationEvents: (id, options = {}) =>
      request(`/workspace/conversations/${encodeURIComponent(id)}/events`, {
        query:
          options.after === undefined ? undefined : { after: options.after },
        headers: {
          Accept: "text/event-stream",
          ...(options.lastEventId === undefined
            ? {}
            : { "Last-Event-ID": String(options.lastEventId) }),
        },
        signal: options.signal,
        returnResponse: true,
      }),
    // Alias with a GET-shaped name for callers that do not need to distinguish a stream
    // from ordinary conversation reads. The returned Response body is intentionally open.
    getConversationEvents: (id, options = {}) =>
      request(`/workspace/conversations/${encodeURIComponent(id)}/events`, {
        query:
          options.after === undefined ? undefined : { after: options.after },
        headers: {
          Accept: "text/event-stream",
          ...(options.lastEventId === undefined
            ? {}
            : { "Last-Event-ID": String(options.lastEventId) }),
        },
        signal: options.signal,
        returnResponse: true,
      }),
    getConversationEvidence: (id) =>
      request(`/workspace/conversations/${encodeURIComponent(id)}/evidence`),
    getConversationProofPack,
    getConversationIsolationReceipt,
    sendConversationMessage: (id, input, options = {}) => {
      const { idempotencyKey, ...messageOptions } = options;
      return request(
        `/workspace/conversations/${encodeURIComponent(id)}/messages`,
        {
          method: "POST",
          ...(typeof idempotencyKey === "string" && idempotencyKey.length > 0
            ? { headers: { "Idempotency-Key": idempotencyKey } }
            : {}),
          body: { input, ...messageOptions },
        },
      );
    },
    getConversationControlStatus: (id) =>
      request(
        `/workspace/conversations/${encodeURIComponent(id)}/control-status`,
      ),
    getConversationTree: (id) =>
      request(`/workspace/conversations/${encodeURIComponent(id)}/tree`),
    getConversationObservations: (id, query) =>
      request(
        `/workspace/conversations/${encodeURIComponent(id)}/observations`,
        { query },
      ),
    listConversationApprovals: async (id) => {
      const result = await request(
        `/workspace/conversations/${encodeURIComponent(id)}/approvals`,
      );
      return result.available
        ? {
            ...result,
            approvals: Array.isArray(result.approvals) ? result.approvals : [],
          }
        : result;
    },
    controlConversation: (id, action, input = {}, idempotencyKey) => {
      if (!CONVERSATION_CONTROL_ACTIONS.has(action)) {
        return unavailable(
          "INVALID_ACTION",
          "The requested conversation control action is not supported.",
        );
      }
      const { idempotencyKey: bodyIdempotencyKey, ...controlInput } =
        input || {};
      const requestKey = idempotencyKey || bodyIdempotencyKey;
      return request(
        `/workspace/conversations/${encodeURIComponent(id)}/${action}`,
        {
          method: "POST",
          ...(requestKey ? { headers: { "Idempotency-Key": requestKey } } : {}),
          body: controlInput,
        },
      );
    },
    rateConversation: (id, input = {}) =>
      request(`/workspace/conversations/${encodeURIComponent(id)}/rating`, {
        method: "POST",
        body: input,
      }),
    forkConversation: (id, input = {}) =>
      request(`/workspace/conversations/${encodeURIComponent(id)}/fork`, {
        method: "POST",
        body: input,
      }),
    listApprovals: async () => {
      const result = await request("/workspace/approvals");
      return result.available
        ? {
            ...result,
            approvals: Array.isArray(result.approvals) ? result.approvals : [],
          }
        : result;
    },
    resolveApproval: (id, input, idempotencyKey) =>
      request(`/workspace/approvals/${encodeURIComponent(id)}`, {
        method: "POST",
        ...(idempotencyKey
          ? { headers: { "Idempotency-Key": idempotencyKey } }
          : {}),
        body: idempotencyKey ? { ...input, idempotencyKey } : input,
      }),
    listEnvironments: async () => {
      const result = await request("/workspace/environments");
      return result.available
        ? {
            ...result,
            environments: Array.isArray(result.environments)
              ? result.environments
              : [],
          }
        : result;
    },
    getEnvironment: (id) =>
      request(`/workspace/environments/${encodeURIComponent(id)}`),
    listEnvironmentRevisions: (id) =>
      request(`/workspace/environments/${encodeURIComponent(id)}/revisions`),
    createEnvironment: (input, idempotencyKey) =>
      request("/workspace/environments", {
        method: "POST",
        ...(idempotencyKey
          ? { headers: { "Idempotency-Key": idempotencyKey } }
          : {}),
        body: input,
      }),
    reviseEnvironment: (id, input, idempotencyKey) =>
      request(`/workspace/environments/${encodeURIComponent(id)}/revisions`, {
        method: "POST",
        ...(idempotencyKey
          ? { headers: { "Idempotency-Key": idempotencyKey } }
          : {}),
        body: input,
      }),
    controlEnvironment: (id, input, idempotencyKey) =>
      request(`/workspace/environments/${encodeURIComponent(id)}/control`, {
        method: "POST",
        ...(idempotencyKey
          ? { headers: { "Idempotency-Key": idempotencyKey } }
          : {}),
        body: input,
      }),
    listSchedules: (query) => request("/workspace/schedules", { query }),
    createSchedule: (input, idempotencyKey) =>
      request("/workspace/schedules", {
        method: "POST",
        ...(idempotencyKey
          ? { headers: { "Idempotency-Key": idempotencyKey } }
          : {}),
        body: idempotencyKey ? { ...input, idempotencyKey } : input,
      }),
    getSchedule: (id) =>
      request(`/workspace/schedules/${encodeURIComponent(id)}`),
    updateSchedule: (id, input, idempotencyKey) =>
      request(`/workspace/schedules/${encodeURIComponent(id)}`, {
        method: "PATCH",
        ...(idempotencyKey
          ? { headers: { "Idempotency-Key": idempotencyKey } }
          : {}),
        body: input,
      }),
    deleteSchedule: (id, idempotencyKey) =>
      request(`/workspace/schedules/${encodeURIComponent(id)}`, {
        method: "DELETE",
        ...(idempotencyKey
          ? { headers: { "Idempotency-Key": idempotencyKey } }
          : {}),
      }),
    runSchedule: (id, input = {}, idempotencyKey) =>
      request(`/workspace/schedules/${encodeURIComponent(id)}/run`, {
        method: "POST",
        ...(idempotencyKey
          ? { headers: { "Idempotency-Key": idempotencyKey } }
          : {}),
        body: idempotencyKey ? { ...input, idempotencyKey } : input,
      }),
    listConnectors: async (query) => {
      const result = await request("/os/connectors", { query });
      return result.available
        ? {
            ...result,
            connectors: Array.isArray(result.connectors)
              ? result.connectors
              : [],
          }
        : result;
    },
    getConnector: (id) => request(`/os/connectors/${encodeURIComponent(id)}`),
    // Connector build is an operator-global pay-to-build operation. The
    // customer gateway deliberately denies it, so fail closed locally rather
    // than exposing a method that can only produce a route_not_allowed error.
    buildConnector: async () =>
      unavailable(
        "OPERATOR_ONLY",
        "Connector building requires an Omega operator session.",
      ),
    connectConnector: (id, input = {}, idempotencyKey) =>
      request(`/os/connectors/${encodeURIComponent(id)}/connect`, {
        method: "POST",
        ...(idempotencyKey
          ? { headers: { "Idempotency-Key": idempotencyKey } }
          : {}),
        body: input,
      }),
    testConnector: (id, input = {}) =>
      request(`/os/connectors/${encodeURIComponent(id)}/test`, {
        method: "POST",
        body: input,
      }),
    listCustomConnectors: async () => {
      const result = await request("/os/custom-connectors");
      return result.available
        ? {
            ...result,
            connectors: Array.isArray(result.connectors)
              ? result.connectors
              : [],
          }
        : result;
    },
    createCustomConnector: (input, idempotencyKey) =>
      request("/os/custom-connectors", {
        method: "POST",
        headers: { "Idempotency-Key": idempotencyKey },
        body: input,
      }),
    getCustomConnector: (id) =>
      request(`/os/custom-connectors/${encodeURIComponent(id)}`),
    getCustomConnectorReview: (id) =>
      request(`/os/custom-connectors/${encodeURIComponent(id)}/review`),
    getCustomConnectorRunCapability: (id) =>
      request(`/os/custom-connectors/${encodeURIComponent(id)}/run-capability`),
    connectCustomConnector: (id, input = {}, idempotencyKey) =>
      request(`/os/custom-connectors/${encodeURIComponent(id)}/connect`, {
        method: "POST",
        ...(idempotencyKey
          ? { headers: { "Idempotency-Key": idempotencyKey } }
          : {}),
        body: input,
      }),
    disconnectCustomConnector: (id, input = {}, idempotencyKey) =>
      request(`/os/custom-connectors/${encodeURIComponent(id)}/disconnect`, {
        method: "POST",
        ...(idempotencyKey
          ? { headers: { "Idempotency-Key": idempotencyKey } }
          : {}),
        body: input,
      }),
    getConnectorOAuth: (id) =>
      request(`/os/connectors/${encodeURIComponent(id)}/oauth`),
    startConnectorOAuth: (id, input = {}, idempotencyKey) =>
      request(`/os/connectors/${encodeURIComponent(id)}/oauth/start`, {
        method: "POST",
        ...(idempotencyKey
          ? { headers: { "Idempotency-Key": idempotencyKey } }
          : {}),
        body: input,
      }),
    // Credential vault reads deliberately return value-free metadata only. The
    // gateway does not expose create/rotate/revoke/bind writes, so secrets can
    // never be collected in this browser surface.
    listCredentialMetadata: async (query) => {
      const result = await request("/workspace/credentials", { query });
      return result.available
        ? {
            ...result,
            credentials: Array.isArray(result.credentials)
              ? result.credentials
              : [],
          }
        : result;
    },
    getCredentialMetadata: (id) => {
      if (!id)
        return Promise.resolve(
          unavailable("INVALID_INPUT", "A credential id is required."),
        );
      return request(`/workspace/credentials/${encodeURIComponent(id)}`);
    },
    listCredentialBindings: async (id) => {
      if (!id)
        return Promise.resolve(
          unavailable("INVALID_INPUT", "A credential id is required."),
        );
      const result = await request(
        `/workspace/credentials/${encodeURIComponent(id)}/bindings`,
      );
      return result.available
        ? {
            ...result,
            bindings: Array.isArray(result.bindings) ? result.bindings : [],
          }
        : result;
    },
    // Connector data routes are read-only here. The EE ConnectorDataCaller
    // binds connector id, org, and account on the server; browser query
    // parameters are filters/pagination only and never carry credentials.
    getConnectorDataOverview: (id, query) => {
      if (!id)
        return Promise.resolve(
          unavailable("INVALID_INPUT", "A connector id is required."),
        );
      return request(`/os/connectors/${encodeURIComponent(id)}/data`, {
        query,
      });
    },
    listConnectorSources: async (id, query) => {
      if (!id)
        return unavailable("INVALID_INPUT", "A connector id is required.");
      const result = await request(
        `/os/connectors/${encodeURIComponent(id)}/sources`,
        { query },
      );
      return result.available
        ? {
            ...result,
            sources: Array.isArray(result.sources) ? result.sources : [],
          }
        : result;
    },
    listConnectorSyncs: async (id, query) => {
      if (!id)
        return unavailable("INVALID_INPUT", "A connector id is required.");
      const result = await request(
        `/os/connectors/${encodeURIComponent(id)}/syncs`,
        { query },
      );
      return result.available
        ? { ...result, syncs: Array.isArray(result.syncs) ? result.syncs : [] }
        : result;
    },
    listConnectorSyncSchedules: async (id, query) => {
      if (!id)
        return unavailable("INVALID_INPUT", "A connector id is required.");
      const result = await request(
        `/os/connectors/${encodeURIComponent(id)}/sync-schedules`,
        { query },
      );
      return result.available
        ? {
            ...result,
            schedules: Array.isArray(result.schedules) ? result.schedules : [],
          }
        : result;
    },
    listMemoryScopes: () => request("/os/memory/scopes"),
    getMemoryEvents: (query) => request("/os/memory/events", { query }),
    getMemoryEvent: (id) => {
      if (!id)
        return Promise.resolve(
          unavailable("INVALID_INPUT", "A memory event id is required."),
        );
      return request(`/os/memory/events/${encodeURIComponent(id)}`);
    },
    getMemoryLineage: (id) => {
      if (!id)
        return Promise.resolve(
          unavailable("INVALID_INPUT", "A memory event id is required."),
        );
      return request(`/os/memory/events/${encodeURIComponent(id)}/lineage`);
    },
    getMemoryRecall: (query) => request("/os/memory/recall", { query }),
    getMemoryGraph: (query) => request("/os/memory/graph", { query }),
    getMemoryTimeline: (query) => request("/os/memory/timeline", { query }),
    getMemoryConsolidation: (query) =>
      request("/os/memory/consolidation", { query }),
    exportMemory: (query) => request("/os/memory/export", { query }),
    // These are owner-bound customer projections. Scope comes only from the
    // exchanged session and trusted host headers; tenant selectors are never
    // accepted as query parameters. Keep the operator-global `/os/audit`
    // routes unreachable through this client.
    getTenantAudit: (query = {}) => {
      const safeQuery = {};
      if (Number.isSafeInteger(query?.limit) && query.limit > 0)
        safeQuery.limit = query.limit;
      if (typeof query?.cursor === "string" && query.cursor.length > 0)
        safeQuery.cursor = query.cursor;
      return request("/workspace/audit", { query: safeQuery });
    },
    getTenantAuditIntegrity: (snapshot) => {
      const query =
        typeof snapshot === "string" && snapshot.length > 0
          ? { snapshot }
          : undefined;
      return request("/workspace/audit/integrity", { query });
    },
    listWorkspaceRatings: () => request("/workspace/ratings/summary"),
    listBillingPlans: () => request("/billing/plans"),
    getBillingRateCard: () => request("/billing/rate-card"),
    getBillingMeter: () => request("/billing/meter"),
    listCreditPacks: () => request("/billing/credit-packs"),
    listBillingAccounts: (query) => request("/billing/accounts", { query }),
    getBillingSubscription: (id) =>
      request(`/billing/accounts/${encodeURIComponent(id)}/subscription`),
    getBillingSeats: (id) =>
      request(`/billing/accounts/${encodeURIComponent(id)}/seats`),
    getBillingSeatQuote: (id, query) =>
      request(`/billing/accounts/${encodeURIComponent(id)}/seats/quote`, {
        query,
      }),
    getBillingUsage: (id, query) =>
      request(`/billing/accounts/${encodeURIComponent(id)}/usage`, { query }),
    getBillingUsageAttribution: (id, query) =>
      request(`/billing/accounts/${encodeURIComponent(id)}/usage/attribution`, {
        query,
      }),
    getBillingUsageRecords: (id, query) =>
      request(`/billing/accounts/${encodeURIComponent(id)}/usage/records`, {
        query,
      }),
    getBillingInvoices: (id, query) =>
      request(`/billing/accounts/${encodeURIComponent(id)}/invoices`, {
        query,
      }),
    getBillingMeterReconciliation: (id, query) =>
      request(`/billing/accounts/${encodeURIComponent(id)}/meter`, { query }),
    startSubscriptionCheckout: (id, input, idempotencyKey) =>
      request(`/billing/accounts/${encodeURIComponent(id)}/checkout`, {
        method: "POST",
        ...(idempotencyKey
          ? { headers: { "Idempotency-Key": idempotencyKey } }
          : {}),
        body: input,
      }),
    cancelSubscription: (id, idempotencyKey) =>
      request(
        `/billing/accounts/${encodeURIComponent(id)}/subscription/cancel`,
        {
          method: "POST",
          ...(idempotencyKey
            ? { headers: { "Idempotency-Key": idempotencyKey } }
            : {}),
        },
      ),
    getBillingInvoice: (id, query) =>
      request(`/billing/accounts/${encodeURIComponent(id)}/invoice`, { query }),
    getBillingCredits: (id, query) =>
      request(`/billing/accounts/${encodeURIComponent(id)}/credits`, { query }),
    getBillingIntegrity: (id) =>
      request(`/billing/accounts/${encodeURIComponent(id)}/integrity`),
    listSandboxes: async () => {
      const result = await request("/os/sandboxes");
      if (!result.available) return result;
      const items = Array.isArray(result.items) ? result.items : [];
      // The list projection does not carry providerKind on current Omega EE
      // versions. Resolve each detail before displaying a sandbox so a local
      // or unknown provider can never be presented as a cloud workspace.
      const verifiedItems = await Promise.all(
        items.map(async (item) => {
          if (!item || typeof item !== "object") return null;
          if (REMOTE_SANDBOX_PROVIDER_KINDS.has(item.providerKind)) return item;
          // An explicit provider declaration is authoritative. Do not make a
          // second request for a known local/unknown provider, which would turn
          // an isolation failure into an unrelated network/body error.
          if (
            typeof item.providerKind === "string" &&
            item.providerKind.length > 0
          )
            return null;
          const id = item.id || item.sandboxId;
          if (typeof id !== "string" || id.length === 0) return null;
          const detail = await request(
            `/os/sandboxes/${encodeURIComponent(id)}`,
          );
          if (!detail.available) return null;
          const sandbox =
            detail.sandbox && typeof detail.sandbox === "object"
              ? detail.sandbox
              : detail;
          if (!REMOTE_SANDBOX_PROVIDER_KINDS.has(sandbox.providerKind))
            return null;
          return { ...item, providerKind: sandbox.providerKind };
        }),
      );
      if (verifiedItems.some((item) => item === null)) {
        return unavailable(
          "ISOLATION_UNVERIFIED",
          "The remote deployment did not prove a cloud sandbox provider.",
        );
      }
      return { ...result, items: verifiedItems };
    },
    getSandbox: (id) => request(`/os/sandboxes/${encodeURIComponent(id)}`),
    listSandboxFiles: (id, query) =>
      request(`/os/sandboxes/${encodeURIComponent(id)}/files`, { query }),
    readSandboxFile: (id, query) =>
      request(`/os/sandboxes/${encodeURIComponent(id)}/file`, { query }),
    writeSandboxFile: (id, input, query) =>
      request(`/os/sandboxes/${encodeURIComponent(id)}/file`, {
        method: "PUT",
        query,
        body: input,
      }),
    controlSandbox: (id, action, input = {}) => {
      if (!SANDBOX_LIFECYCLE_ACTIONS.has(action)) {
        return unavailable(
          "INVALID_ACTION",
          "The requested sandbox lifecycle action is not supported.",
        );
      }
      return request(`/os/sandboxes/${encodeURIComponent(id)}/${action}`, {
        method: "POST",
        body: input,
      });
    },
    listSandboxServices: (id) =>
      request(`/os/sandboxes/${encodeURIComponent(id)}/services`),
    startSandboxService: (id, input) =>
      request(`/os/sandboxes/${encodeURIComponent(id)}/services`, {
        method: "POST",
        body: input,
      }),
    stopSandboxService: (id, serviceId) =>
      request(
        `/os/sandboxes/${encodeURIComponent(id)}/services/${encodeURIComponent(serviceId)}/stop`,
        {
          method: "POST",
        },
      ),
    getSandboxServiceLogs: (id, serviceId, query) =>
      request(
        `/os/sandboxes/${encodeURIComponent(id)}/services/${encodeURIComponent(serviceId)}/logs`,
        { query },
      ),
    mintSandboxPreview: (id, serviceId) =>
      request(
        `/os/sandboxes/${encodeURIComponent(id)}/services/${encodeURIComponent(serviceId)}/preview-token`,
        {
          method: "POST",
        },
      ),
    getMemoryStatus: () => request("/os/memory"),
    getSocietyStatus: () => request("/os/society"),
    /**
     * Tenant-scoped webhook/channel operations. The gateway allowlist only
     * forwards these channel paths; it does not expose the operator connector
     * or global sandbox registries. IDs are encoded as path segments so a
     * caller cannot turn a channel id into an arbitrary upstream path.
     */
    listChannels: async (query) => {
      const result = await request("/os/channels", { query });
      return result.available
        ? {
            ...result,
            channels: Array.isArray(result.channels) ? result.channels : [],
          }
        : result;
    },
    listChannelSubscriptions: async (channelId, query) => {
      if (!channelId)
        return unavailable("INVALID_INPUT", "A channel id is required.");
      const result = await request(
        `/os/channels/${encodeURIComponent(channelId)}/subscriptions`,
        { query },
      );
      return result.available
        ? {
            ...result,
            subscriptions: Array.isArray(result.subscriptions)
              ? result.subscriptions
              : [],
          }
        : result;
    },
    createChannelSubscription: (channelId, input, idempotencyKey) => {
      if (!channelId)
        return Promise.resolve(
          unavailable("INVALID_INPUT", "A channel id is required."),
        );
      return request(
        `/os/channels/${encodeURIComponent(channelId)}/subscriptions`,
        {
          method: "POST",
          ...(idempotencyKey
            ? { headers: { "Idempotency-Key": idempotencyKey } }
            : {}),
          body: input,
        },
      );
    },
    testChannelSubscription: (channelId, subscriptionId, input = {}) => {
      if (!channelId || !subscriptionId)
        return Promise.resolve(
          unavailable(
            "INVALID_INPUT",
            "A channel and subscription id are required.",
          ),
        );
      return request(
        `/os/channels/${encodeURIComponent(channelId)}/subscriptions/${encodeURIComponent(subscriptionId)}/test`,
        {
          method: "POST",
          body: input,
        },
      );
    },
    rotateChannelSubscriptionSecret: (
      channelId,
      subscriptionId,
      input,
      idempotencyKey,
    ) => {
      if (!channelId || !subscriptionId)
        return Promise.resolve(
          unavailable(
            "INVALID_INPUT",
            "A channel and subscription id are required.",
          ),
        );
      return request(
        `/os/channels/${encodeURIComponent(channelId)}/subscriptions/${encodeURIComponent(subscriptionId)}/signing-secret`,
        {
          method: "POST",
          ...(idempotencyKey
            ? { headers: { "Idempotency-Key": idempotencyKey } }
            : {}),
          body: input,
        },
      );
    },
    deleteChannelSubscription: (channelId, subscriptionId, idempotencyKey) => {
      if (!channelId || !subscriptionId)
        return Promise.resolve(
          unavailable(
            "INVALID_INPUT",
            "A channel and subscription id are required.",
          ),
        );
      return request(
        `/os/channels/${encodeURIComponent(channelId)}/subscriptions/${encodeURIComponent(subscriptionId)}`,
        {
          method: "DELETE",
          ...(idempotencyKey
            ? { headers: { "Idempotency-Key": idempotencyKey } }
            : {}),
        },
      );
    },
    listChannelEvents: async (channelId, query) => {
      if (!channelId)
        return unavailable("INVALID_INPUT", "A channel id is required.");
      const result = await request(
        `/os/channels/${encodeURIComponent(channelId)}/events`,
        { query },
      );
      return result.available
        ? { ...result, items: Array.isArray(result.items) ? result.items : [] }
        : result;
    },
    listChannelEventDeliveries: async (channelId, eventKey, query) => {
      if (!channelId || !eventKey)
        return unavailable(
          "INVALID_INPUT",
          "A channel and event key are required.",
        );
      const result = await request(
        `/os/channels/${encodeURIComponent(channelId)}/events/${encodeURIComponent(eventKey)}/deliveries`,
        { query },
      );
      return result.available
        ? {
            ...result,
            deliveries: Array.isArray(result.deliveries)
              ? result.deliveries
              : [],
          }
        : result;
    },
    getChannelDeliverySlo: (channelId, query) => {
      if (!channelId)
        return Promise.resolve(
          unavailable("INVALID_INPUT", "A channel id is required."),
        );
      return request(
        `/os/channels/${encodeURIComponent(channelId)}/delivery-slo`,
        { query },
      );
    },
    redriveChannelDelivery: (
      channelId,
      eventKey,
      deliveryId,
      idempotencyKey,
    ) => {
      if (!channelId || !eventKey || !deliveryId)
        return Promise.resolve(
          unavailable(
            "INVALID_INPUT",
            "A channel, event, and delivery id are required.",
          ),
        );
      return request(
        `/os/channels/${encodeURIComponent(channelId)}/events/${encodeURIComponent(eventKey)}/deliveries/${encodeURIComponent(deliveryId)}/redrive`,
        {
          method: "POST",
          ...(idempotencyKey
            ? { headers: { "Idempotency-Key": idempotencyKey } }
            : {}),
          body: {},
        },
      );
    },
    /**
     * Read the tenant-scoped workforce projection used by the customer-facing
     * Fleet view. This deliberately uses /workspace/workforce rather than the
     * operator-only /os/processes endpoint: Omega applies the authenticated
     * organization/workspace boundary on the remote deployment.
     */
    getFleetHealth: async () => {
      const result = await request("/workspace/workforce");
      if (!result.available) return result;
      return parseFleetHealth(result);
    },
  };
}

/**
 * Normalize the real Omega Workforce envelope into the small shape the Squad
 * dashboard needs. Unknown status values or records are rejected as malformed
 * so the UI cannot display an invented health state.
 *
 * @param {Object} value
 * @returns {Object | OmegaUnavailable}
 */
export function parseFleetHealth(value) {
  if (
    !value ||
    typeof value !== "object" ||
    value.schemaVersion !== WORKFORCE_SCHEMA_VERSION ||
    !Array.isArray(value.agents)
  ) {
    return unavailable(
      "MALFORMED",
      "The remote workforce returned an unsupported fleet envelope.",
    );
  }

  const agents = [];
  for (const candidate of value.agents) {
    if (!candidate || typeof candidate !== "object") {
      return unavailable(
        "MALFORMED",
        "The remote workforce returned an invalid agent record.",
      );
    }
    const name =
      typeof candidate.name === "string" && candidate.name.trim().length > 0
        ? candidate.name
        : typeof candidate.id === "string" && candidate.id.trim().length > 0
          ? candidate.id
          : "";
    const status =
      typeof candidate.status === "string" &&
      WORKFORCE_STATUS_VALUES.includes(candidate.status)
        ? candidate.status
        : "";
    if (!name || !status) {
      return unavailable(
        "MALFORMED",
        "The remote workforce returned an agent without a valid name or status.",
      );
    }
    agents.push({
      id: typeof candidate.id === "string" ? candidate.id : name,
      name,
      role: typeof candidate.role === "string" ? candidate.role : "",
      purpose: typeof candidate.purpose === "string" ? candidate.purpose : "",
      status,
      ...(typeof candidate.reason === "string"
        ? { reason: candidate.reason }
        : {}),
      currentWork:
        typeof candidate.currentWork === "string"
          ? candidate.currentWork
          : null,
      ...(typeof candidate.updatedAt === "string"
        ? { updatedAt: candidate.updatedAt }
        : {}),
      ...(typeof candidate.workMode === "string"
        ? { workMode: candidate.workMode }
        : {}),
    });
  }

  const byStatus = Object.fromEntries(
    WORKFORCE_STATUS_VALUES.map((status) => [status, 0]),
  );
  agents.forEach((agent) => {
    byStatus[agent.status] += 1;
  });
  return {
    available: true,
    schemaVersion: WORKFORCE_SCHEMA_VERSION,
    agents,
    capabilities:
      value.capabilities && typeof value.capabilities === "object"
        ? value.capabilities
        : null,
    health: {
      total: agents.length,
      healthy: byStatus.idle + byStatus.working,
      active: byStatus.working,
      ready: byStatus.idle,
      paused: byStatus.paused,
      attention: byStatus.needs_attention,
      offline: byStatus.offline,
      byStatus,
    },
  };
}
