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
    needs:
      "Needs a license that includes this feature. The installed license does not.",
    label: LICENSE_CTA,
    toLicense: true,
  },
  LICENSE_EXPIRED: {
    needs: "Needs a current license. The installed license has expired.",
    label: LICENSE_CTA,
    toLicense: true,
  },
  LICENSE_TRIAL_EXPIRED: {
    needs: "Needs a license key. The trial has ended.",
    label: LICENSE_CTA,
    toLicense: true,
  },
  LICENSE_INVALID: {
    needs:
      "Needs a valid license key. The installed key could not be validated.",
    label: LICENSE_CTA,
    toLicense: true,
  },
  FEATURE_NOT_IN_GRACE: {
    needs:
      "Needs a current license. This feature is not available during the license's grace period.",
    label: LICENSE_CTA,
    toLicense: true,
  },
  LICENSE_VERSION_UNSUPPORTED: {
    needs:
      "Needs a license that covers this version. The installed license does not.",
    label: LICENSE_CTA,
    toLicense: true,
  },
  PLAN_FEATURE_MISSING: {
    eyebrow: "Plan",
    needs:
      "Needs a plan that includes this feature. Your current plan does not.",
    label: CONTACT_CTA,
  },
  RESOLVER_UNAVAILABLE: {
    eyebrow: "Access",
    needs:
      "Feature access could not be checked on this deployment. Contact your administrator.",
    label: CONTACT_CTA,
  },
  FEATURE_UNKNOWN: {
    eyebrow: "Access",
    needs:
      "This server does not recognise the feature. It may be running an older version.",
    label: CONTACT_CTA,
  },
};

export const DEFAULT_REASON = {
  needs: "Needs an Enterprise license.",
  label: CONTACT_CTA,
};
