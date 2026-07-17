import React from "react";
import PropTypes from "prop-types";
import FeatureGateOverlay from "src/components/feature-gate/FeatureGateOverlay";
import { logger } from "src/utils/logger";
import errorFeedPreview from "src/assets/oss-gate/error_feed_light.png";
import errorFeedPreviewDark from "src/assets/oss-gate/error_feed_dark.png";
import optimizationPreview from "src/assets/oss-gate/optimization_light.png";
import optimizationPreviewDark from "src/assets/oss-gate/optimization_dark.png";

const CONTACT_URL = "https://futureagi.com/talk-to-human";
const DOCS_URL = "https://docs.futureagi.com";

const FEATURES = {
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

export default function OSSUpgradeGate({ feature, image, imageDark }) {
  const config = FEATURES[feature];
  if (!config) {
    logger.warn(`OSSUpgradeGate: unknown feature "${feature}"`);
    return null;
  }
  return (
    <FeatureGateOverlay
      image={image || config.image}
      imageDark={imageDark || config.imageDark}
      eyebrow={config.eyebrow}
      title={config.title}
      description={config.description}
      steps={config.steps}
      footnote={config.footnote}
      primaryLabel="Upgrade to EE license key"
      primaryHref={CONTACT_URL}
      secondaryLabel="Read docs"
      secondaryHref={config.docsUrl || DOCS_URL}
    />
  );
}

OSSUpgradeGate.propTypes = {
  feature: PropTypes.oneOf(Object.keys(FEATURES)).isRequired,
  image: PropTypes.string,
  imageDark: PropTypes.string,
};
