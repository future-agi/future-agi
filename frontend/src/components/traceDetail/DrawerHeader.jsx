import React, { useEffect, useCallback } from "react";
import PropTypes from "prop-types";
import { IconButton, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "notistack";

/* ── Icon button ── */

const HeaderButton = ({ icon, tooltip, onClick, disabled }) => (
  <Tooltip title={tooltip} arrow placement="bottom">
    <span style={{ display: "inline-flex" }}>
      <IconButton
        size="small"
        aria-label={tooltip}
        onClick={onClick}
        disabled={disabled}
      >
        <Iconify icon={icon} width={16} sx={{ color: "text.primary" }} />
      </IconButton>
    </span>
  </Tooltip>
);

HeaderButton.propTypes = {
  icon: PropTypes.string.isRequired,
  tooltip: PropTypes.string.isRequired,
  onClick: PropTypes.func,
  disabled: PropTypes.bool,
};

/* ── Nav arrow button ── */

const NavButton = ({ icon, tooltip, onClick, disabled }) => (
  <Tooltip title={tooltip} arrow placement="bottom">
    <span style={{ display: "inline-flex" }}>
      <IconButton
        size="small"
        aria-label={tooltip}
        onClick={onClick}
        disabled={disabled}
      >
        <Iconify icon={icon} width={20} sx={{ color: "text.primary" }} />
      </IconButton>
    </span>
  </Tooltip>
);

NavButton.propTypes = {
  icon: PropTypes.string.isRequired,
  tooltip: PropTypes.string.isRequired,
  onClick: PropTypes.func,
  disabled: PropTypes.bool,
};

/* ── DrawerHeader ─────────────────────────────────────── */

const DrawerHeader = ({
  traceId,
  projectId,
  onClose,
  onPrev,
  onNext,
  hasPrev = true,
  hasNext = true,
  onFullscreen,
  isFullscreen = false,
  onOpenNewTab,
  onDownload,
  onShare,
}) => {
  // Keyboard shortcuts: J = next, K = prev
  const handleKeyDown = useCallback(
    (e) => {
      // Don't trigger if user is typing in an input/textarea
      if (
        e.target.tagName === "INPUT" ||
        e.target.tagName === "TEXTAREA" ||
        e.target.isContentEditable
      )
        return;

      if ((e.key === "j" || e.key === "J") && hasNext) {
        e.preventDefault();
        onNext?.();
      } else if ((e.key === "k" || e.key === "K") && hasPrev) {
        e.preventDefault();
        onPrev?.();
      }
    },
    [onPrev, onNext, hasPrev, hasNext],
  );

  useEffect(() => {
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [handleKeyDown]);

  const handleCopy = () => {
    if (!traceId) return;
    navigator.clipboard.writeText(traceId).then(() => {
      enqueueSnackbar("Trace ID copied", {
        variant: "info",
        autoHideDuration: 1500,
      });
    });
  };

  return (
    <Stack
      direction="row"
      alignItems="center"
      justifyContent="space-between"
      sx={{
        px: 2,
        py: 1.5,
        bgcolor: "background.default",
        borderBottom: "1px solid",
        borderColor: "divider",
        flexShrink: 0,
        minHeight: 48,
      }}
    >
      {/* ── Left: Trace ID + Copy + Nav arrows ── */}
      <Stack direction="row" alignItems="center" spacing={1.5}>
        {/* Trace ID */}
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.25}
          sx={{ maxWidth: 280 }}
        >
          <Typography
            sx={{
              fontSize: 13,
              fontWeight: 500,
              fontFamily: "'IBM Plex Sans', sans-serif",
              color: "text.primary",
              whiteSpace: "nowrap",
            }}
          >
            Trace ID :
          </Typography>
          <Typography
            noWrap
            sx={{
              fontSize: 13,
              fontFamily: "'IBM Plex Sans', sans-serif",
              color: "text.primary",
              whiteSpace: "nowrap",
            }}
          >
            {traceId || "-"}
          </Typography>
          <Tooltip title="Copy Trace ID" arrow placement="bottom">
            <IconButton
              size="small"
              aria-label="Copy Trace ID"
              onClick={handleCopy}
            >
              <Iconify
                icon="solar:copy-linear"
                width={12}
                sx={{ color: "text.secondary" }}
              />
            </IconButton>
          </Tooltip>
        </Stack>

        {/* Nav arrows */}
        <Stack direction="row" spacing={0.5}>
          <NavButton
            icon="solar:alt-arrow-up-linear"
            tooltip="Previous trace (K)"
            onClick={onPrev}
            disabled={!hasPrev}
          />
          <NavButton
            icon="solar:alt-arrow-down-linear"
            tooltip="Next trace (J)"
            onClick={onNext}
            disabled={!hasNext}
          />
        </Stack>
      </Stack>

      {/* ── Right: Action buttons + Close ── */}
      <Stack direction="row" alignItems="center" spacing={1.5}>
        {/* Action buttons group */}
        <Stack direction="row" spacing={0.5}>
          {onFullscreen && (
            <HeaderButton
              icon={
                isFullscreen
                  ? "solar:quit-full-screen-linear"
                  : "solar:full-screen-linear"
              }
              tooltip={isFullscreen ? "Exit fullscreen" : "Fullscreen"}
              onClick={onFullscreen}
            />
          )}
          {onOpenNewTab && (
            <HeaderButton
              icon="solar:arrow-right-up-linear"
              tooltip="Open in new tab"
              onClick={onOpenNewTab}
            />
          )}
          <HeaderButton
            icon="solar:download-minimalistic-linear"
            tooltip="Download raw data"
            onClick={onDownload}
          />
          <HeaderButton
            icon="solar:share-linear"
            tooltip="Share trace"
            onClick={onShare}
          />
        </Stack>

        {/* Close button */}
        <Tooltip title="Close (Esc)" arrow placement="bottom">
          <IconButton size="small" aria-label="Close" onClick={onClose}>
            <Iconify
              icon="solar:close-linear"
              width={20}
              sx={{ color: "text.primary" }}
            />
          </IconButton>
        </Tooltip>
      </Stack>
    </Stack>
  );
};

DrawerHeader.propTypes = {
  traceId: PropTypes.string,
  projectId: PropTypes.string,
  onClose: PropTypes.func.isRequired,
  onPrev: PropTypes.func,
  onNext: PropTypes.func,
  hasPrev: PropTypes.bool,
  hasNext: PropTypes.bool,
  onFullscreen: PropTypes.func,
  isFullscreen: PropTypes.bool,
  onOpenNewTab: PropTypes.func,
  onDownload: PropTypes.func,
  onShare: PropTypes.func,
};

export default React.memo(DrawerHeader);
