import React from "react";
import PropTypes from "prop-types";
import { Box, Tooltip } from "@mui/material";
import { INTEGRATION_ADMIN_ONLY } from "./access";

// A disabled control does not raise a tooltip, so the reason sits on a
// wrapping element. Renders the control unchanged for a role that may write.
export default function IntegrationAdminOnly({ allowed, block, children }) {
  if (allowed) return children;
  return (
    <Tooltip title={INTEGRATION_ADMIN_ONLY}>
      <Box component="span" display={block ? "block" : "inline-flex"}>
        {children}
      </Box>
    </Tooltip>
  );
}

IntegrationAdminOnly.propTypes = {
  allowed: PropTypes.bool.isRequired,
  block: PropTypes.bool,
  children: PropTypes.node.isRequired,
};
