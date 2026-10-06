// Self-hosted Enterprise gate (TH-8084): the backend refuses a 2nd
// organization, a 2nd workspace or a 4th member on Community with HTTP 402
// ENTERPRISE_FEATURE_REQUIRED and an `enterprise_gate` block. It is never a
// usage limit: the copy names the Community allowance and offers Contact sales
// and Activate license.

export const SALES_EMAIL = "sales@futureagi.com";
export const SALES_MAILTO = `mailto:${SALES_EMAIL}`;

export const ENTERPRISE_GATE_CODE = "ENTERPRISE_FEATURE_REQUIRED";

// Window event the axios interceptor dispatches for edition gates;
// EnterpriseGateHost listens and opens the dialog.
export const ENTERPRISE_GATE_EVENT = "enterprise-gate";

// The Community edition rule's features. Product gates (falcon_ai, ...) carry
// an `enterprise_gate` too but keep their existing in-page upgrade surfaces.
export const EDITION_GATE_FEATURES = Object.freeze([
  "members",
  "organizations",
  "workspaces",
]);

export function isEditionGate(gate) {
  return Boolean(gate) && EDITION_GATE_FEATURES.includes(gate.feature);
}

export function dispatchEnterpriseGate(gate) {
  window.dispatchEvent(
    new CustomEvent(ENTERPRISE_GATE_EVENT, { detail: gate }),
  );
}

const EDITION_COPY = {
  members: {
    title: "Add more members with Enterprise",
    allowance: (limit) =>
      `Community includes up to ${limit ?? 3} organization members.`,
    more: "More members are an Enterprise feature.",
  },
  organizations: {
    title: "Create more organizations with Enterprise",
    allowance: () => "Community includes one organization.",
    more: "More organizations are an Enterprise feature.",
  },
  workspaces: {
    title: "Create more workspaces with Enterprise",
    allowance: () => "Community includes one workspace.",
    more: "More workspaces are an Enterprise feature.",
  },
};

export const ENTERPRISE_GATE_STEPS = Object.freeze([
  `Contact ${SALES_EMAIL} for an Enterprise license.`,
  "Set EE_LICENSE_KEY on every backend, worker and Temporal worker, then restart them.",
  "Everything you already have stays as it is.",
]);

/** Title and description for an `enterprise_gate` block. */
export function enterpriseGateCopy(gate) {
  const copy = EDITION_COPY[gate?.feature];
  if (!copy) {
    return {
      title: "This is an Enterprise feature",
      description:
        "It is included with a Future AGI Enterprise license on self-hosted installs.",
    };
  }
  return {
    title: copy.title,
    description: `${copy.allowance(gate?.limit)} ${copy.more}`,
  };
}
