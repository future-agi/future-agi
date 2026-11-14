import React from "react";
import PropTypes from "prop-types";
import {
  Events,
  getPageViewEvent,
  pageViewPath,
  trackEvent,
} from "src/utils/Mixpanel";
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
    trackEvent(eventName, { path: pageViewPath(location.pathname), ...extras });
  }, [location]);

  return (
    <Stack
      component="main"
      direction="row"
      sx={{
        minHeight: "100vh",
        minWidth: 1200,
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
