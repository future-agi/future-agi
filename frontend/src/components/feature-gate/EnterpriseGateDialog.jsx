import React from "react";
import PropTypes from "prop-types";
import { Box, Dialog, IconButton } from "@mui/material";
import Iconify from "src/components/iconify";
import { useRouter } from "src/routes/hooks";
import { paths } from "src/routes/paths";
import FeatureGateOverlay from "./FeatureGateOverlay";
import {
  ENTERPRISE_GATE_STEPS,
  SALES_MAILTO,
  enterpriseGateCopy,
} from "./enterprise-gate";

/**
 * The self-hosted Enterprise gate, shown when creating one more organization,
 * workspace or member is refused on Community (HTTP 402
 * ENTERPRISE_FEATURE_REQUIRED). Contact sales or activate a license; nothing
 * the install already has is affected.
 */
export default function EnterpriseGateDialog({ open, gate, onClose, note }) {
  const router = useRouter();
  const { title, description } = enterpriseGateCopy(gate);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      aria-label={title}
      PaperProps={{ sx: { overflow: "hidden", position: "relative" } }}
    >
      <Box sx={{ position: "absolute", top: 8, right: 8, zIndex: 2 }}>
        <IconButton size="small" aria-label="Close" onClick={onClose}>
          <Iconify icon="akar-icons:cross" sx={{ color: "text.primary" }} />
        </IconButton>
      </Box>
      <FeatureGateOverlay
        eyebrow="Enterprise feature"
        title={title}
        description={description}
        steps={[...ENTERPRISE_GATE_STEPS]}
        primaryLabel="Contact sales"
        primaryHref={SALES_MAILTO}
        secondaryLabel="Activate license"
        onSecondary={() => {
          onClose?.();
          router.push(paths.dashboard.settings.eeLicenses);
        }}
        footnote={note}
        minHeight={420}
        blurFrom={0}
      />
    </Dialog>
  );
}

EnterpriseGateDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  gate: PropTypes.shape({
    feature: PropTypes.string,
    limit: PropTypes.number,
    current: PropTypes.number,
    requested: PropTypes.number,
  }),
  onClose: PropTypes.func,
  note: PropTypes.string,
};
