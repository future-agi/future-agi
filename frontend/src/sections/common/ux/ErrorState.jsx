import React, { useEffect, useRef } from "react";
import PropTypes from "prop-types";
import { Box, Button, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { KeyCap } from "src/sections/common/RowPanel/ShortcutSheet";
import { shortcutAllowed } from "./useShortcuts";
import CopyButton from "./CopyButton";
import { isLaunchBuild, isSurfaceShown } from "src/launch/launchSwitch";

const reasonOf = (error) => {
  if (!error) return null;
  if (typeof error === "string") return error;
  const r = error.response;
  const status = r?.status ?? error.status;
  const msg =
    (typeof r?.data?.result === "string" && r.data.result) ||
    r?.data?.detail ||
    r?.data?.message ||
    error.message;
  if (status === 0 || /network/i.test(String(msg)))
    return "The server didn't answer. Check your connection.";
  return status ? `${status} · ${msg ?? "Request failed"}` : msg ?? null;
};

/**
 * ErrorState — `Couldn't load <thing>.` + the reason + `Retry (R)` +
 * `Copy request id`. `R` retries while it is on screen (not while typing).
 *
 * Props (frozen):
 * @param {string} [props.title]  default `Couldn't load ${thing}.`
 * @param {string} [props.thing="this view"]
 * @param {Error|object|string} [props.error]  axios error, Error or text
 * @param {Function} [props.onRetry]
 * @param {string} [props.requestId]  default: the error's `x-request-id`
 * @param {boolean} [props.compact]
 * @param {object} [props.sx]
 */
export default function ErrorState({
  title,
  thing = "this view",
  error,
  onRetry,
  requestId,
  compact = false,
  sx,
}) {
  const ref = useRef(onRetry);
  ref.current = onRetry;
  useEffect(() => {
    if (!onRetry) return undefined;
    const onKey = (e) => {
      if (e.key !== "r" && e.key !== "R") return;
      if (!shortcutAllowed(e, document.activeElement)) return;
      e.preventDefault();
      ref.current?.();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onRetry]);
  // L6 launch switch (state:errors): Copy request id shows only for an id
  // the browser can really read: a caller's, or the x-request-id header
  // (readable cross-origin only when the API exposes it). A launch build
  // never shows an id from anywhere else.
  const headers = error?.responseHeaders ?? error?.response?.headers;
  const rid =
    requestId ??
    headers?.["x-request-id"] ??
    (isLaunchBuild() ? null : error?.requestId) ??
    null;
  const reason = reasonOf(error);
  return (
    <Stack
      alignItems="center"
      textAlign="center"
      gap={1}
      role="alert"
      data-testid="error-state"
      sx={{ py: compact ? 3 : 6, px: 2, maxWidth: 520, mx: "auto", ...sx }}
    >
      <Box sx={{ color: "red.600" }}>
        <Iconify icon="mdi:alert-circle-outline" width={28} />
      </Box>
      <Typography
        typography="s1"
        fontWeight="fontWeightSemiBold"
        color="text.primary"
      >
        {title ?? `Couldn't load ${thing}.`}
      </Typography>
      {reason && (
        <Typography
          typography="s2"
          color="text.secondary"
          sx={{ wordBreak: "break-word" }}
        >
          {reason}
        </Typography>
      )}
      <Stack direction="row" alignItems="center" gap={1} sx={{ mt: 0.5 }}>
        {onRetry && (
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={onRetry}
            startIcon={<Iconify icon="mdi:refresh" width={16} />}
            endIcon={
              <KeyCap sx={{ height: 18, minWidth: 18, typography: "s3" }}>
                R
              </KeyCap>
            }
          >
            Retry
          </Button>
        )}
        {isSurfaceShown("errors.requestId") && rid && (
          <Stack direction="row" alignItems="center" gap={0.25}>
            <Typography typography="s3" color="text.secondary">
              Request id
            </Typography>
            <CopyButton value={rid} tooltip="Copy request id" />
          </Stack>
        )}
      </Stack>
    </Stack>
  );
}

ErrorState.propTypes = {
  title: PropTypes.string,
  thing: PropTypes.string,
  error: PropTypes.oneOfType([PropTypes.object, PropTypes.string]),
  onRetry: PropTypes.func,
  requestId: PropTypes.string,
  compact: PropTypes.bool,
  sx: PropTypes.object,
};
