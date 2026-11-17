// The calls a code panel never offers, because the stack refuses them to a
// personal API key or a program should never make them (parity P22 rule: no
// snippet for an action the stack refuses).
//
// - KEY_REFUSED_ROUTES: the gateway's human-session-only verbs
//   (services/omega-gateway/src/session-only.mjs, SB-W2-KEYS CLAIMS 2a). A key
//   gets 403 omega_human_session_required. A contract test keeps this list
//   equal to the gateway's own.
// - HUMAN_ONLY_ROUTES: writes the platform SDK never wraps (sdk-split PLAN
//   section 2.4: approvals.decide, credentials create, revoke and rotate,
//   sandboxes.settings.update). A secret's value belongs in the vault form, not
//   in a shell history.

export const KEY_REFUSED_ROUTES = Object.freeze([
  "POST /workspace/approvals/{id}",
  "PUT /workspace/sandboxes/settings",
  // A conversation computer's channel token is minted by a signed-in person only.
  "POST /workspace/conversations/{id}/computer/channel",
  // P07 CHAT-TRACE: the Observe content switch is a person's decision.
  "PUT /workspace/observe/settings",
  // Tool policy and telemetry settings also require a signed-in person.
  "GET /workspace/tool-policy/workspace",
  "PUT /workspace/tool-policy/workspace",
  "GET /workspace/tool-policy/organization",
  "PUT /workspace/tool-policy/organization",
  "PUT /workspace/otlp",
  "POST /workspace/otlp/test",
  // Removing an agent's lesson and pausing its learning are a person's decision.
  "POST /workspace/learning/agents/{id}/lessons/{id}/revert",
  "POST /workspace/learning/agents/{id}/pause",
  // Creating, deleting and rotating a workspace credential take a signed-in
  // person too (also below: the SDK never wraps them).
  "POST /workspace/credentials",
  "DELETE /workspace/credentials/{id}",
  "POST /workspace/credentials/{id}/rotate",
]);

export const HUMAN_ONLY_ROUTES = Object.freeze([
  "POST /workspace/credentials",
  "DELETE /workspace/credentials/{id}",
  "POST /workspace/credentials/{id}/rotate",
]);

const REFUSED = new Set([...KEY_REFUSED_ROUTES, ...HUMAN_ONLY_ROUTES]);

/** True when a panel may offer this manifest route ("METHOD /path/{param}"). */
export function isOffered(route) {
  return !REFUSED.has(route);
}

/** The requests a panel may show: refused routes dropped, order kept. */
export function offered(requests) {
  return requests.filter((request) => request && isOffered(request.route));
}
