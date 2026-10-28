import errorFeedPreview from "src/assets/oss-gate/error_feed_light.png";
import errorFeedPreviewDark from "src/assets/oss-gate/error_feed_dark.png";
import optimizationPreview from "src/assets/oss-gate/optimization_light.png";
import optimizationPreviewDark from "src/assets/oss-gate/optimization_dark.png";
import falconAIPreview from "src/assets/oss-gate/falcon_ai_light.png";
import falconAIPreviewDark from "src/assets/oss-gate/falcon_ai_dark.png";
import instantEvalPreview from "src/assets/oss-gate/instant_eval_light.png";
import instantEvalPreviewDark from "src/assets/oss-gate/instant_eval_dark.png";
import insightsPreview from "src/assets/oss-gate/insights_light.png";
import insightsPreviewDark from "src/assets/oss-gate/insights_dark.png";

export const CONTACT_URL = "https://futureagi.com/talk-to-human";
export const DOCS_URL = "https://docs.futureagi.com";

export const LICENSE_CTA = "Manage license";
export const CONTACT_CTA = "Talk to us";
export const UPGRADE_CTA = "Upgrade to EE license key";
export const DOCS_CTA = "Read docs";

export const FEATURES = {
  errorFeed: {
    eyebrow: "Cloud feature",
    title: "Upgrade to access Error Feed",
    description:
      "Auto-clustered error triage and production failure insights, so you can ship with confidence.",
    image: errorFeedPreview,
    imageDark: errorFeedPreviewDark,
    steps: [
      "Talk to us to enable Error Feed on your workspace.",
      "Point your SDK or project at Future AGI.",
      "Errors are clustered and start flowing into this feed automatically.",
    ],
    footnote:
      "Prefer self-hosting? Error Feed is on the open-source roadmap — star the repo to follow along.",
  },
  knowledgeBase: {
    eyebrow: "Cloud feature",
    title: "Upgrade to access Knowledge Base",
    description:
      "Ground your agents in your own documents with managed retrieval when you're ready to scale.",
    steps: [
      "Talk to us to enable Knowledge Base on your workspace.",
      "Upload your documents or connect a source.",
      "Your agents retrieve grounded answers from your knowledge automatically.",
    ],
  },
  optimization: {
    eyebrow: "Cloud feature",
    title: "Upgrade to run Optimization",
    description:
      "Automatically optimize your prompts and agents against your evals when you're ready to scale.",
    image: optimizationPreview,
    imageDark: optimizationPreviewDark,
    steps: [
      "Talk to us to enable Optimization on your workspace.",
      "Pick a dataset or simulation and choose an optimizer.",
      "We iterate on your prompts and surface the best-performing variant.",
    ],
  },
  falconAI: {
    eyebrow: "Cloud feature",
    title: "Upgrade to use Falcon AI",
    description:
      "Your AI copilot for Future AGI — ask questions, build evals, and debug traces in natural language.",
    image: falconAIPreview,
    imageDark: falconAIPreviewDark,
    steps: [
      "Talk to us to enable Falcon AI on your workspace.",
      "Connect your tools so Falcon AI can act on your data.",
      "Ask in plain language and Falcon AI does the work for you.",
    ],
  },
  instantEval: {
    eyebrow: "Enterprise feature",
    title: "Upgrade to use Instant eval",
    description:
      "Ask a question in plain English and Jev answers it for a sample or every trace, session or user in view; the first answers come in seconds.",
    image: instantEvalPreview,
    imageDark: instantEvalPreviewDark,
    steps: [
      "Talk to us about an Enterprise license that includes Instant eval.",
      "Pick a view in Observe and ask Jev a yes/no or category question.",
      "Answers land on a timeline as they come in, with what stands out.",
    ],
    footnote:
      "Jev evaluators come with every edition, open source included: add one from Evals with your own TypeSafe key.",
    docsUrl: "https://docs.futureagi.com/docs/observe/features/instant-eval",
    // The preview is an example run, not this workspace's data: blur all of
    // it, enough that none of its invented text can be read.
    blurFrom: 0,
    blur: 16,
  },
  insights: {
    eyebrow: "Enterprise feature",
    title: "Upgrade to use Insights",
    description:
      "Reports that sort your traces, sessions or users into categories, with the numbers behind each and what sets them apart.",
    image: insightsPreview,
    imageDark: insightsPreviewDark,
    steps: [
      "Talk to us about an Enterprise license that includes Insights.",
      "Choose what to learn (usage, failures, experience) and which data to read.",
      "Get categories, findings and examples, and run the report again as your traffic changes.",
    ],
    docsUrl: "https://docs.futureagi.com/docs/observe/features/insights",
    blurFrom: 0,
    blur: 16,
  },
  usageSummary: {
    eyebrow: "Cloud feature",
    title: "Upgrade to see usage & spend",
    description:
      "Full visibility into your usage, spend, and credits when you're ready to grow.",
  },
  pricing: {
    eyebrow: "Cloud feature",
    title: "Upgrade for flexible plans",
    description:
      "Flexible plans that scale with your team when you're ready to grow.",
  },
  billing: {
    eyebrow: "Cloud feature",
    title: "Upgrade to manage billing",
    description:
      "Payment methods, invoices, and budget controls when you're ready to grow.",
  },
};

export const REASONS = {
  LICENSE_EXPIRED: {
    note: "Your license has expired — renew it to restore access.",
    label: LICENSE_CTA,
    toLicense: true,
  },
  LICENSE_TRIAL_EXPIRED: {
    note: "Your trial has ended — add a license key to restore access.",
    label: LICENSE_CTA,
    toLicense: true,
  },
  LICENSE_INVALID: {
    note: "Your license key could not be validated.",
    label: LICENSE_CTA,
    toLicense: true,
  },
  FEATURE_NOT_IN_GRACE: {
    note: "This feature is not available during your license's grace period.",
    label: LICENSE_CTA,
    toLicense: true,
  },
  LICENSE_VERSION_UNSUPPORTED: {
    note: "Your license does not cover this version.",
    label: LICENSE_CTA,
    toLicense: true,
  },
  EE_CODE_UNAVAILABLE: {
    note: "This deployment is running an image built without the EE package, so a license key alone will not enable this. Contact your administrator.",
    label: CONTACT_CTA,
  },
  RESOLVER_UNAVAILABLE: {
    note: "Feature access could not be resolved on this deployment. Contact your administrator.",
    label: CONTACT_CTA,
  },
  FEATURE_UNKNOWN: {
    note: "This server does not recognise the feature — it may be running an older version.",
    label: CONTACT_CTA,
  },
};
