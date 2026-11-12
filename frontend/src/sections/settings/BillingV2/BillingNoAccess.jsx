import React from "react";
import { Box } from "@mui/material";
import PageHeader from "src/components/page-header";
import { BILLING_NO_ACCESS_MESSAGE } from "src/constants/usageCopy";

// Settings > Billing for a role that cannot open it (routes/sections/
// dashboard.jsx billingAllowedRoles): who can, on the Billing URL itself.
// No padding of its own: the settings shell supplies the page inset, as it
// does for the Billing page, and the title is the same shared page header.
export default function BillingNoAccess() {
  return (
    <Box>
      <PageHeader title="Billing" description={BILLING_NO_ACCESS_MESSAGE} />
    </Box>
  );
}
