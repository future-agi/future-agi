import React from "react";
import { Alert } from "@mui/material";
import { useViewOnlyReviewer } from "./useViewOnlyReviewer";

/**
 * The review role split in the annotate workspace (W4-3): a workspace viewer
 * sees every item's source material but no score inputs, Submit or Skip;
 * the server answers their submit with 403 (`score`). Members and editors
 * see nothing here.
 */
export default function RoleNotice() {
  const viewOnly = useViewOnlyReviewer();
  if (!viewOnly) return null;
  return (
    <Alert
      severity="info"
      data-testid="role-notice"
      sx={{ mx: 3, mt: 1, py: 0.25 }}
    >
      View only · You can inspect these items; workspace members score them.
    </Alert>
  );
}
