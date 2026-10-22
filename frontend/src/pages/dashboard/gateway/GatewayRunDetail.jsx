import React from "react";
import { Helmet } from "react-helmet-async";
import RunDetailSection from "src/sections/gateway/runs/RunDetailSection";

const GatewayRunDetail = () => (
  <>
    <Helmet>
      <title>Gateway Run | Future AGI</title>
    </Helmet>
    <RunDetailSection />
  </>
);

export default GatewayRunDetail;
