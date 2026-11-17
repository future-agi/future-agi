import React from "react";
import Alert from "@mui/material/Alert";
import Chip from "@mui/material/Chip";
import Typography from "@mui/material/Typography";

// E06 operator disclosure. Keep this text verbatim across the enabling path.
// The literal must stay a standalone double-quoted line for the COPY1 exception.
export const DISCLOSURE =
  "Self-hosted Deployments is a preview. Run it only on a dedicated deploy-engine host you own, not shared with other workloads or tenants. Until a separate control daemon ships, the server and its deploy engine are root-equivalent on that host. Each deployment's outbound network is isolated in its own network namespace, which is on by default.";

// Admission remains server-owned: hosted withholds the Deployments product
// and Early access row. This notice never enables or advertises a hidden row.
export default function DeploymentPreviewNotice() {
  return (
    <Alert severity="warning" sx={{ my: 1.5 }}>
      <Chip size="small" label="Preview" color="warning" sx={{ mb: 1 }} />
      <Typography component="p" typography="s2">
        {DISCLOSURE}
      </Typography>
    </Alert>
  );
}
