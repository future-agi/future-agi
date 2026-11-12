import React from "react";
import PropTypes from "prop-types";
import { Box, Link } from "@mui/material";
import { Link as RouterLink } from "react-router-dom";
import { paths } from "src/routes/paths";
import { isGatewayPageHidden } from "../shell/page-status";
import { GATEWAY_MONO } from "../utils/fonts";

/**
 * A run id linked to its run page. Extra props go to the MUI Link. While the
 * runs pages are hidden (shell/page-status.js) the id is plain text: a link
 * would only bounce back to the logs.
 */
export default function RunLink({ runId, sx, ...other }) {
  if (isGatewayPageHidden("runs")) {
    return (
      <Box
        component="span"
        sx={{ fontFamily: GATEWAY_MONO, ...sx }}
        data-testid="run-id"
      >
        {runId}
      </Box>
    );
  }
  return (
    <Link
      component={RouterLink}
      to={paths.dashboard.gateway.runDetail(runId)}
      // Inside a clickable table row, open the run, not the row's drawer.
      onClick={(event) => event.stopPropagation()}
      sx={{ fontFamily: GATEWAY_MONO, ...sx }}
      {...other}
    >
      {runId}
    </Link>
  );
}

RunLink.propTypes = {
  runId: PropTypes.string.isRequired,
  sx: PropTypes.object,
};
