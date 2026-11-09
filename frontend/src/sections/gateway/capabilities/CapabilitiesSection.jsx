import React from "react";
import { Navigate, useLocation } from "react-router-dom";

import { paths } from "src/routes/paths";

import { hiddenPageRedirect } from "../shell/page-status";

// The capability matrix has no backend (agentcc/urls_capabilities.py is
// empty), so the page is hidden (shell/page-status.js) and this section only
// forwards to the page the hide names, keeping the query and the hash. It
// never renders a placeholder, whichever route mounts it.
const CapabilitiesSection = () => {
  const location = useLocation();
  const to =
    hiddenPageRedirect("capabilities") || paths.dashboard.gateway.models;
  return (
    <Navigate
      replace
      to={{ pathname: to, search: location.search, hash: location.hash }}
    />
  );
};

export default CapabilitiesSection;
