import React from "react";
import PropTypes from "prop-types";
import FeatureGateOverlay from "src/components/feature-gate/FeatureGateOverlay";
import { useRouter } from "src/routes/hooks";
import { paths } from "src/routes/paths";
import { logger } from "src/utils/logger";
import {
  CONTACT_URL,
  DOCS_URL,
  DOCS_CTA,
  UPGRADE_CTA,
  FEATURES,
  REASONS,
} from "./constants";

export default function OSSUpgradeGate({ feature, image, imageDark, reasonCode }) {
  const router = useRouter();
  const config = FEATURES[feature];
  if (!config) {
    logger.warn(`OSSUpgradeGate: unknown feature "${feature}"`);
    return null;
  }
  const reason = REASONS[reasonCode];
  return (
    <FeatureGateOverlay
      image={image || config.image}
      imageDark={imageDark || config.imageDark}
      eyebrow={config.eyebrow}
      title={config.title}
      description={config.description}
      steps={config.steps}
      footnote={reason?.note || config.footnote}
      primaryLabel={reason?.label || UPGRADE_CTA}
      primaryHref={reason?.toLicense ? undefined : CONTACT_URL}
      onPrimary={
        reason?.toLicense
          ? () => router.push(paths.dashboard.settings.eeLicenses)
          : undefined
      }
      secondaryLabel={DOCS_CTA}
      secondaryHref={config.docsUrl || DOCS_URL}
    />
  );
}

OSSUpgradeGate.propTypes = {
  feature: PropTypes.oneOf(Object.keys(FEATURES)).isRequired,
  image: PropTypes.string,
  imageDark: PropTypes.string,
  reasonCode: PropTypes.string,
};
