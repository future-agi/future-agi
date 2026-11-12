// What the AI Gateway console can save but this gateway does not apply.
//
// An option that is stored but not enforced is never shown as working. Each
// entry is a setting this gateway keeps and never reads; a page shows it
// read-only with the neutral "Not enforced on this gateway" chip and the
// reason. When the gateway starts applying a setting, its entry is deleted
// and the note disappears everywhere: EnforcementNote renders nothing for an
// id that has no entry.
//
// Whether the gateway runs an organization's budgets, cache, IP ACL, tool
// policy, alerting or rate limiting is not here: the gateway reports it in
// org_sections_enforced, and sectionEnforcement.js reads that.

export const NOT_ENFORCED_LABEL = "Not enforced on this gateway";

export const ENFORCEMENT = {
  // Guardrails: the gateway runs an organization's checks with its own node
  // settings (Org config > Guardrails shows them read-only).
  "guardrails.failOpen": {
    kind: "not-enforced",
    reason:
      "The gateway takes fail-open from its own node settings for every organization, so what happens when a check errors or times out does not follow a setting saved here.",
  },
  "guardrails.pipelineMode": {
    kind: "not-enforced",
    reason:
      "Checks run one after another whatever pipeline mode is saved here.",
  },

  // Routing settings the gateway keeps but does not run.
  "routing.defaultModel": {
    kind: "not-enforced",
    reason:
      "The default model is stored and not applied: a request is never sent to it in place of the model it names.",
  },
  "routing.conditionalRoutes": {
    kind: "not-enforced",
    reason: "Conditional routes are stored and not applied.",
  },
  // agentcc/services/routing_key_rules.py UNSUPPORTED_KEYS: a new or changed
  // value is refused at save (400 unsupported_routing_key).
  "routing.unsupportedKeys": {
    kind: "not-enforced",
    reason:
      "Scheduled completions, model access groups and traffic mirroring do not run on this gateway: a new or changed value is refused when saved.",
  },
  // agentcc-gateway hook_hedge.go: the gateway races no request.
  "routing.fastest": {
    kind: "not-enforced",
    reason:
      "Race mode does not run on this gateway: a stored race setting is kept and not applied.",
  },

  // The cache: the gateway reads only on/off and the TTL for an organization.
  "cache.backends": {
    kind: "not-enforced",
    reason:
      "Only on/off and the TTL apply. The backend, its connection, size limits, semantic and edge settings are recorded only.",
  },

  "mcp.guardrails": {
    kind: "not-enforced",
    // NEW: a storage fact, not an implementation diagnostic; site words it.
    reason:
      "Saved MCP guardrail settings are stored with your organization's configuration.",
  },

  "customProperties.rules": {
    kind: "not-enforced",
    reason:
      "Required and Allowed Values are checked by the Validate API (POST /agentcc/custom-properties/validate/); they are not applied by the gateway to requests.",
  },
};

export function hasEnforcement(id) {
  return Object.prototype.hasOwnProperty.call(ENFORCEMENT, id);
}

export function enforcementFor(id) {
  if (!hasEnforcement(id)) throw new Error(`Unknown enforcement id: ${id}`);
  return ENFORCEMENT[id];
}
