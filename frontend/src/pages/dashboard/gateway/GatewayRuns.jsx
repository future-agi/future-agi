import React from "react";
import { Helmet } from "react-helmet-async";
import RunsSection from "src/sections/gateway/runs/RunsSection";

const GatewayRuns = () => (
  <>
    <Helmet>
      <title>Gateway Runs | Future AGI</title>
    </Helmet>
    <RunsSection />
  </>
);

export default GatewayRuns;
