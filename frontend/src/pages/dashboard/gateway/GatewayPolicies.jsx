import React from "react";
import { Helmet } from "react-helmet-async";
import PoliciesSection from "src/sections/gateway/policy/PoliciesSection";

const GatewayPolicies = () => (
  <>
    <Helmet>
      <title>Gateway Policies | Future AGI</title>
    </Helmet>
    <PoliciesSection />
  </>
);

export default GatewayPolicies;
