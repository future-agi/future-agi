import process from "node:process";
import { apiPath } from "../../../src/api/contracts/api-surface.js";
import { readCachedTokens, writeCachedTokens } from "./token-cache.mjs";

export { apiPath };

export class ApiJourneyError extends Error {
  constructor(message, { method, pathName, status, body } = {}) {
    super(message);
    this.name = "ApiJourneyError";
    this.method = method;
    this.pathName = pathName;
    this.status = status;
    this.body = body;
  }
}

export class SkipJourney extends Error {
  constructor(reason) {
    super(reason);
    this.name = "SkipJourney";
    this.reason = reason;
  }
}

export function skip(reason) {
  throw new SkipJourney(reason);
}

export function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

export function unwrapApiData(data) {
  if (data && Object.prototype.hasOwnProperty.call(data, "result")) {
    return data.result;
  }
  if (data && Object.prototype.hasOwnProperty.call(data, "results")) {
    return data.results;
  }
  return data;
}

export function asArray(value) {
  if (Array.isArray(value)) return value;
  if (Array.isArray(value?.results)) return value.results;
  if (Array.isArray(value?.items)) return value.items;
  if (Array.isArray(value?.datasets)) return value.datasets;
  if (Array.isArray(value?.table)) return value.table;
  if (Array.isArray(value?.data)) return value.data;
  return [];
}

export function withQuery(pathName, query = {}) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) {
      for (const item of value) {
        params.append(key, stringifyQueryValue(item));
      }
      continue;
    }
    params.set(key, stringifyQueryValue(value));
  }
  const queryString = params.toString();
  if (!queryString) return pathName;
  return `${pathName}${pathName.includes("?") ? "&" : "?"}${queryString}`;
}

export function isUuid(value) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
    String(value || ""),
  );
}

export function envFlag(name) {
  return ["1", "true", "yes", "on"].includes(
    String(process.env[name] || "").toLowerCase(),
  );
}

export function requireMutations() {
  if (!envFlag("API_JOURNEY_MUTATIONS")) {
    skip("Set API_JOURNEY_MUTATIONS=1 to run data-mutating journeys.");
  }
}

export function currentUserId(user) {
  return (
    user?.id ||
    user?.user_id ||
    user?.pk ||
    user?.user?.id ||
    user?.result?.id ||
    null
  );
}

export function currentUserEmail(user) {
  return user?.email || user?.user?.email || user?.result?.email || "";
}

export class CleanupStack {
  constructor() {
    this.items = [];
  }

  defer(label, fn) {
    this.items.push({ label, fn });
  }

  async run(evidence = []) {
    const failures = [];
    for (const item of this.items.reverse()) {
      try {
        await item.fn();
        evidence.push({ cleanup: item.label, status: "passed" });
      } catch (error) {
        failures.push({ label: item.label, error: error.message });
        evidence.push({
          cleanup: item.label,
          status: "failed",
          error: error.message,
        });
      }
    }
    return failures;
  }
}

export function createApiClient({
  apiBase = process.env.API_BASE || "http://localhost:8003",
  accessToken,
  organizationId,
  workspaceId,
} = {}) {
  const base = normalizeBaseUrl(apiBase);

  async function request(method, pathName, options = {}) {
    const url = new URL(pathName, base);
    if (options.query) {
      for (const [key, value] of Object.entries(options.query)) {
        if (value === undefined || value === null || value === "") continue;
        if (Array.isArray(value)) {
          for (const item of value) {
            url.searchParams.append(key, stringifyQueryValue(item));
          }
          continue;
        }
        url.searchParams.set(key, stringifyQueryValue(value));
      }
    }

    const headers = {
      ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
      ...(organizationId ? { "X-Organization-Id": organizationId } : {}),
      ...(workspaceId ? { "X-Workspace-Id": workspaceId } : {}),
      ...(options.headers || {}),
    };

    const init = { method, headers };
    if (options.body !== undefined) {
      init.body = JSON.stringify(options.body);
      init.headers = { "Content-Type": "application/json", ...headers };
    }

    const response = await fetch(url, init);
    const body = await parseResponseBody(response);
    const okStatuses = options.okStatuses;
    const ok = Array.isArray(okStatuses)
      ? okStatuses.includes(response.status)
      : response.status >= 200 && response.status < 300;
    if (!ok) {
      throw new ApiJourneyError(
        `${method} ${pathName} failed with HTTP ${response.status}: ${formatBody(
          body,
        )}`,
        { method, pathName, status: response.status, body },
      );
    }
    if (body && typeof body === "object" && body.status === false) {
      throw new ApiJourneyError(
        `${method} ${pathName} returned status:false: ${formatBody(body)}`,
        { method, pathName, status: response.status, body },
      );
    }
    return options.unwrap === false ? body : unwrapApiData(body);
  }

  return {
    apiBase: base,
    get: (pathName, options) => request("GET", pathName, options),
    post: (pathName, body = {}, options = {}) =>
      request("POST", pathName, { ...options, body }),
    put: (pathName, body = {}, options = {}) =>
      request("PUT", pathName, { ...options, body }),
    patch: (pathName, body = {}, options = {}) =>
      request("PATCH", pathName, { ...options, body }),
    delete: (pathName, options) => request("DELETE", pathName, options),
    request,
  };
}

