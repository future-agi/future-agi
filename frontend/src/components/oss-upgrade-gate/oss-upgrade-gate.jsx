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
  UNLOCK,
} from "./constants";

export default function OSSUpgradeGate({
  feature,
  image,
  imageDark,
  reasonCode,
}) {
  const router = useRouter();
  const config = FEATURES[feature];
  if (!config) {
    logger.warn(`OSSUpgradeGate: unknown feature "${feature}"`);
    return null;
  }
  const reason = REASONS[reasonCode];
  const unlock = config.name ? UNLOCK[reasonCode] : undefined;
  // The card keeps three steps: how to unlock, then what the feature does
  // (or that past work is kept, for someone who had it).
  const steps = unlock
    ? [
        ...unlock.steps(config.name),
        ...(unlock.kept ? [config.kept] : config.steps.slice(1)),
      ].slice(0, 3)
    : config.steps;
  const toLicense = !unlock && reason?.toLicense;
  return (
    <FeatureGateOverlay
      image={image || config.image}
      imageDark={imageDark || config.imageDark}
      eyebrow={unlock && "eyebrow" in unlock ? unlock.eyebrow : config.eyebrow}
      title={unlock ? unlock.title(config.name) : config.title}
      description={config.description}
      steps={steps}
      blurFrom={config.blurFrom}
      blur={config.blur}
      footnote={reason?.note || config.footnote}
      primaryLabel={unlock?.label || reason?.label || UPGRADE_CTA}
      primaryHref={toLicense ? undefined : CONTACT_URL}
      onPrimary={
        toLicense
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
