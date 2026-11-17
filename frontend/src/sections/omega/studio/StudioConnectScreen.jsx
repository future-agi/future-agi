import React, { useCallback, useEffect, useId, useState } from "react";
import PropTypes from "prop-types";
import Alert from "@mui/material/Alert";
import AlertTitle from "@mui/material/AlertTitle";
import Box from "@mui/material/Box";
import Typography from "@mui/material/Typography";
import { alpha } from "@mui/material/styles";

import { onOmegaConnect } from "src/sections/omega/embed";

import ConnectForm from "./ConnectForm";
import SavedConnections from "./SavedConnections";
import StartOmegaDev from "./StartOmegaDev";
import StudioIcon from "./StudioIcon";
import {
  SAVED_CONNECTIONS_EVENT,
  SAVED_CONNECTIONS_KEY,
  forgetConnection,
  readSavedConnections,
  renameConnection,
} from "./saved-connections";
import { CONNECT, noticeCopy } from "./studio-copy";

function useSavedConnections() {
  const [items, setItems] = useState(readSavedConnections);
  useEffect(() => {
    const refresh = () => setItems(readSavedConnections());
    const onStorage = (event) => {
      if (event.key === null || event.key === SAVED_CONNECTIONS_KEY) refresh();
    };
    window.addEventListener(SAVED_CONNECTIONS_EVENT, refresh);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener(SAVED_CONNECTIONS_EVENT, refresh);
      window.removeEventListener("storage", onStorage);
    };
  }, []);
  return items;
}

// The first script in index.html marks an "Open in Future AGI" link that
// arrived without a usable key (cut off where the terminal wrapped it, or
// copied without its "&t=" part). The mark is that fact alone: no key, no
// address. A link opened with the page already showing is marked only while
// its omega:connect event runs.
const LINK_WITHOUT_KEY_MARK = "__omegaConnectKeyless";

const linkLostItsKey = () => {
  try {
    return window[LINK_WITHOUT_KEY_MARK] === true;
  } catch {
    return false;
  }
};

const forgetLinkWithoutKey = () => {
  try {
    delete window[LINK_WITHOUT_KEY_MARK];
  } catch {
    /* nothing to forget */
  }
};

const LINK_WITHOUT_KEY_KIND = "link-without-key";
const LINK_WITHOUT_KEY_COPY = {
  severity: "warning",
  title: "That link had no key",
  body: "It may have been cut off when it was copied. Copy the whole “Open in Future AGI” line omega dev printed and open it again, or paste it below.",
};

// A fresh object for each link that arrives without its key, so the field
// takes focus again even when the notice already shows; null once a link
// with a key arrives. The mark is read without spending it while rendering
// (StrictMode renders twice) and forgotten once the screen has mounted.
function useLinkWithoutKey() {
  const [arrival, setArrival] = useState(() =>
    linkLostItsKey() ? { kind: LINK_WITHOUT_KEY_KIND } : null,
  );
  useEffect(() => {
    forgetLinkWithoutKey();
    return onOmegaConnect(() => {
      setArrival(linkLostItsKey() ? { kind: LINK_WITHOUT_KEY_KIND } : null);
    });
  }, []);
  return arrival;
}

// Shown when no Studio is connected: paste the link, pick a saved server, or
// learn how to start omega dev for this site.
export default function StudioConnectScreen({
  origin,
  notice,
  onConnectPasted,
  onConnectSaved,
}) {
  const items = useSavedConnections();
  const linkWithoutKey = useLinkWithoutKey();
  // The newest reason wins: a link without its key is newer than any notice
  // the screen opened with.
  const shown = linkWithoutKey || notice;
  const message = linkWithoutKey ? LINK_WITHOUT_KEY_COPY : noticeCopy(notice);
  const noticeId = useId();
  const rename = useCallback((url, label) => renameConnection(url, label), []);
  const remove = useCallback((url) => forgetConnection(url), []);

  return (
    <Box
      data-testid="studio-connect"
      sx={{
        flex: 1,
        display: "flex",
        justifyContent: "center",
        px: { xs: 0, sm: 2 },
        pt: { xs: 1, sm: 4 },
        pb: 6,
      }}
    >
      <Box
        sx={{
          width: "100%",
          maxWidth: 600,
          display: "flex",
          flexDirection: "column",
          gap: 4,
        }}
      >
        <Box
          component="section"
          sx={{
            p: { xs: 2, sm: 3 },
            borderRadius: 1.5,
            border: "1px solid",
            borderColor: "divider",
            bgcolor: "background.paper",
          }}
        >
          <Box sx={{ display: "flex", alignItems: "center", gap: 1.5, mb: 2 }}>
            <Box
              sx={{
                width: 36,
                height: 36,
                borderRadius: 1,
                flexShrink: 0,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                color: "primary.main",
                bgcolor: (theme) => alpha(theme.palette.primary.main, 0.08),
              }}
            >
              <StudioIcon name="plug" size={18} />
            </Box>
            <Box sx={{ minWidth: 0 }}>
              <Typography
                component="h2"
                variant="m3"
                fontWeight="fontWeightMedium"
                color="text.primary"
              >
                {CONNECT.title}
              </Typography>
              <Typography variant="s1" color="text.secondary">
                {CONNECT.body}
              </Typography>
            </Box>
          </Box>
          {message && (
            <Alert
              severity={message.severity}
              variant="outlined"
              id={noticeId}
              data-testid="studio-connect-notice"
              data-kind={shown.kind}
              sx={{ mb: 2, typography: "s1" }}
            >
              <AlertTitle sx={{ typography: "s1", fontWeight: 500, mb: 0.25 }}>
                {message.title}
              </AlertTitle>
              {message.body}
            </Alert>
          )}
          <ConnectForm
            onConnect={onConnectPasted}
            describedBy={message ? noticeId : undefined}
            focusKey={shown}
          />
        </Box>

        <SavedConnections
          items={items}
          onConnect={onConnectSaved}
          onRename={rename}
          onRemove={remove}
        />

        <StartOmegaDev origin={origin} />
      </Box>
    </Box>
  );
}

StudioConnectScreen.propTypes = {
  origin: PropTypes.string.isRequired,
  notice: PropTypes.shape({
    kind: PropTypes.string,
    url: PropTypes.string,
  }),
  onConnectPasted: PropTypes.func.isRequired,
  onConnectSaved: PropTypes.func.isRequired,
};
