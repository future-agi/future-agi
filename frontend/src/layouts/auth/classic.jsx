import React from "react";
import PropTypes from "prop-types";
import { Events, getPageViewEvent, trackEvent } from "src/utils/Mixpanel";
import { paths } from "src/routes/paths";
import Stack from "@mui/material/Stack";
import { useLocation } from "react-router-dom";

// ----------------------------------------------------------------------

const normalizeAuthPathname = (pathname = "") =>
  pathname.replace(/\/+$/, "") || "/";

// Onboarding is PostHog-only; setup-org must not emit a Mixpanel pageview.
const shouldTrackMixpanelPageView = (pathname) =>
  normalizeAuthPathname(pathname) !== paths.auth.jwt.setup_org;

export default function AuthClassicLayout({ children }) {
  const location = useLocation();

  React.useEffect(() => {
    if (!shouldTrackMixpanelPageView(location.pathname)) return;

    const { eventName, extras = {} } = getPageViewEvent(location.pathname) || {
      eventName: Events.pageView,
      extras: {},
    };
    trackEvent(eventName, { path: location.pathname, ...extras });
  }, [location]);

  return (
    <Stack
      component="main"
      direction="row"
      sx={{
        minHeight: "100vh",
        minWidth: { xs: 0, md: 1200 },
        background: "url('/assets/illustrations/auth-background.png')",
        backgroundRepeat: "no-repeat",
        backgroundSize: "100% 100%",
      }}
    >
      {children}
    </Stack>
  );
}

AuthClassicLayout.propTypes = {
  children: PropTypes.node,
};
