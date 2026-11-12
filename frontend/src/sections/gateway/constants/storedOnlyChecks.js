// Checks the console keeps only as stored: the partner checks whose partner
// call this release has not exercised against a working service, and the
// tool permissions check, a tool-call check the console does not offer
// (NOT_OFFERED_CHECKS in GuardrailConfigTab). The console has no way to add
// one, turn one on or change its settings. One an organization already saved
// stays in its config exactly as saved, and the config push sends it to the
// gateway as before, so a request it blocked is still blocked. The Overview
// lists every stored one read-only, as do Rules and the Org config editor.
// An Owner or Admin can turn one off, or remove one in Rules/the editor,
// each after a confirmation. The names
// are as stored, which for these checks is also the catalog name.
export const STORED_ONLY_CHECKS = new Set([
  "llama-guard",
  "azure-content-safety",
  "presidio-pii",
  "bedrock-guardrails",
  "hiddenlayer-guard",
  "aporia-guard",
  "pangea-guard",
  "dynamoai-guard",
  "enkrypt-guard",
  "ibm-ai-detector",
  "grayswan-guard",
  "lasso-guard",
  "crowdstrike-aidr",
  "zscaler-guard",
  "tool-permissions",
]);

export const isStoredOnlyCheck = (name) => STORED_ONLY_CHECKS.has(name);

export const STORED_ONLY_PARTNER_TITLE = "Saved partner checks";

export const STORED_ONLY_PARTNER_HELP =
  "Your organization saved these partner checks before. This release has not verified their partner services, so it offers no way to add one, turn one on or change one. Each stays in your config exactly as saved and is sent to the gateway as stored. An Owner or Admin can turn one off or remove it.";

export const STORED_ONLY_OVERVIEW_HELP =
  "Read-only rows are checks your organization saved before that this release does not offer. They are kept and sent to the gateway exactly as saved. An Owner or Admin can turn one off here; none can be turned on or edited.";

// The confirmation for turning a stored-only check off: in an editor's draft
// (applyLabel names the button that applies it), or at once (no applyLabel).
export const storedOnlyOffMessage = (name, applyLabel) =>
  applyLabel
    ? `Turn off "${name}" in the draft? This release offers no way to turn it back on from the console. Click ${applyLabel} to apply.`
    : `Turn off "${name}"? It is saved off and pushed to the gateway. This release offers no way to turn it back on from the console.`;
