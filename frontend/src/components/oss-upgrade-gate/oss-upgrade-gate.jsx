import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
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
  UNLOCK,
} from "./constants";

// A card for a feature this deployment cannot run: what the feature does and
// what it needs, over the team's blurred preview where the feature has one,
// and on a neutral surface where it has none.
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
  const reason = REASONS[reasonCode] || DEFAULT_REASON;
  const guided =
    Array.isArray(config.steps) &&
    (!reasonCode ||
      reasonCode === "LICENSE_MISSING" ||
      Object.hasOwn(UNLOCK, reasonCode));
  const unlock = guided ? UNLOCK[reasonCode] : undefined;
  const steps = !guided
    ? []
    : unlock
      ? [
          ...unlock.steps(config.name),
          ...(unlock.kept ? [config.kept] : config.steps.slice(1)),
        ].slice(0, 3)
      : config.steps;
  const toLicense = !guided && reason.toLicense;
  const preview = image || config.image;
  const gate = (
    <FeatureGateOverlay
      image={preview}
      imageDark={imageDark || config.imageDark}
      blurFrom={config.blurFrom}
      blur={config.blur}
      plain={!preview}
      eyebrow={
        unlock && "eyebrow" in unlock
          ? unlock.eyebrow
          : reason.eyebrow || config.eyebrow || DEFAULT_EYEBROW
      }
      title={
        guided
          ? unlock
            ? unlock.title(config.name)
            : config.title
          : `${config.name} is not available on this deployment`
      }
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
          {guided && reason.toLicense && (
            <Box component="span" sx={{ display: "block", mt: 1.5 }}>
              <Button
                variant="outlined"
                size="small"
                onClick={() => router.push(paths.dashboard.settings.eeLicenses)}
              >
                Manage license
              </Button>
            </Box>
          )}
        </>
      }
      steps={steps}
      footnote={reason.note || config.footnote}
      primaryLabel={
        unlock?.label || (guided ? "Upgrade to EE license key" : reason.label)
      }
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
  if (!config.observeTab) return gate;
  // ObservePage pads its header and tab bar 16 px each side but not the tab
  // body, where each tab pads itself. Without this inset the panel ran from
  // the sidebar border to the viewport edge under a toolbar inset 16 px, and
  // its bottom corners sat on the window edge.
  return (
    <Box sx={{ height: 1, boxSizing: "border-box", px: 2, pb: 2 }}>{gate}</Box>
  );
}

OSSUpgradeGate.propTypes = {
  feature: PropTypes.oneOf(Object.keys(FEATURES)).isRequired,
  image: PropTypes.string,
  imageDark: PropTypes.string,
  reasonCode: PropTypes.string,
};
