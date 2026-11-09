import React from "react";
import PropTypes from "prop-types";

import CustomStatusChip from "src/components/custom-status-chip/CustomStatusChip";
import { STATUS_TYPES } from "src/utils/statusUtils";

// Gateway states on the Future AGI status chip (the one Observe uses), so a
// healthy provider and a passing span read the same way in light and dark.
const TONES = {
  healthy: STATUS_TYPES.PASS,
  error: STATUS_TYPES.ERROR,
  warning: STATUS_TYPES.CANCELED,
  pending: STATUS_TYPES.RUNNING,
  neutral: STATUS_TYPES.UNSET,
};

export const GATEWAY_STATUS_TONES = Object.keys(TONES);

// The neutral chip ("Not enforced on this gateway", "Warns") carries the
// honest labels, so it must read: the shared grey is 2.54:1 on its fill. It
// takes text.secondary through the chip's opt-in neutralTextColor (TH-4088,
// as on Observe); every other tone keeps the shared status colours.
export default function GatewayStatusChip({ tone, label, showIcon = true }) {
  return (
    <CustomStatusChip
      status={TONES[tone] || STATUS_TYPES.UNSET}
      label={label}
      showIcon={showIcon}
      neutralTextColor="text.secondary"
    />
  );
}

GatewayStatusChip.propTypes = {
  tone: PropTypes.oneOf(GATEWAY_STATUS_TONES).isRequired,
  label: PropTypes.string.isRequired,
  showIcon: PropTypes.bool,
};
