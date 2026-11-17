// The gateway's own default Mode and Fail Open (GET /-/config
// guardrail_pipeline: enabled, fail_open, mode), shown read-only. They apply
// only to the checks in the gateway's own configuration: your organization
// decides Mode and Fail Open for its checks. Never a claim the gateway did
// not make. The Guardrails page shows this one sentence.
const DEFAULT_PREFIX =
  "Gateway default for the checks in this gateway's own configuration:";
const ORG_OWN =
  "Your organization's Mode and Fail Open settings apply to its checks.";
const REWRITE_RULE =
  "A check that rewrites the request and does not finish always refuses the request.";

export function nodePipelineSentence(node) {
  if (!node || typeof node !== "object") {
    return `${DEFAULT_PREFIX} this gateway does not report it. ${ORG_OWN}`;
  }
  if (node.enabled === false) {
    return "Guardrails are off on this gateway, so no check runs.";
  }
  const order =
    node.mode === "sequential"
      ? " they run in turn and stop at the first block."
      : node.mode === "parallel"
        ? " they run together."
        : "";
  const errors = node.fail_open
    ? "skips that check and continues with the remaining checks"
    : "produces a failed result evaluated against the check's threshold and action";
  return `${DEFAULT_PREFIX}${order} For checks that do not rewrite the request, a timeout or recovered panic ${errors}. Remote-service errors returned as failed check results follow the check's threshold and action. ${ORG_OWN} ${REWRITE_RULE}`;
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
