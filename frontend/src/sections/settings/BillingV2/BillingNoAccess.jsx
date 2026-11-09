import React from "react";
import { Box, Typography } from "@mui/material";
import { BILLING_NO_ACCESS_MESSAGE } from "src/constants/usageCopy";

// Settings > Billing for a role that cannot open it (routes/sections/
// dashboard.jsx billingAllowedRoles): who can, on the Billing URL itself.
export default function BillingNoAccess() {
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1, p: 3 }}>
      <Typography component="h1" typography="m3" fontWeight="fontWeightMedium">
        Billing
      </Typography>
      <Typography typography="s1" color="text.secondary">
        {BILLING_NO_ACCESS_MESSAGE}
      </Typography>
    </Box>
  );
}
