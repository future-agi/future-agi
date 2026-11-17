import React from "react";
import { Helmet } from "react-helmet-async";
import CapabilitiesSection from "src/sections/gateway/capabilities/CapabilitiesSection";

const GatewayCapabilities = () => (
  <>
    <Helmet>
      <title>Gateway Capabilities | Future AGI</title>
    </Helmet>
    <CapabilitiesSection />
  </>
);

export default GatewayCapabilities;
