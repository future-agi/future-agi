/**
 * Future AGI's words for why a template, or any create at all, is not offered.
 *
 * EE's reasons are written for whoever runs the deployment: a template whose
 * image is missing says "not on the container host yet. An operator builds it
 * with deploy/sandbox-images/build.mjs", and a refused create ends with "An
 * operator can offer ...". A customer can do nothing with either, and COPY1
 * bans "operator", "provider" and "binding" on customer screens. So the tab
 * words them by the structured field EE sends (`refusedBy`, `field`) and keeps
 * only EE sentences that are about this workspace.
 */

const TEMPLATE_REASONS = {
  host: "Not available on this deployment right now.",
  operator: "Not offered on this deployment.",
  workspace:
    "Not offered in this workspace. A workspace admin can turn it on in Settings.",
};

// Sentences written for the people who run the deployment, not for customers,
// and "not ... yet" promises (the owner's rule: nothing reads as unfinished).
const OPERATOR_WORDS =
  /\b(operator|operators|provider|providers|binding|colima|yet)\b|container host|\.mjs\b|\bdeploy[/]/i;

/** EE's sentences minus those addressed to the people who run the deployment. */
export function customerSentences(text) {
  if (typeof text !== "string") return "";
  // A sentence ends at . ! or ? followed by a space, so "0.5 CPU" stays whole.
  return text
    .split(/(?<=[.!?])\s+/)
    .map((sentence) => sentence.trim())
    .filter((sentence) => sentence && !OPERATOR_WORDS.test(sentence))
    .join(" ");
}

/** Why this template cannot be picked, for the card and the form. */
export function templateReason(template) {
  if (!template || template.available) return null;
  const known = TEMPLATE_REASONS[template.refusedBy];
  if (known) return known;
  // "size" (EE's own words name the size it needs) and older EEs without
  // `refusedBy`: EE's text, without any sentence meant for the operator.
  return customerSentences(template.reason) || TEMPLATE_REASONS.operator;
}

const SIZE_REASONS = {
  host: "Too big for the machines this deployment runs sandboxes on.",
  operator: "Not offered on this deployment.",
  workspace:
    "Not offered in this workspace. A workspace admin can turn it on in Settings.",
};

/**
 * Why this size cannot be picked. EE's "ceiling" reason states this
 * deployment's limit in plain words and stays; its host reason names the
 * "container host" (COPY1), so it is worded here.
 */
export function sizeReason(size) {
  if (!size || size.available) return null;
  return (
    SIZE_REASONS[size.refusedBy] ||
    customerSentences(size.reason) ||
    SIZE_REASONS.operator
  );
}

const REFUSAL_HEADLINES = {
  size: "None of the sizes this workspace offers can run on this deployment.",
  template:
    "None of the templates or images this workspace offers can be used on this deployment.",
};

/**
 * Why nothing can be created here: EE's sentences about this workspace (which
 * sizes or templates, and what a workspace admin can change), or Future AGI's
 * own line for the refused field when every sentence was meant for the
 * operator.
 */
export function createRefusalText(refusal) {
  if (!refusal) return null;
  return (
    customerSentences(refusal.reason) ||
    REFUSAL_HEADLINES[refusal.field] ||
    "New sandboxes cannot be created in this workspace right now."
  );
}

/**
 * Why New sandbox is off at the running cap, and what to do about it. EE
 * counts running sandboxes only (hibernated ones do not), and refuses a create
 * or a resume past the cap with 409 quota_exceeded.
 */
export function capReason(active, max) {
  return `${active} visible sandboxes are running, at the workspace limit of ${max}. Hibernate or delete one before trying to start another.`;
}

// EE refuses a network-on start with 503 `sandboxes_unavailable` and one of
// these reasons. The shared copy for that code says "Sandboxes aren't turned on
// for this workspace", which is false here: network-off sandboxes still work.
const NETWORK_ON_REASONS = new Set([
  "egress_firewall_missing",
  "runsc_required",
  "sandbox_network_posture",
]);

/**
 * Why the create drawer offers network off only. EE leaves `full` out of
 * caps.networkModes for one of two causes: this workspace's settings (no
 * code), or this deployment cannot run a sandbox with the network on safely
 * (`networkUnavailableCode`, for example `runsc_required`: network on runs
 * only under gVisor). EE's reason for the second is written for whoever runs
 * the deployment ("an operator can install gVisor ..."), so it is worded here.
 */
export function networkOffText(caps) {
  if (caps?.networkUnavailableCode) {
    return "Sandboxes with the network on can't run on this deployment right now, so new sandboxes here start with the network off: no network interface besides loopback.";
  }
  return "This workspace offers sandboxes without a network only. The container has no network interface besides loopback.";
}

/** The message for a failed create, resume or restore, in words that fit it. */
export function sandboxErrorText(error) {
  if (!error) return null;
  if (
    error.code === "sandboxes_unavailable" &&
    NETWORK_ON_REASONS.has(error.reason)
  ) {
    return "Sandboxes with the network on can't start on this deployment right now, so nothing was started. Sandboxes with the network off are not affected.";
  }
  return error.message;
}
