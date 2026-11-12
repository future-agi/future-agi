// How the gateway runs an organization's checks (GET /-/config
// guardrail_pipeline: enabled, fail_open, mode), shown read-only. The
// gateway decides Mode and Fail Open for every organization. Never a claim
// the gateway did not make. The Guardrails page and the Org config editor
// both show this one sentence.
export function nodePipelineSentence(node) {
  if (!node || typeof node !== "object") {
    return "Set by this gateway, read-only: this gateway does not report how it runs organization checks.";
  }
  if (node.enabled === false) {
    return "Set by this gateway, read-only: guardrails are off on this gateway, so no check runs.";
  }
  const errors = node.fail_open
    ? "skips that check and continues with the remaining checks"
    : "produces a failed result evaluated against the check's threshold and action";
  return `Set by this gateway, read-only: enabled organization checks run one at a time and stop at the first block. For checks that do not rewrite the request, a timeout or recovered panic ${errors}. Remote-service errors returned as failed check results follow the check's threshold and action. A request-rewriting check that times out or panics blocks the request. The gateway sets this behavior for every organization.`;
}

// Said at the top of every Guardrails view, and wherever a guardrail control
// is disabled, while the gateway runs no check. The gateway turns its
// guardrail pipeline on only when its node config sets guardrails.enabled
// (server.go configHandler reports it as guardrail_pipeline.enabled).
export const GUARDRAILS_OFF_BANNER =
  "Guardrails are off on this gateway, so no check runs. Rules you save are stored and run when guardrails are turned on in the gateway config.";
export const GUARDRAILS_OFF_REASON =
  "Guardrails are off on this gateway, so no check runs.";
export const GUARDRAILS_OFF_TITLE = "Guardrails are off on this gateway";
export const NOT_ENFORCED_STATUS = "Not enforced";

// "on" | "off" | "unknown", or null while the gateway config is loading.
// Only a gateway that reports guardrail_pipeline.enabled === false is off;
// one that does not report it is unknown, and every page treats unknown as
// it treats on (nothing is hidden on a guess).
export function guardrailsState(gatewayConfigQuery) {
  if (!gatewayConfigQuery || gatewayConfigQuery.isLoading) return null;
  const node = gatewayConfigQuery.data?.guardrail_pipeline;
  if (!node || typeof node !== "object") return "unknown";
  return node.enabled === false ? "off" : "on";
}

export function guardrailsAreOff(gatewayConfigQuery) {
  return guardrailsState(gatewayConfigQuery) === "off";
}
