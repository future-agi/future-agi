import React from "react";
import { Helmet } from "react-helmet-async";
import BillingSection from "src/sections/gateway/billing/BillingSection";

const GatewayBilling = () => (
  <>
    <Helmet>
      <title>Gateway Billing & Credits | Future AGI</title>
    </Helmet>
    <BillingSection />
  </>
);

export default GatewayBilling;
