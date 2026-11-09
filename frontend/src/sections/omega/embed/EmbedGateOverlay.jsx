import React from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import Stack from "@mui/material/Stack";
import Button from "@mui/material/Button";
import Typography from "@mui/material/Typography";
import CircularProgress from "@mui/material/CircularProgress";
import { alpha } from "@mui/material/styles";

import EmbedGateIcon from "./EmbedGateIcon";
import { gateCopy } from "./gate-copy";
import { EMBED_STATUS } from "./use-embed-session";

// The calm panel shown over the frame while it is not usable: connecting,
// checking a key with the child, a gate the child reported (needs a key,
// refused), or no hello in time.

const ICON = {
  [EMBED_STATUS.needsKey]: "key",
  [EMBED_STATUS.refused]: "shield",
  [EMBED_STATUS.timeout]: "unlinked",
};

function waitingText(status, label, copy) {
  if (status === EMBED_STATUS.diagnosing)
    return `Still connecting to ${label}. Checking what's wrong…`;
  if (status === EMBED_STATUS.checking)
    return copy?.checking || `Checking the key with ${label}…`;
  return copy?.connecting || `Connecting to ${label}…`;
}

export default function EmbedGateOverlay({
  status,
  diagnosis,
  label,
  copy,
  openHref,
  onRetry,
}) {
  if (status === EMBED_STATUS.connected) return null;
  const waiting =
    status === EMBED_STATUS.connecting ||
    status === EMBED_STATUS.checking ||
    status === EMBED_STATUS.diagnosing;
  const text = gateCopy(status, { label, diagnosis, copy });

  const openDirectly = () => {
    if (openHref) window.open(openHref, "_blank", "noopener,noreferrer");
  };

  return (
    <Box
      role="status"
      aria-live="polite"
      data-testid="omega-embed-gate"
      data-state={status}
      data-cause={diagnosis?.cause || undefined}
      sx={{
        position: "absolute",
        inset: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        px: 2,
        bgcolor: "background.default",
      }}
    >
      {waiting ? (
        <Stack direction="row" spacing={1.5} alignItems="center">
          <CircularProgress size={18} thickness={5} />
          <Typography variant="s1" color="text.secondary">
            {waitingText(status, label, copy)}
          </Typography>
        </Stack>
      ) : (
        <Stack
          spacing={2}
          alignItems="center"
          sx={{ maxWidth: 440, textAlign: "center" }}
        >
          <Box
            sx={{
              width: 48,
              height: 48,
              borderRadius: "50%",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              color: "primary.main",
              bgcolor: (theme) => alpha(theme.palette.primary.main, 0.08),
            }}
          >
            <EmbedGateIcon
              name={ICON[status] || ICON[EMBED_STATUS.timeout]}
              sx={{ fontSize: 24 }}
            />
          </Box>
          <Stack spacing={0.75}>
            <Typography
              variant="m3"
              fontWeight="fontWeightSemiBold"
              color="text.primary"
            >
              {text?.title}
            </Typography>
            <Typography variant="s1" color="text.secondary">
              {text?.body}
            </Typography>
          </Stack>
          <Stack
            direction="row"
            spacing={1}
            flexWrap="wrap"
            justifyContent="center"
            useFlexGap
          >
            <Button variant="contained" size="small" onClick={onRetry}>
              {status === EMBED_STATUS.timeout ? "Try again" : "Reconnect"}
            </Button>
            {openHref ? (
              <Button
                variant="outlined"
                size="small"
                color="inherit"
                onClick={openDirectly}
                endIcon={
                  <EmbedGateIcon name="external" sx={{ fontSize: 16 }} />
                }
              >
                {`Open ${label} directly`}
              </Button>
            ) : null}
          </Stack>
        </Stack>
      )}
    </Box>
  );
}

EmbedGateOverlay.propTypes = {
  status: PropTypes.oneOf(Object.values(EMBED_STATUS)).isRequired,
  diagnosis: PropTypes.shape({
    cause: PropTypes.string,
    origin: PropTypes.string,
  }),
  label: PropTypes.string.isRequired,
  copy: PropTypes.shape({
    connecting: PropTypes.string,
    checking: PropTypes.string,
    needsKey: PropTypes.shape({
      title: PropTypes.string,
      body: PropTypes.string,
    }),
    refused: PropTypes.shape({
      title: PropTypes.string,
      body: PropTypes.string,
    }),
    diagnosis: PropTypes.func,
  }),
  openHref: PropTypes.string,
  onRetry: PropTypes.func.isRequired,
};
