import { HOST_API } from "src/config-global";

import { HUB_API } from "../api/endpoints";

// Keep command routing separate from the hosted empty-state classification.
// This default matches packages/cli/src/hub/registry/config.ts in Omega.
const DEFAULT_REGISTRY = `https://api.futureagi.com${HUB_API}`;
// Hosted API origins supported by src/config-global.js (including regions).
const HOSTED_ORIGINS = new Set([
  "https://api.futureagi.com",
  "https://api-us.futureagi.com",
  "https://api-eu.futureagi.com",
  "https://dev.api.futureagi.com",
]);

// HOST_API already resolves runtime config before build-time config. Resolve
// relative API bases against the app, never against the CLI's hosted default.
export function hubApiUrl(
  path,
  apiBase = HOST_API,
  origin = globalThis.location?.origin,
) {
  const base = new URL(apiBase || "/", origin).href.replace(/\/+$/, "");
  return `${base}${path}`;
}

export function hubRegistryUrl(
  apiBase = HOST_API,
  origin = globalThis.location?.origin,
) {
  return hubApiUrl(HUB_API, apiBase, origin);
}

export function isHostedHub(apiBase = HOST_API) {
  return HOSTED_ORIGINS.has(new URL(hubRegistryUrl(apiBase)).origin);
}

// POSIX shell quoting: normal references stay readable, but spaces, quotes,
// substitutions and shell operators can never turn one value into commands.
export function shellQuote(value) {
  const text = String(value);
  return /^[a-zA-Z0-9_@%+=:,./-]+$/.test(text)
    ? text
    : `'${text.replace(/'/g, `'"'"'`)}'`;
}

export function hubCommand(args, apiBase = HOST_API) {
  const registry = hubRegistryUrl(apiBase);
  const command = ["omega", "hub", ...args];
  if (registry !== DEFAULT_REGISTRY) command.push("--registry", registry);
  return command.map(shellQuote).join(" ");
}
