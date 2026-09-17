import React from "react";
import PropTypes from "prop-types";
import { Events, getPageViewEvent, trackEvent } from "src/utils/Mixpanel";
import Stack from "@mui/material/Stack";
import { useLocation } from "react-router-dom";

// ----------------------------------------------------------------------

export default function AuthClassicLayout({ children }) {
  const location = useLocation();

  React.useEffect(() => {
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
        // Only pin the wide desktop min-width on large screens. On phones this
        // hard-locked every auth page to 1200px, forcing horizontal scroll.
        minWidth: { xs: "auto", lg: 1200 },
        overflowX: "hidden",
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
