import { v4 as nonce, v5 as comparisonTag } from "uuid";
import { hasMixpanelMember, identifyUser } from "src/utils/Mixpanel";
import { hasPostHogMember, identifyPostHogUser } from "src/utils/PostHog";
import {
  SS_KEY_ORG_ID,
  SS_KEY_USER_ID,
  SS_KEY_WORKSPACE_ID,
} from "src/utils/sessionKeys";

const KEY = "consentIdentityHandoff.v1";
const MAX_AGE_MS = 30_000;

// Snapshot the fields sent by the two identify helpers, not the /me response.
// Changes to roles or names should still reach the SDKs on the next document.
function identityFields(user) {
  return [
    user?.id,
    user?.organization?.id,
    user?.default_workspace_id,
    user?.email,
    user?.name,
    user?.organization?.name,
    user?.organization?.display_name,
    user?.default_workspace_role,
    user?.organization_role,
  ];
}

export function clearConsentIdentityHandoff() {
  try {
    sessionStorage.removeItem(KEY);
  } catch {
    /* Analytics storage is optional. */
  }
}

export function takeConsentIdentityHandoff() {
  try {
    const value = sessionStorage.getItem(KEY);
    sessionStorage.removeItem(KEY); // Also discard on ordinary document entry.
    if (
      window.__analyticsConsentHandoffAtLoad === true &&
      value?.length <= 256
    ) {
      return JSON.parse(value);
    }
  } catch {
    /* Unavailable, malformed, or not removable: identify normally. */
  }
  return null;
}

// Check the tab's existing context and the actual SDK identities. Another tab
// may have changed shared SDK state, or an SDK may not have persisted it.
function confirmed(providers, [id, org, workspace]) {
  const storedWorkspace = sessionStorage.getItem(SS_KEY_WORKSPACE_ID);
  if (
    !id ||
    !org ||
    workspace === undefined ||
    sessionStorage.getItem(SS_KEY_USER_ID) !== id ||
    sessionStorage.getItem(SS_KEY_ORG_ID) !== org ||
    (storedWorkspace !== null && storedWorkspace !== workspace)
  )
    return null;

  return {
    mixpanel: providers.mixpanel === true && hasMixpanelMember(id, org),
    posthog: providers.posthog === true && hasPostHogMember(id, org, workspace),
  };
}

export function prepareConsentIdentityHandoff(member) {
  clearConsentIdentityHandoff();
  if (!member) return undefined;
  try {
    const providers = confirmed(member, member.fields);
    if (providers?.mixpanel || providers?.posthog) {
      // A fresh namespace makes the equality tag specific to this handoff. No
      // stable user ID, profile, token, or route is copied into browser storage.
      const salt = nonce();
      const tag = comparisonTag(JSON.stringify(member.fields), salt);
      const value = JSON.stringify({ at: Date.now(), salt, tag, ...providers });
      sessionStorage.setItem(KEY, value);
      return () => {
        try {
          if (sessionStorage.getItem(KEY) === value)
            sessionStorage.removeItem(KEY);
        } catch {
          /* Navigation was cancelled; storage may be unavailable. */
        }
      };
    }
  } catch {
    /* Missing SDK or storage evidence: navigate without a receipt. */
  }
  return undefined;
}

// Must run before AuthProvider writes the new /me identity to sessionStorage.
export function matchingConsentIdentity(receipt, user) {
  if (!receipt) return null;
  try {
    const age = Date.now() - receipt.at;
    if (!Number.isSafeInteger(receipt.at) || age < 0 || age > MAX_AGE_MS)
      return null;
    const fields = identityFields(user);
    if (comparisonTag(JSON.stringify(fields), receipt.salt) === receipt.tag) {
      return confirmed(receipt, fields);
    }
  } catch {
    /* Never let malformed storage or a readback failure affect auth. */
  }
  return null;
}

export function identifyMember(user, reuse) {
  const mixpanel = reuse?.mixpanel || identifyUser(user) === true;
  const posthog = reuse?.posthog || identifyPostHogUser(user) === true;
  return mixpanel || posthog
    ? { fields: identityFields(user), mixpanel, posthog }
    : null;
}
