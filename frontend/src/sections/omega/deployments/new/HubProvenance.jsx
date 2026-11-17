import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";

/** Hub identity is not the recomputed deployment archive/content identity. */
export default function HubProvenance({ provenance }) {
  if (!provenance) return null;
  return (
    <Box data-testid="hub-source-provenance" sx={{ overflowWrap: "anywhere" }}>
      <Typography typography="s2">Hub source: {provenance.ref}</Typography>
      <Typography typography="s3" color="text.secondary">
        Hub catalog digest: {provenance.digest}
      </Typography>
      <Typography typography="s3" color="text.secondary">
        Hub archive SHA-256: {provenance.bundleSha256}
      </Typography>
    </Box>
  );
}
HubProvenance.propTypes = { provenance: PropTypes.object };
