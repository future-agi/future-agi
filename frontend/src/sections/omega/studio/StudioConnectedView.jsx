import React, { useMemo } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";

import { EMBED_STATUS, EmbeddedSurface } from "src/sections/omega/embed";

import {
  serverDisplayName,
  studioDirectHref,
  studioFrameSrc,
} from "./connect-link";
import StudioIcon from "./StudioIcon";
import {
  GATE_COPY,
  PAGE,
  STUDIO_BASE_PATH,
  STUDIO_HELLO_TIMEOUT_MS,
  STUDIO_LABEL,
  STUDIO_PROBE_PATH,
  TOOLBAR,
  needsKeyGateCopy,
  needsLinkReason,
} from "./studio-copy";
import { surfaceCopy } from "./surface-copy";

const WAITING = new Set([EMBED_STATUS.connecting, EMBED_STATUS.diagnosing]);

// The gate panel in Studio's words for this connection. Once Studio says it
// needs a key or refused one, Reconnect cannot help (the key was handed over
// once and is gone), so the panel's button goes to the paste field. A
// connection with no key to hand over says so from the start, and offers the
// same button while it waits.
function studioGateCopy(connection, hostOrigin, onPasteLink) {
  const pasteLink = {
    label: GATE_COPY.pasteLink,
    onClick: onPasteLink,
    testId: "studio-gate-paste-link",
  };
  return {
    ...surfaceCopy(hostOrigin),
    needsKey: needsKeyGateCopy(connection),
    waitingNote: connection.hasKey ? undefined : needsLinkReason(connection),
    action: (status) => {
      if (status === EMBED_STATUS.needsKey || status === EMBED_STATUS.refused)
        return pasteLink;
      if (connection.hasKey) return null;
      // No hello in time keeps Try again: the diagnosis names the real cause
      // (not running, blocked, permission) and ends with "try again".
      return WAITING.has(status) ? pasteLink : null;
    },
  };
}

const MONO =
  'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace';

const DOT_COLOR = {
  [EMBED_STATUS.connected]: "success.main",
  [EMBED_STATUS.connecting]: "text.disabled",
  [EMBED_STATUS.checking]: "text.disabled",
  [EMBED_STATUS.diagnosing]: "warning.main",
  [EMBED_STATUS.needsKey]: "warning.main",
  [EMBED_STATUS.refused]: "error.main",
  [EMBED_STATUS.timeout]: "error.main",
};

function StatusLine({ url, status, hasKey }) {
  // A connection with no key cannot become connected: while it waits, the bar
  // says what it needs rather than "Connecting…".
  const shown =
    !hasKey && WAITING.has(status) ? EMBED_STATUS.needsKey : status;
  return (
    <Box
      data-testid="studio-status"
      data-state={status}
      sx={{ display: "flex", alignItems: "center", gap: 1, minWidth: 0 }}
    >
      <Box
        aria-hidden
        sx={{
          width: 8,
          height: 8,
          borderRadius: "50%",
          flexShrink: 0,
          bgcolor: DOT_COLOR[shown] || "text.disabled",
        }}
      />
      <Typography
        variant="s2"
        color="text.secondary"
        noWrap
        sx={{ minWidth: 0 }}
      >
        {TOOLBAR.status[shown] || TOOLBAR.status.connecting}
        <Box component="span" sx={{ mx: 0.75, color: "text.disabled" }}>
          ·
        </Box>
        <Box
          component="span"
          data-testid="studio-server"
          sx={{ fontFamily: MONO, color: "text.primary" }}
        >
          {serverDisplayName(url)}
        </Box>
      </Typography>
    </Box>
  );
}

StatusLine.propTypes = {
  url: PropTypes.string,
  status: PropTypes.string,
  hasKey: PropTypes.bool,
};

// The framed Studio that omega dev serves, under a slim bar that says which
// server it is and offers the way out: open it on its own, or disconnect.
export default function StudioConnectedView({
  connection,
  status,
  hostOrigin,
  onStatusChange,
  onDisconnect,
  onPasteLink,
}) {
  const direct = studioDirectHref(connection.url);
  const copy = useMemo(
    () => studioGateCopy(connection, hostOrigin, onPasteLink),
    [connection, hostOrigin, onPasteLink],
  );

  const openLocal = () => window.open(direct, "_blank", "noopener,noreferrer");

  return (
    <Box
      data-testid="studio-connected"
      data-source={connection.source}
      sx={{
        flex: 1,
        minHeight: { xs: 520, md: 560 },
        display: "flex",
        flexDirection: "column",
        gap: 1.5,
      }}
    >
      <Box
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1.5,
          minWidth: 0,
          flexWrap: { xs: "wrap", sm: "nowrap" },
        }}
      >
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography
            component="h1"
            typography="m2"
            fontWeight="fontWeightMedium"
            color="text.primary"
          >
            {PAGE.title}
          </Typography>
          <StatusLine
            url={connection.url}
            status={status}
            hasKey={Boolean(connection.hasKey)}
          />
        </Box>
        <Box sx={{ display: "flex", alignItems: "center", gap: 0.5 }}>
          <Button
            size="small"
            color="inherit"
            onClick={openLocal}
            aria-label={TOOLBAR.openLocal}
            title={TOOLBAR.openLocal}
            data-testid="studio-open-local"
            endIcon={<StudioIcon name="external" size={14} />}
            sx={{
              color: "text.secondary",
              minWidth: 0,
              "& .MuiButton-endIcon": { ml: { xs: 0, sm: 1 } },
            }}
          >
            <Box
              component="span"
              sx={{ display: { xs: "none", sm: "inline" } }}
            >
              {TOOLBAR.openLocal}
            </Box>
          </Button>
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={() => onDisconnect()}
            data-testid="studio-disconnect"
            sx={{ borderColor: "divider" }}
          >
            {TOOLBAR.disconnect}
          </Button>
        </Box>
      </Box>
      <Box
        sx={{
          flex: 1,
          minHeight: 0,
          display: "flex",
          borderRadius: 1.5,
          border: "1px solid",
          borderColor: "divider",
          overflow: "hidden",
        }}
      >
        <EmbeddedSurface
          key={connection.id}
          src={studioFrameSrc(connection.url)}
          expectedOrigin={connection.origin}
          getToken={connection.getToken}
          basePath={STUDIO_BASE_PATH}
          label={STUDIO_LABEL}
          title="Omega Studio"
          probePath={STUDIO_PROBE_PATH}
          helloTimeoutMs={STUDIO_HELLO_TIMEOUT_MS}
          // The key came from a link: connected only once Studio's own server
          // has accepted it (its gate says "ready").
          requireGate
          openHref={direct}
          copy={copy}
          onStatusChange={onStatusChange}
        />
      </Box>
    </Box>
  );
}

StudioConnectedView.propTypes = {
  connection: PropTypes.shape({
    id: PropTypes.number.isRequired,
    url: PropTypes.string.isRequired,
    origin: PropTypes.string.isRequired,
    getToken: PropTypes.func.isRequired,
    source: PropTypes.string,
    hasKey: PropTypes.bool,
    label: PropTypes.string,
  }).isRequired,
  status: PropTypes.string.isRequired,
  hostOrigin: PropTypes.string.isRequired,
  onStatusChange: PropTypes.func.isRequired,
  onDisconnect: PropTypes.func.isRequired,
  onPasteLink: PropTypes.func.isRequired,
};
