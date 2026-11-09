import React from "react";
import { Helmet } from "react-helmet-async";
import { Navigate } from "react-router";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import UsageSummaryV2 from "src/sections/settings/UsageSummaryV2/UsageSummaryV2";
import GatewayUsageSplit from "src/sections/settings/UsageSummaryV2/GatewayUsageSplit";
import { useCentralBilling } from "src/sections/settings/BillingV2/useCentralBilling";

// Usage Summary: the platform's usage on cloud, and the AI Gateway's spend
// split between managed credits and the org's own provider keys wherever the
// gateway's credit ledger answers.
export default function UsageSummary() {
  const { isCloud, creditsAvailable, isLoading } = useCentralBilling();

  if (isLoading) {
    return null;
  }

  if (!isCloud && !creditsAvailable) {
    return <Navigate to="/dashboard/settings/profile-settings" replace />;
  }

  return (
    <>
      <Helmet>
        <title>Usage & Billing</title>
      </Helmet>
      {isCloud ? (
        <UsageSummaryV2 />
      ) : (
        <>
          <Typography variant="h5" component="h1" fontWeight={700} mb={0.5}>
            Usage Summary
          </Typography>
          <Typography variant="body2" color="text.secondary" mb={3}>
            What the AI Gateway spent, and who bills it.
          </Typography>
        </>
      )}
      {creditsAvailable && (
        <Box mt={isCloud ? 4 : 0}>
          <GatewayUsageSplit />
        </Box>
      )}
    </>
  );
}
