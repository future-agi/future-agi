// Where a snippet sends its request: the stack the page runs on. The tabs
// reach EE through the gateway mounted on this same origin (omegaClient.js,
// VITE_OMEGA_API_BASE, default /omega/api), and a program with a personal API
// key uses the very same URL (SB-W2-KEYS walks call <origin>/omega/api). The
// organization and workspace the page shows are sent as the selection headers,
// so the snippet reaches this workspace even with a key pinned to none.

import { resolveOmegaApiBase } from "src/sections/omega/shared/omegaClient";
import { SS_KEY_ORG_ID, SS_KEY_WORKSPACE_ID } from "src/utils/sessionKeys";

function stored(name) {
  try {
    return globalThis.sessionStorage?.getItem(name) || null;
  } catch {
    return null;
  }
}

/** `https://app.example.com/omega/api`: this stack's gateway, as a program calls it. */
export function gatewayBaseUrl(
  origin = globalThis.location?.origin,
  base = import.meta.env?.VITE_OMEGA_API_BASE,
) {
  return `${origin}${resolveOmegaApiBase(base)}`;
}

/** The organization and workspace this page shows (the host's own selection). */
export function currentScope() {
  return {
    organizationId: stored(SS_KEY_ORG_ID),
    workspaceId: stored(SS_KEY_WORKSPACE_ID),
  };
}
