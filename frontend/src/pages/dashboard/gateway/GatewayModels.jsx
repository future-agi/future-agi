import React from "react";
import { Helmet } from "react-helmet-async";
import ModelsSection from "src/sections/gateway/models/ModelsSection";

const GatewayModels = () => (
  <>
    <Helmet>
      <title>Gateway Models | Future AGI</title>
    </Helmet>
    <ModelsSection />
  </>
);

export default GatewayModels;
