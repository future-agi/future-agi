// What a gated feature is, and what it needs on this deployment.
//
// The card states facts only: what the feature does and what this deployment
// is missing for it. It shows no preview screenshot (sample data reads as the
// user's own history), no roadmap promise and no setup steps for parts that
// are not here.

export const CONTACT_URL = "https://futureagi.com/talk-to-human";
export const DOCS_URL = "https://docs.futureagi.com";

export const LICENSE_CTA = "Manage license";
export const CONTACT_CTA = "Talk to us";
export const DOCS_CTA = "Read docs";

export const DEFAULT_EYEBROW = "Enterprise Edition";

export const FEATURES = {
  errorFeed: {
    name: "Error Feed",
    description:
      "Error Feed groups the errors in your traces into issues you can triage, assign and resolve.",
  },
  knowledgeBase: {
    name: "Knowledge Base",
    description:
      "Knowledge Base stores your documents so agents can retrieve grounded answers from them.",
  },
  optimization: {
    name: "Optimization",
    description:
      "Optimization runs your prompts against your evals and keeps the best-scoring variant.",
  },
  falconAI: {
    name: "Falcon AI",
    description:
      "Falcon AI is an assistant that answers questions about your evals, datasets and traces in plain language.",
  },
  instantEval: {
    name: "Instant eval",
    eyebrow: "Enterprise feature",
    title: "Upgrade to use Instant eval",
    description:
      "Ask a question in plain English and Jev answers it for a sample or every trace, session or user in view; the first answers come in seconds.",
    steps: [
      "Talk to us about an Enterprise license that includes Instant eval.",
      "Pick a view in Observe and ask Jev a yes/no or category question.",
      "Answers land on a timeline as they come in, with what stands out.",
    ],
    kept: "Your past runs are kept and open again with a valid license.",
    footnote:
      "Jev evaluators come with every edition, open source included: add one from Evals with your own TypeSafe key.",
    docsUrl: "https://docs.futureagi.com/docs/observe/features/instant-eval",
    // Any supplied child preview is blurred as a whole element.
    blurFrom: 0,
    blur: 16,
    // Shown as an Observe tab (/observe/:id/ask), whose body has no side
    // padding: the gate keeps the toolbar's 16 px gutters itself.
    observeTab: true,
  },
  insights: {
    name: "Insights",
    eyebrow: "Enterprise feature",
    title: "Upgrade to use Insights",
    description:
      "Reports that sort your traces, sessions or users into categories, with the numbers behind each and what sets them apart.",
    steps: [
      "Talk to us about an Enterprise license that includes Insights.",
      "Choose what to learn (usage, failures, experience) and which data to read.",
      "Get categories, findings and examples, and run the report again as your traffic changes.",
    ],
    kept: "Your past reports are kept and open again with a valid license.",
    docsUrl: "https://docs.futureagi.com/docs/observe/features/insights",
    blurFrom: 0,
    blur: 16,
    // Shown as an Observe tab (/observe/:id/insights), as Instant eval.
    observeTab: true,
  },
  usageSummary: {
    name: "Usage summary",
    description: "Usage summary shows your usage, spend and credits.",
  },
  pricing: {
    name: "Plans & pricing",
    description: "Plans & pricing lists the plans you can move to.",
  },
  billing: {
    name: "Billing",
    description: "Billing holds your payment methods and invoices.",
  },
};

