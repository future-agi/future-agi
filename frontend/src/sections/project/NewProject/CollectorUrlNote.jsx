import React from "react";
import PropTypes from "prop-types";
import { Typography } from "@mui/material";

// The keys snippet keeps YOUR_FI_COLLECTOR_URL when this self-hosted
// deployment has not published its trace collector address
// (tracer.views.project.collector_url_missing). A localhost fallback would
// send a reader's spans to their own machine, so say once, under the snippet,
// what to put there.
export default function CollectorUrlNote({ missing = false }) {
  if (!missing) return null;
  return (
    <Typography variant="body2" color="text.secondary" role="note">
      This deployment has not published its trace collector address yet, so the
      example keeps YOUR_FI_COLLECTOR_URL. Ask your admin for the address (set
      as FI_COLLECTOR_PUBLIC_URL).
    </Typography>
  );
}

CollectorUrlNote.propTypes = {
  missing: PropTypes.bool,
};
