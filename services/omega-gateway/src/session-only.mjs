// The verbs only a person in an interactive Future AGI session may use (sandbox-better PLAN L3
// and W2-ACCESS, critique C2-3 "API key can self-approve"): deciding an approval, share links,
// sharing grants, settings, an agent's learning controls and a workspace credential's writes.
// test/session-only.test.mjs holds this list to every operation EE's customer contract marks
// `x-omega-human-only` that the manifest routes. An assertion that names any other
// authentication method (SB W2-KEYS: `api_key`, a personal API key from an SDK or CI) gets 403
// here and never reaches EE,
// so an agent holding a key cannot approve its own egress request or rewrite its sandbox
// settings.
//
// Reading stays open to a key: an agent polls its own approval (`GET /workspace/approvals`) and
// reads the settings it runs under. The decision is on the verified claims, not on which
// credential the caller presented, so a pre-minted assertion that says `api_key` is refused on
// the Console origin too.
//
// The manifest is byte-identical with EE's copy and its strict compiler refuses unknown fields,
// so this table is the gateway's list. It is checked once at load against the manifest the
// gateway enforces: an entry whose route the manifest does not admit stops the gateway, so a
// typo can never silently reopen a verb. test/session-only.test.mjs also fails when the
// manifest gains a write route that looks like one of these verbs (W2-ACCESS's sharing and
// share-link routes) and this table does not list it. EE refusing the same verbs itself
// (W2-ACCESS) is still owed; this is the edge's half (CLAIMS.md section 6).
//
// The 60 s channel token of a conversation's computer
// (its terminal) is minted by a signed-in person only. The Console mints it through this gateway
// with the person's session assertion; a Future AGI API key gets 403 here. EE refuses a plain
// workspace API key itself and records every mint with its principal in its control ledger.
import { ROUTE_MANIFEST } from "./routes.mjs";

export const SESSION_ONLY_ROUTES = Object.freeze([
  "POST /workspace/approvals/{id}",
  "PUT /workspace/sandboxes/settings",
  "POST /workspace/conversations/{id}/computer/channel",
  // P07 CHAT-TRACE: a workspace's Observe content switch. EE's policy row
  // `observeSettings.update` is humanOnly (x-omega-human-only), so an agent's key cannot turn capture back on.
  "PUT /workspace/observe/settings",
  "GET /workspace/tool-policy/workspace",
  "PUT /workspace/tool-policy/workspace",
  "GET /workspace/tool-policy/organization",
  "PUT /workspace/tool-policy/organization",
  "PUT /workspace/otlp",
  "POST /workspace/otlp/test",
  // An agent's learning controls: removing one of its lessons and pausing or resuming its
  // learning. EE's policy rows `agents.revertLesson` and `agents.pauseLearning` are humanOnly,
  // so an agent's key cannot turn its own learning off or on; the key still reads the lessons.
  "POST /workspace/learning/agents/{id}/lessons/{id}/revert",
  "POST /workspace/learning/agents/{id}/pause",
  // A workspace credential's create, delete and rotate: EE's contract marks them for a signed-in
  // person (`x-omega-human-only`), so a secret's value is set, replaced or removed by a person;
  // a key still lists and reads credentials.
  "POST /workspace/credentials",
  "DELETE /workspace/credentials/{id}",
  "POST /workspace/credentials/{id}/rotate",
]);

/** The one method that proves a person is present (CLAIMS.md section 1). */
const HUMAN_SESSION = "session";

/** Throws when an entry names no route the manifest admits. */
export function checkSessionOnlyRoutes(
  manifest = ROUTE_MANIFEST,
  routes = SESSION_ONLY_ROUTES,
) {
  for (const id of routes) {
    if (
      !manifest.routes.some((entry) => `${entry.method} ${entry.path}` === id)
    ) {
      throw new Error(`Session-only route ${id} names no manifest route.`);
    }
  }
}

checkSessionOnlyRoutes();

const LISTED = new Set(SESSION_ONLY_ROUTES);

/** null when the caller may use the matched route; else the refusal (fails closed on any other method). */
export function decideSessionOnly(route, claims, routes = LISTED) {
  if (!routes.has(`${route.method} ${route.path}`)) return null;
  if (claims?.auth_method === HUMAN_SESSION) return null;
  return {
    status: 403,
    error: "omega_human_session_required",
    reason: "session_only_verb",
  };
}
