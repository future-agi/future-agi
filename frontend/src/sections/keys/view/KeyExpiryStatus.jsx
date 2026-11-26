import { Chip, Typography } from "@mui/material";
import { format } from "date-fns";
import PropTypes from "prop-types";
import React from "react";
import { isKeyExpired, parseExpiry } from "./keyExpiry";

const chipSx = {
  ml: 1,
  flexShrink: 0,
  height: 22,
  fontSize: 11,
  borderRadius: "4px",
};

const rowPropType = PropTypes.shape({
  enabled: PropTypes.bool,
  expires_at: PropTypes.string,
  is_expired: PropTypes.bool,
});

// Expired wins over Disabled: auth disables a key once it sees it expired,
// and the admin needs to know why it stopped working.
export const KeyStatusChip = ({ row }) => {
  if (isKeyExpired(row)) {
    return (
      <Chip label="Expired" color="warning" variant="outlined" sx={chipSx} />
    );
  }
  if (!row.enabled) {
    return (
      <Chip
        label="Disabled"
        sx={{ ...chipSx, color: "text.primary", bgcolor: "background.neutral" }}
      />
    );
  }
  return null;
};

KeyStatusChip.propTypes = { row: rowPropType.isRequired };

const KeyExpiryStatus = ({ row }) => {
  const expiresAt = parseExpiry(row?.expires_at);

  return (
    <Typography
      variant="body2"
      noWrap
      sx={{
        fontSize: 13,
        color: !expiresAt || isKeyExpired(row) ? "text.disabled" : undefined,
      }}
    >
      {expiresAt ? format(expiresAt, "MM-dd-yyyy") : "Never"}
    </Typography>
  );
};

KeyExpiryStatus.propTypes = { row: rowPropType };

export default KeyExpiryStatus;
