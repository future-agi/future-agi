// Whether this user may open Omega Console in the current workspace (CP P10).
//
// The session exchange decides (futureagi/accounts/omega_session.py
// delegation_role): organization owner (15) and admin (8) may; an
// organization member (3) may with a workspace member (3) or admin (8) grant;
// everyone else, viewers included, is refused with 403
// omega_viewer_not_admitted. The host mirrors that rule from the levels it
// already holds, so a viewer sees "Ask an admin for access" on the product
// switcher and no frame on the Console page, without a sign-in attempt.
//
// The exchange stays the authority: a 403 viewer refusal is remembered for
// the workspace (in memory only) and overrides the levels, and a levels-based
// "viewer" is only a prediction the page can check again. Unknown levels
// (not loaded yet) are never read as a refusal.
import { useSyncExternalStore } from "react";

import { useOrganization } from "src/contexts/OrganizationContext";
import { useWorkspace } from "src/contexts/WorkspaceContext";

export const CONSOLE_ACCESS = Object.freeze({
  UNKNOWN: "unknown",
  ALLOWED: "allowed",
  VIEWER: "viewer",
});

/** What the product switcher says under Omega Console for a viewer. */
export const VIEWER_SWITCHER_DETAIL = "Ask an admin for access";

const LEVEL = Object.freeze({ OWNER: 15, ADMIN: 8, MEMBER: 3 });
const WORKSPACE_GRANTS = new Set([LEVEL.MEMBER, LEVEL.ADMIN]);

const toLevel = (value) => {
  if (value === null || value === undefined || value === "") return null;
  const level = typeof value === "number" ? value : Number(value);
  return Number.isInteger(level) ? level : null;
};

/** The exchange's rule on the host's levels; UNKNOWN when it cannot tell. */
export function consoleAccessFor(orgLevel, wsLevel) {
  const org = toLevel(orgLevel);
  if (org === null) return CONSOLE_ACCESS.UNKNOWN;
  if (org === LEVEL.OWNER || org === LEVEL.ADMIN) return CONSOLE_ACCESS.ALLOWED;
  if (org === LEVEL.MEMBER) {
    const ws = toLevel(wsLevel);
    if (ws === null) return CONSOLE_ACCESS.UNKNOWN;
    return WORKSPACE_GRANTS.has(ws)
      ? CONSOLE_ACCESS.ALLOWED
      : CONSOLE_ACCESS.VIEWER;
  }
  return CONSOLE_ACCESS.VIEWER;
}

// Workspaces whose exchange refused this user as a viewer, this tab only.
const refused = new Set();
const listeners = new Set();
let version = 0;
const notify = () => {
  version += 1;
  listeners.forEach((listener) => listener());
};
const keyOf = (workspaceId) => String(workspaceId ?? "");

export function rememberViewerRefusal(workspaceId) {
  const key = keyOf(workspaceId);
  if (refused.has(key)) return;
  refused.add(key);
  notify();
}

export function forgetViewerRefusal(workspaceId) {
  if (refused.delete(keyOf(workspaceId))) notify();
}

export function resetConsoleAccessForTests() {
  refused.clear();
  notify();
}

const subscribe = (listener) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};
const snapshot = () => version;

/**
 * @returns {{ access: string, refusedByExchange: boolean, workspaceId: string | null }}
 */
export function useConsoleAccess() {
  const { orgLevel } = useOrganization();
  const { wsLevel, currentWorkspaceId } = useWorkspace();
  useSyncExternalStore(subscribe, snapshot, snapshot);
  const refusedByExchange = refused.has(keyOf(currentWorkspaceId));
  return {
    access: refusedByExchange
      ? CONSOLE_ACCESS.VIEWER
      : consoleAccessFor(orgLevel, wsLevel),
    refusedByExchange,
    workspaceId: currentWorkspaceId ?? null,
  };
}
