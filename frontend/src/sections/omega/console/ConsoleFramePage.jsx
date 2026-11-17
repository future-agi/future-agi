import React, { useCallback, useMemo, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import { Helmet } from "react-helmet-async";
import { Navigate, useLocation } from "react-router-dom";

import {
  CONSOLE_ACCESS,
  forgetViewerRefusal,
  rememberViewerRefusal,
  useConsoleAccess,
} from "src/sections/omega/shell/omega-access";

import ConsoleFrame from "./ConsoleFrame";
import ConsoleViewerPanel from "./ConsoleViewerPanel";
import { consoleSrc } from "./console-config";
import { CONSOLE_LABEL } from "./console-copy";
import { consoleLandingRedirect, landingFlags } from "./console-landing";
import { createAssertionMinter } from "./session-assertion";

// /dashboard/console/*: the Omega Console (the private EE build) in a frame,
// or, for a viewer, a calm panel and no frame at all (CP P10).
//
// Before the frame is built, a Hub handoff (?hub=) or a remix link (/remix/)
// is checked and the URL corrected (console-landing.js), so the Console only
// ever receives a route that passed.

const defaultMinter = createAssertionMinter();

export default function ConsoleFramePage({ src, mintAssertion }) {
  const location = useLocation();
  const frameSrc = useMemo(() => src || consoleSrc(), [src]);
  const redirect = useMemo(
    () =>
      consoleLandingRedirect(
        location.pathname,
        location.search,
        landingFlags(),
      ),
    [location.pathname, location.search],
  );
  const { access, workspaceId } = useConsoleAccess();
  // "Check again" admitted this user, so a stale levels-based "viewer" is
  // ignored for as long as this page stays open.
  const [admitted, setAdmitted] = useState(false);

  const onViewerRefused = useCallback(() => {
    rememberViewerRefusal(workspaceId);
    setAdmitted(false);
  }, [workspaceId]);

  const checkAccess = useCallback(
    () => (mintAssertion || defaultMinter)({ count: 1 }),
    [mintAssertion],
  );
  const onAllowed = useCallback(() => {
    forgetViewerRefusal(workspaceId);
    setAdmitted(true);
  }, [workspaceId]);

  if (redirect) return <Navigate to={redirect} replace />;

  if (access === CONSOLE_ACCESS.VIEWER && !admitted) {
    return (
      <Box
        data-testid="omega-tab-console"
        data-state="viewer"
        sx={{ flex: "1 1 auto", display: "flex", minHeight: 0 }}
      >
        <Helmet>
          <title>{CONSOLE_LABEL}</title>
        </Helmet>
        <ConsoleViewerPanel checkAccess={checkAccess} onAllowed={onAllowed} />
      </Box>
    );
  }

  return (
    <ConsoleFrame
      src={frameSrc}
      mintAssertion={mintAssertion}
      onViewerRefused={onViewerRefused}
    />
  );
}

ConsoleFramePage.propTypes = {
  /** Overrides the Console URL (defaults to VITE_OMEGA_CONSOLE_URL or /omega/console/?embed=1). */
  src: PropTypes.string,
  /** Overrides how keys are minted (tests). */
  mintAssertion: PropTypes.func,
};
