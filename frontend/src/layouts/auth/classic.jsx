import React from "react";
import PropTypes from "prop-types";
import { Events, getPageViewEvent, trackEvent } from "src/utils/Mixpanel";
import Stack from "@mui/material/Stack";
import Typography from "@mui/material/Typography";
import { useLocation } from "react-router-dom";

// Static gate (HO S2 leak scan): other builds fold the note away.
const PROTOTYPE_BUILD =
  import.meta.env.VITE_ENVIRONMENT === "local" &&
  import.meta.env.VITE_PROTOTYPE_MODE === "true";

/**
 * L7c g1 (switcher:logout): what the prototype's sign-in does, on the
 * sign-in page only (sign up, reset and SSO links land there too).
 */
const PROTOTYPE_SIGN_IN_NOTE = PROTOTYPE_BUILD
  ? "Any email and any password sign in as the demo user."
  : null;
const signInNoteFor = (pathname) =>
  PROTOTYPE_SIGN_IN_NOTE && /^\/auth\/jwt\/login\/?$/.test(pathname ?? "")
    ? PROTOTYPE_SIGN_IN_NOTE
    : null;

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
      sx={{
        minHeight: "100vh",
        minWidth: 0,
        width: 1,
        flexDirection: { xs: "column", md: "row" },
        background: "url('/assets/illustrations/auth-background.png')",
        backgroundRepeat: "no-repeat",
        backgroundSize: "100% 100%",
      }}
    >
      {signInNoteFor(location.pathname) ? (
        <Typography
          typography="s2"
          data-testid="prototype-sign-in-note"
          sx={{
            position: "fixed",
            top: 36,
            left: "50%",
            transform: "translateX(-50%)",
            px: 1.5,
            py: 0.5,
            borderRadius: 1,
            bgcolor: "background.paper",
            border: "1px solid",
            borderColor: "divider",
            zIndex: 10,
          }}
        >
          {signInNoteFor(location.pathname)}
        </Typography>
      ) : null}
      {children}
    </Stack>
  );
}

AuthClassicLayout.propTypes = {
  children: PropTypes.node,
};