export async function createAuthenticatedContext() {
  const apiBase = process.env.API_BASE || "http://localhost:8003";
  const tokens = await loadTokensForApiJourney(apiBase);
  let userClient = createApiClient({
    apiBase,
    accessToken: tokens.access,
    organizationId: process.env.FUTURE_AGI_ORGANIZATION_ID,
    workspaceId: process.env.FUTURE_AGI_WORKSPACE_ID,
  });
  const user = await userClient.get(apiPath("/accounts/user-info/"));
  const contextIds = await resolveContextIds(userClient, user);
  userClient = createApiClient({
    apiBase,
    accessToken: tokens.access,
    organizationId: contextIds.organizationId,
    workspaceId: contextIds.workspaceId,
  });
  if (tokens.source !== "env") {
    await writeCachedTokens(tokens, {
      apiBase,
      organizationId: contextIds.organizationId,
      workspaceId: contextIds.workspaceId,
    }).catch(() => null);
  }

  return {
    client: userClient,
    user,
    tokens,
    apiBase: normalizeBaseUrl(apiBase),
    organizationId: contextIds.organizationId,
    workspaceId: contextIds.workspaceId,
    runId: `${Date.now().toString(36)}-${Math.random()
      .toString(36)
      .slice(2, 8)}`,
  };
}

export async function loadTokensForApiJourney(
  apiBase,
  { env = process.env, fetchImpl = fetch } = {},
) {
  if (env.FUTURE_AGI_ACCESS_TOKEN) {
    return {
      access: env.FUTURE_AGI_ACCESS_TOKEN,
      refresh: env.FUTURE_AGI_REFRESH_TOKEN || "",
      source: "env",
    };
  }

  const cachedTokens = await readCachedTokens({ apiBase, env });
  if (
    cachedTokens?.access &&
    (await accessTokenAccepted(apiBase, cachedTokens.access, fetchImpl))
  ) {
    return cachedTokens;
  }

  const email = env.FUTURE_AGI_EMAIL;
  const password = env.FUTURE_AGI_PASSWORD;
  if (!email || !password) {
    throw new Error(
      "Set FUTURE_AGI_ACCESS_TOKEN, FUTURE_AGI_TOKEN_FILE, or FUTURE_AGI_EMAIL and FUTURE_AGI_PASSWORD.",
    );
  }

  let response = await fetchImpl(
    `${normalizeBaseUrl(apiBase)}/accounts/token/`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email,
        password,
        remember_me: true,
        "recaptcha-response": "api-journey-local-test",
      }),
    },
  );
  let body = await parseResponseBody(response);
  if (
    response.status === 400 &&
    String(body?.message || body?.detail || "").includes("recaptcha-response")
  ) {
    response = await fetchImpl(`${normalizeBaseUrl(apiBase)}/accounts/token/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password, remember_me: true }),
    });
    body = await parseResponseBody(response);
  }
  if (!response.ok || !body?.access) {
    throw new ApiJourneyError(
      `Login failed with HTTP ${response.status}: ${formatBody(body)}`,
      {
        method: "POST",
        pathName: "/accounts/token/",
        status: response.status,
        body,
      },
    );
  }
  const tokens = { ...body, source: "login" };
  await writeCachedTokens(tokens, { apiBase, env }).catch(() => null);
  return tokens;
}

async function accessTokenAccepted(apiBase, accessToken, fetchImpl) {
  try {
    const response = await fetchImpl(
      `${normalizeBaseUrl(apiBase)}/accounts/user-info/`,
      {
        method: "GET",
        headers: { Authorization: `Bearer ${accessToken}` },
      },
    );
    const body = await parseResponseBody(response);
    return response.ok && body?.status !== false;
  } catch {
    return false;
  }
}

async function resolveContextIds(client, user) {
  let organizationId =
    process.env.FUTURE_AGI_ORGANIZATION_ID ||
    user?.organization_id ||
    user?.organization?.id ||
    user?.selected_organization?.id ||
    user?.config?.selected_organization_id ||
    user?.user?.organization_id ||
    "";

  let workspaceId =
    process.env.FUTURE_AGI_WORKSPACE_ID ||
    user?.workspace_id ||
    user?.workspace?.id ||
    user?.default_workspace_id ||
    user?.default_workspace?.id ||
    user?.config?.currentWorkspaceId ||
    user?.config?.current_workspace_id ||
    "";

  if (!workspaceId) {
    try {
      const workspaces = await client.get(apiPath("/accounts/workspace/list/"));
      const rows = asArray(workspaces);
      const first = rows.find((row) => row?.id || row?.workspace_id);
      workspaceId = first?.id || first?.workspace_id || "";
      organizationId =
        organizationId ||
        first?.organization_id ||
        first?.organization?.id ||
        first?.org_id ||
        "";
    } catch {
      // The auth middleware can fall back to the user's selected workspace.
    }
  }

  return { organizationId, workspaceId };
}

function normalizeBaseUrl(value) {
  return String(value || "").replace(/\/+$/, "");
}

function stringifyQueryValue(value) {
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

async function parseResponseBody(response) {
  const text = await response.text();
  if (!text) return null;
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    try {
      return JSON.parse(text);
    } catch {
      return text;
    }
  }
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function formatBody(body) {
  if (typeof body === "string") return body.slice(0, 1000);
  return JSON.stringify(body).slice(0, 1000);
}
