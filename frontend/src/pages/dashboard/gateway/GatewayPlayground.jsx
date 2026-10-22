import React from "react";
import { Helmet } from "react-helmet-async";
import PlaygroundSection from "src/sections/gateway/playground/PlaygroundSection";

const GatewayPlayground = () => (
  <>
    <Helmet>
      <title>Gateway Playground | Future AGI</title>
    </Helmet>
    <PlaygroundSection />
  </>
);

export default GatewayPlayground;
