import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import FeatureGateOverlay from "src/components/feature-gate/FeatureGateOverlay";
import { useRouter } from "src/routes/hooks";
import { paths } from "src/routes/paths";
import { logger } from "src/utils/logger";
import {
  CONTACT_URL,
  DEFAULT_EYEBROW,
  DEFAULT_REASON,
  DOCS_URL,
  DOCS_CTA,
  FEATURES,
  REASONS,
} from "./constants";

// A neutral card for a feature this deployment cannot run: what the feature
// does and what it needs. No preview backdrop.
export default function OSSUpgradeGate({ feature, reasonCode }) {
  const router = useRouter();
  const config = FEATURES[feature];
  if (!config) {
    logger.warn(`OSSUpgradeGate: unknown feature "${feature}"`);
    return null;
  }
  const reason = REASONS[reasonCode] || DEFAULT_REASON;
  return (
    <FeatureGateOverlay
      plain
      eyebrow={reason.eyebrow || DEFAULT_EYEBROW}
      title={`${config.name} is not available on this deployment`}
      description={
        <>
          {config.description}
          <Box
            component="span"
            data-testid="oss-gate-needs"
            sx={{
              display: "block",
              mt: 1.5,
              color: "text.primary",
              fontWeight: "fontWeightMedium",
            }}
          >
            {reason.needs}
          </Box>
        </>
      }
      primaryLabel={reason.label}
      primaryHref={reason.toLicense ? undefined : CONTACT_URL}
      onPrimary={
        reason.toLicense
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
  reasonCode: PropTypes.string,
};
