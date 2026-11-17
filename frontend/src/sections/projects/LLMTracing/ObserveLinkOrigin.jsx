import React from "react";
import PropTypes from "prop-types";
import { Box, Button, Typography } from "@mui/material";
import { Link as RouterLink } from "react-router-dom";

import { parseObserveOrigin } from "src/sections/omega/shared/observeLinks";

/**
 * The return affordance for links opened from a Future AGI product tab.
 *
 * Query parameters are untrusted input. `parseObserveOrigin` only returns a
 * bounded label paired with a same-origin path, so this component never turns
 * an arbitrary URL into a clickable outbound link. RouterLink keeps the back
 * action inside the SPA rather than forcing a full document reload.
 */
export default function ObserveLinkOrigin({
  search = typeof window === "undefined" ? "" : window.location.search,
  location = typeof window === "undefined" ? null : window.location,
}) {
  const origin = parseObserveOrigin({ search, location });
  if (!origin) return null;

  return (
    <Box
      data-testid="observe-link-origin"
      sx={{
        display: "flex",
        alignItems: { xs: "flex-start", sm: "center" },
        justifyContent: "space-between",
        gap: 1.5,
        flexWrap: "wrap",
        px: 2,
        py: 1,
        mb: 1,
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
      }}
    >
      <Typography typography="s2" color="text.secondary">
        Opened from <strong>{origin.origin}</strong>. These are ordinary
        filters.
      </Typography>
      <Button
        component={RouterLink}
        to={origin.returnTo}
        size="small"
        variant="outlined"
        color="inherit"
        data-testid="observe-link-back"
      >
        Back
      </Button>
    </Box>
  );
}

ObserveLinkOrigin.propTypes = {
  search: PropTypes.string,
  location: PropTypes.shape({ origin: PropTypes.string }),
};