// What the feature needs, per denial reason from /api/capabilities/
// (tfc/licensing/types.py DenialReason). `toLicense` sends the primary button
// to Settings > License instead of the contact page.
export const REASONS = {
  EE_CODE_UNAVAILABLE: {
    note: "This deployment is running an image built without the EE package, so a license key alone will not enable this. Contact your administrator.",
    needs:
      "Needs the Enterprise Edition image and a license key. This deployment runs the open-source image, so a license key alone will not enable it. Contact your administrator.",
    label: CONTACT_CTA,
  },
  LICENSE_MISSING: {
    needs:
      "Needs an Enterprise license key. None is installed on this deployment.",
    label: LICENSE_CTA,
    toLicense: true,
  },
  LICENSE_FEATURE_MISSING: {
    note: "Your Enterprise license is active but doesn't include this feature.",
    needs:
      "Needs a license that includes this feature. The installed license does not.",
    label: LICENSE_CTA,
    toLicense: true,
  },
  LICENSE_EXPIRED: {
    note: "Your license has expired — renew it to restore access.",
    needs: "Needs a current license. The installed license has expired.",
    label: LICENSE_CTA,
    toLicense: true,
  },
  LICENSE_TRIAL_EXPIRED: {
    note: "Your trial has ended — add a license key to restore access.",
    needs: "Needs a license key. The trial has ended.",
    label: LICENSE_CTA,
    toLicense: true,
  },
  LICENSE_INVALID: {
    note: "Your license key could not be validated.",
    needs:
      "Needs a valid license key. The installed key could not be validated.",
    label: LICENSE_CTA,
    toLicense: true,
  },
  FEATURE_NOT_IN_GRACE: {
    note: "This feature is not available during your license's grace period.",
    needs:
      "Needs a current license. This feature is not available during the license's grace period.",
    label: LICENSE_CTA,
    toLicense: true,
  },
  LICENSE_VERSION_UNSUPPORTED: {
    note: "Your license does not cover this version.",
    needs:
      "Needs a license that covers this version. The installed license does not.",
    label: LICENSE_CTA,
    toLicense: true,
  },
  PLAN_FEATURE_MISSING: {
    note: "Your plan doesn't include this feature.",
    eyebrow: "Plan",
    needs:
      "Needs a plan that includes this feature. Your current plan does not.",
    label: CONTACT_CTA,
  },
  RESOLVER_UNAVAILABLE: {
    note: "Feature access could not be resolved on this deployment. Contact your administrator.",
    eyebrow: "Access",
    needs:
      "Feature access could not be checked on this deployment. Contact your administrator.",
    label: CONTACT_CTA,
  },
  FEATURE_UNKNOWN: {
    note: "This server does not recognise the feature — it may be running an older version.",
    eyebrow: "Access",
    needs:
      "This server does not recognise the feature. It may be running an older version.",
    label: CONTACT_CTA,
  },
};

const NEW_KEY_STEP =
  "Set the new key as EE_LICENSE_KEY on this deployment and restart it.";

// How a feature that sets `name` is unlocked, by why it is locked: the card's
// title, the steps that come before the feature's own and the button. Someone
// who already holds a license is told how to change it, not to buy one.
export const UNLOCK = {
  LICENSE_FEATURE_MISSING: {
    title: (name) => `Add ${name} to your license`,
    steps: (name) => [
      `Ask us to add ${name} to your Enterprise license.`,
      NEW_KEY_STEP,
    ],
    label: CONTACT_CTA,
  },
  LICENSE_EXPIRED: {
    title: (name) => `Renew your license to use ${name}`,
    steps: () => ["Talk to us to renew your Enterprise license.", NEW_KEY_STEP],
    kept: true,
    label: CONTACT_CTA,
  },
  LICENSE_TRIAL_EXPIRED: {
    title: (name) => `Get a license to keep using ${name}`,
    steps: (name) => [
      `Talk to us about an Enterprise license that includes ${name}.`,
      "Set the key as EE_LICENSE_KEY on this deployment and restart it.",
    ],
    kept: true,
    label: CONTACT_CTA,
  },
  LICENSE_INVALID: {
    title: (name) => `Fix your license to use ${name}`,
    steps: () => [
      "Check that EE_LICENSE_KEY on this deployment holds the whole key, exactly as we sent it, then restart it.",
      "If it still can't be validated, talk to us for a new key.",
    ],
    kept: true,
    label: CONTACT_CTA,
  },
  PLAN_FEATURE_MISSING: {
    // Cloud: no plan is named here, so the card claims no edition.
    eyebrow: null,
    title: (name) => `Upgrade your plan to use ${name}`,
    steps: (name) => [`Talk to us about a plan that includes ${name}.`],
    label: CONTACT_CTA,
  },
};

export const DEFAULT_REASON = {
  needs: "Needs an Enterprise license.",
  label: CONTACT_CTA,
};
