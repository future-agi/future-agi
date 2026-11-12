import React from "react";
import { Helmet } from "react-helmet-async";
import { Navigate } from "react-router";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import LoadFailedState from "src/components/load-failed-state";
import UsageSummaryV2 from "src/sections/settings/UsageSummaryV2/UsageSummaryV2";
import GatewayUsageSplit from "src/sections/settings/UsageSummaryV2/GatewayUsageSplit";
import { useCentralBilling } from "src/sections/settings/BillingV2/useCentralBilling";
import { useOrganizationSettingsLanding } from "../organization-settings-landing";

// Usage Summary: the platform's usage on cloud, and the AI Gateway's spend
// split between managed credits and the org's own provider keys wherever the
// gateway's credit ledger answers.
export default function UsageSummary() {
  const {
    isCloud,
    creditsAvailable,
    creditsError,
    retryCredits,
    creditsRetrying,
    isLoading,
  } = useCentralBilling();
  const organizationPage = useOrganizationSettingsLanding();

  if (isLoading) {
    return null;
  }

  // Where Usage Summary is not offered (off cloud, no gateway credits), the
  // workspace menu's "Organization settings" still leads here for roles other
  // than organization Owner and Admin. Land on the organization page this
  // person's role opens (Org Settings for an Owner or Admin), and on their
  // Profile only when no organization page admits them. A failed ledger read
  // stays here: the Settings menu lists Usage Summary for it, with Retry.
  if (!isCloud && !creditsAvailable && !creditsError) {
    return (
      <Navigate
        to={organizationPage ?? "/dashboard/settings/profile-settings"}
        replace
      />
    );
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
            Usage summary
          </Typography>
          <Typography variant="body2" color="text.secondary" mb={3}>
            What the AI Gateway spent, and who bills it.
          </Typography>
          {creditsError && (
            <LoadFailedState
              title="AI Gateway usage couldn't be loaded"
              detail="The gateway's spend is not shown until the credit summary loads."
              onRetry={retryCredits}
              retrying={creditsRetrying}
            />
          )}
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
