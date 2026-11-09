import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import MenuItem from "@mui/material/MenuItem";
import Typography from "@mui/material/Typography";

import { badgeText, inboxLabel } from "./console-inbox";
import {
  NOTIFY_STATE,
  disableInboxNotifications,
  enableInboxNotifications,
} from "./console-notify";

// The Console's parts of the product switcher (CP P10, P15): the Inbox count
// on the trigger and on the "Omega Console" entry, and the opt-in for
// notifications. Every count has a text label for assistive technology.

/** A small count beside the "Omega Console" entry. */
export function InboxCount({ inbox }) {
  const label = inboxLabel(inbox);
  if (!label) return null;
  return (
    <Box
      component="span"
      aria-label={label}
      data-testid="omega-console-inbox-count"
      sx={{
        minWidth: 20,
        height: 20,
        px: "6px",
        borderRadius: "10px",
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: 12,
        fontWeight: 600,
        lineHeight: "20px",
        bgcolor: "primary.main",
        color: "primary.contrastText",
        flexShrink: 0,
      }}
    >
      {badgeText(inbox)}
    </Box>
  );
}

InboxCount.propTypes = { inbox: PropTypes.number };

/**
 * A dot on the switcher's mark while the Console has approvals waiting and
 * the user is elsewhere in Future AGI. The trigger's own label names it.
 */
export function TriggerDot({ inbox }) {
  if (!inboxLabel(inbox)) return null;
  return (
    <Box
      component="span"
      aria-hidden
      data-testid="omega-console-inbox-dot"
      sx={(theme) => ({
        position: "absolute",
        top: 2,
        right: 2,
        width: 8,
        height: 8,
        borderRadius: "50%",
        bgcolor: "primary.main",
        boxShadow: `0 0 0 2px ${theme.palette.background.paper}`,
      })}
    />
  );
}

TriggerDot.propTypes = { inbox: PropTypes.number };

const NOTIFY_COPY = {
  [NOTIFY_STATE.OFF]: {
    title: "Notify me about approvals",
    detail: "A browser notification when something waits for you",
  },
  [NOTIFY_STATE.ON]: {
    title: "Approval notifications are on",
    detail: "Select to turn them off",
  },
  [NOTIFY_STATE.BLOCKED]: {
    title: "Notifications are blocked",
    detail: "Allow them for this site in your browser settings",
  },
};

/** The opt-in. Turning it on asks the browser for permission from this click. */
export function NotifyMenuItem({ state, onDone }) {
  const copy = NOTIFY_COPY[state];
  if (!copy) return null;
  const blocked = state === NOTIFY_STATE.BLOCKED;
  const choose = async () => {
    if (state === NOTIFY_STATE.ON) disableInboxNotifications();
    else await enableInboxNotifications();
    onDone?.();
  };
  return (
    <MenuItem
      disabled={blocked}
      onClick={choose}
      data-testid="omega-console-notify"
      data-state={state}
      role="menuitemcheckbox"
      aria-checked={state === NOTIFY_STATE.ON ? "true" : "false"}
      sx={{ mx: 0.5, px: 1, py: 0.75, borderRadius: "6px" }}
    >
      <Box sx={{ minWidth: 0 }}>
        <Typography typography="s2_1" fontWeight={500} color="text.primary">
          {copy.title}
        </Typography>
        <Typography
          typography="s2"
          color="text.secondary"
          sx={{ whiteSpace: "normal" }}
        >
          {copy.detail}
        </Typography>
      </Box>
    </MenuItem>
  );
}

NotifyMenuItem.propTypes = {
  state: PropTypes.oneOf(Object.values(NOTIFY_STATE)).isRequired,
  onDone: PropTypes.func,
};
