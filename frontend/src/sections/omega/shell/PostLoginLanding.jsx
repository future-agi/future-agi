import React, { useEffect } from "react";
import { useNavigate } from "react-router-dom";

import { LoadingScreen } from "src/components/loading-screen";
import { useDeploymentMode } from "src/hooks/useDeploymentMode";

import { landingTarget } from "./post-login-landing";
import { OMEGA_PRODUCT } from "./rollout";
import { useOmegaProductStatus } from "./useOmegaProducts";

// The sign-in landing route (post-login-landing.js): the host's loading screen
// until the workspace's capabilities answer, then Console Chat for a workspace
// admitted to the Console, else Future AGI's own landing page.
export default function PostLoginLanding() {
  const navigate = useNavigate();
  const { isOSS, isLoading: modeLoading } = useDeploymentMode();
  const status = useOmegaProductStatus(OMEGA_PRODUCT.CONSOLE);
  const target = modeLoading ? null : landingTarget(status, isOSS);
  useEffect(() => {
    if (target === null) return;
    // The landing is done: the route it moves to has its own AuthGuard, whose
    // first-render redirect must not send this person back here.
    try {
      localStorage.setItem("initial-render", "done");
      localStorage.removeItem("redirectUrl");
    } catch {
      /* storage unavailable: the guard's redirect lands here again, once */
    }
    navigate(target, { replace: true });
  }, [navigate, target]);
  return <LoadingScreen sx={undefined} />;
}
