import { paths } from "src/routes/paths";

// The console model page: /dashboard/gateway/models/<provider>/<model>, with
// ?tab=try or ?tab=api for those tabs. The segments are the gateway model id
// split at its first "/", each URL-encoded. futureagi.com/models links here
// from every model page, so this shape is a contract (modelLinks.test.js).

export const MODEL_TABS = ["overview", "try", "api"];

export function splitModelId(id) {
  const text = typeof id === "string" ? id : "";
  const at = text.indexOf("/");
  if (at <= 0 || at === text.length - 1) return null;
  return { provider: text.slice(0, at), model: text.slice(at + 1) };
}

// A model id with no provider part ("gpt-4o") has no page of its own.
export function modelPath(id, tab) {
  const parts = splitModelId(id);
  if (!parts) return null;
  const base = `${paths.dashboard.gateway.models}/${encodeURIComponent(
    parts.provider,
  )}/${encodeURIComponent(parts.model)}`;
  return tab && tab !== "overview" && MODEL_TABS.includes(tab)
    ? `${base}?tab=${tab}`
    : base;
}

// React Router hands the params over decoded.
export function modelIdFromParams(params) {
  const provider = params?.provider;
  const model = params?.model;
  return provider && model ? `${provider}/${model}` : null;
}

export function modelTabFrom(search) {
  const tab = new URLSearchParams(search || "").get("tab");
  return MODEL_TABS.includes(tab) ? tab : "overview";
}
