import React from "react";
import { Helmet } from "react-helmet-async";
import PlaygroundHandoff from "src/sections/gateway/playground/PlaygroundHandoff";

// The gateway has no playground of its own: this URL opens the Prompts
// workbench playground with the gateway model preset.
const GatewayPlayground = () => (
  <>
    <Helmet>
      <title>Open in Prompts | Future AGI</title>
    </Helmet>
    <PlaygroundHandoff />
  </>
);

export default GatewayPlayground;
