import axios from "src/utils/axios";
import { apiPath } from "src/api/contracts/api-surface";

export const consentRequestPath = (requestId) =>
  apiPath("/agentcc/mcp-oauth/consent/{request_id}/", {
    request_id: requestId,
  });
export const consentListPath = () => apiPath("/agentcc/mcp-oauth/consents/");
export const consentDetailPath = (id) =>
  apiPath("/agentcc/mcp-oauth/consents/{consent_id}/", { consent_id: id });

function result(data) {
  if (!data?.status || data.result == null) {
    throw new Error("The server did not confirm this request. Try again.");
  }
  return data.result;
}

export async function loadConsent(requestId, signal) {
  return result(
    (await axios.get(consentRequestPath(requestId), { signal })).data,
  );
}

export async function decideConsent(requestId, decision, organizationId) {
  const body = { decision };
  if (decision === "approve") body.organization_id = organizationId;
  return result((await axios.post(consentRequestPath(requestId), body)).data);
}

export async function listConsents(scope, organizationId, signal) {
  return result(
    (
      await axios.get(consentListPath(), {
        params: { scope },
        signal,
        headers: { "X-Organization-Id": organizationId },
      })
    ).data,
  );
}

export async function revokeConsent(id, organizationId) {
  return result(
    (
      await axios.delete(consentDetailPath(id), {
        headers: { "X-Organization-Id": organizationId },
      })
    ).data,
  );
}

export function consentError(error, fallback) {
  const data = error?.response?.data;
  for (const value of [
    data?.result,
    data?.message,
    data?.detail,
    data?.error,
  ]) {
    if (typeof value === "string") return value;
  }
  if (error?.response?.status === 404) {
    return "This sign-in request has expired or was already used. Start again from your MCP client.";
  }
  return fallback;
}

// The target comes only from the decision API, never from browser query params.
// Match it to the registered callback shown on the consent screen. Custom app
// schemes (e.g. a desktop MCP client) must continue working.
export function returnToClient(target, registeredRedirect) {
  const actual = new URL(target);
  const expected = new URL(registeredRedirect);
  const forbidden = [
    "javascript",
    "data",
    "vbscript",
    "file",
    "blob",
    "about",
    "ws",
    "wss",
    "ftp",
  ];
  if (
    forbidden.includes(actual.protocol.slice(0, -1)) ||
    ["protocol", "host", "pathname", "username", "password", "hash"].some(
      (field) => actual[field] !== expected[field],
    ) ||
    [...expected.searchParams].some(
      ([key, value]) => !actual.searchParams.getAll(key).includes(value),
    )
  ) {
    throw new Error(
      "The client return address did not match the sign-in request.",
    );
  }
  window.location.assign(target);
}
