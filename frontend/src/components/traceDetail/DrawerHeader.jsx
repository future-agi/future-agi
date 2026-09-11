import React, { useEffect, useCallback } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "notistack";

/* ── Small bordered button (24×24) ── */

const HeaderButton = ({ icon, tooltip, onClick, disabled }) => (
  <Tooltip title={tooltip} arrow placement="bottom">
    <span style={{ display: "inline-flex" }}>
      <Box
        component="button"
        onClick={onClick}
        disabled={disabled}
        sx={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: 24,
          height: 24,
          p: 0,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "4px",
          bgcolor: "background.paper",
          cursor: disabled ? "default" : "pointer",
          opacity: disabled ? 0.4 : 1,
          flexShrink: 0,
          "&:hover:not(:disabled)": {
            bgcolor: "action.hover",
            borderColor: "text.disabled",
          },
          transition: "all 120ms",
        }}
      >
        <Iconify icon={icon} width={16} sx={{ color: "text.primary" }} />
      </Box>
    </span>
  </Tooltip>
);

HeaderButton.propTypes = {
  icon: PropTypes.string.isRequired,
  tooltip: PropTypes.string.isRequired,
  onClick: PropTypes.func,
  disabled: PropTypes.bool,
};

/* ── Nav arrow button (24×24 with 2px radius) ── */

const NavButton = ({ icon, tooltip, onClick, disabled }) => (
  <Tooltip title={tooltip} arrow placement="bottom">
    <span style={{ display: "inline-flex" }}>
      <Box
        component="button"
        onClick={onClick}
        disabled={disabled}
        sx={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: 24,
          height: 24,
          p: 0,
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "2px",
          bgcolor: "background.paper",
          cursor: disabled ? "default" : "pointer",
          opacity: disabled ? 0.4 : 1,
          flexShrink: 0,
          "&:hover:not(:disabled)": {
            bgcolor: "action.hover",
            borderColor: "text.disabled",
          },
          transition: "all 120ms",
        }}
      >
        <Iconify icon={icon} width={20} sx={{ color: "text.primary" }} />
      </Box>
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
              fontSize: 14,
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
            <Box
              component="button"
              onClick={handleCopy}
              sx={{
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                p: 0,
                border: "none",
                bgcolor: "transparent",
                cursor: "pointer",
                flexShrink: 0,
                "&:hover": { opacity: 0.7 },
              }}
            >
              <Iconify
                icon="mdi:content-copy"
                width={12}
                sx={{ color: "text.secondary" }}
              />
            </Box>
          </Tooltip>
        </Stack>

        {/* Nav arrows */}
        <Stack direction="row" spacing={0.5}>
          <NavButton
            icon="mdi:chevron-up"
            tooltip="Previous trace (K)"
            onClick={onPrev}
            disabled={!hasPrev}
          />
          <NavButton
            icon="mdi:chevron-down"
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
              icon={isFullscreen ? "lucide:minimize" : "lucide:fullscreen"}
              tooltip={isFullscreen ? "Exit fullscreen" : "Fullscreen"}
              onClick={onFullscreen}
            />
          )}
          {onOpenNewTab && (
            <HeaderButton
              icon="iconoir:open-new-window"
              tooltip="Open in new tab"
              onClick={onOpenNewTab}
            />
          )}
          <HeaderButton
            icon="mdi:download-outline"
            tooltip="Download raw data"
            onClick={onDownload}
          />
          <HeaderButton
            icon="basil:share-outline"
            tooltip="Share trace"
            onClick={onShare}
          />
        </Stack>

        {/* Close button */}
        <Tooltip title="Close (Esc)" arrow placement="bottom">
          <Box
            component="button"
            onClick={onClose}
            sx={{
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              p: 0,
              border: "none",
              bgcolor: "transparent",
              cursor: "pointer",
              flexShrink: 0,
              "&:hover": { opacity: 0.6 },
            }}
          >
            <Iconify
              icon="mdi:close"
              width={20}
              sx={{ color: "text.primary" }}
            />
          </Box>
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
