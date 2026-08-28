import React from "react";
import { Helmet } from "react-helmet-async";
import { Navigate } from "react-router";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";
import UsageSummaryV2 from "src/sections/settings/UsageSummaryV2/UsageSummaryV2";

export default function UsageSummary() {
  const { isCloud, isLoading } = useDeploymentMode();

  if (isLoading) {
    return null;
  }

  if (!isCloud) {
    return <Navigate to="/dashboard/settings/profile-settings" replace />;
  }

  return (
    <>
      <Helmet>
        <title>Usage & Billing</title>
      </Helmet>
      <UsageSummaryV2 />
    </>
  );
}
