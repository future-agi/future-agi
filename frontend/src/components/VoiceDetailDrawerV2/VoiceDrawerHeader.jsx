import React, { useEffect, useCallback } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "notistack";

const HeaderButton = ({ icon, tooltip, onClick, disabled }) => (
  <Tooltip title={tooltip} arrow placement="bottom">
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
  </Tooltip>
);

HeaderButton.propTypes = {
  icon: PropTypes.string.isRequired,
  tooltip: PropTypes.string.isRequired,
  onClick: PropTypes.func,
  disabled: PropTypes.bool,
};

const NavButton = ({ icon, tooltip, onClick, disabled }) => (
  <Tooltip title={tooltip} arrow placement="bottom">
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
      }}
    >
      <Iconify icon={icon} width={20} sx={{ color: "text.primary" }} />
    </Box>
  </Tooltip>
);

NavButton.propTypes = {
  icon: PropTypes.string.isRequired,
  tooltip: PropTypes.string.isRequired,
  onClick: PropTypes.func,
  disabled: PropTypes.bool,
};

const VoiceDrawerHeader = ({
  callId,
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
  // Customizes the ID row label (e.g. "Call ID" for voice, "Chat ID"
  // for chat). Defaults to "Call ID" so existing callers don't change.
  idLabel = "Call ID",
  copyTooltip = "Copy Call ID",
  copyToastMessage = "Call ID copied",
}) => {
  // Keyboard: ArrowUp = previous call, ArrowDown = next call.
  // Arrows (not j/k) because TranscriptView owns j/k for turn-level nav inside a call.
  const handleKeyDown = useCallback(
    (e) => {
      if (
        e.target.tagName === "INPUT" ||
        e.target.tagName === "TEXTAREA" ||
        e.target.isContentEditable
      )
        return;

      if (e.key === "ArrowDown" && hasNext) {
        e.preventDefault();
        onNext?.();
      } else if (e.key === "ArrowUp" && hasPrev) {
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
    if (!callId) return;
    navigator.clipboard.writeText(callId).then(() => {
      enqueueSnackbar(copyToastMessage, {
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
        px: 1.5,
        py: 1,
        bgcolor: "background.default",
        borderBottom: "1px solid",
        borderColor: "divider",
        flexShrink: 0,
        minHeight: 40,
        gap: 1.5,
      }}
    >
      {/* Left: ID + metadata + nav */}
      <Stack
        direction="row"
        alignItems="center"
        spacing={1.5}
        sx={{ minWidth: 0, flex: 1 }}
      >
        <Stack
          direction="row"
          alignItems="center"
          spacing={0.25}
          sx={{ minWidth: 0, maxWidth: 320 }}
        >
          <Typography
            sx={{
              fontSize: 12,
              fontWeight: 500,
              color: "text.primary",
              whiteSpace: "nowrap",
            }}
          >
            {idLabel} :
          </Typography>
          <Typography
            noWrap
            sx={{
              fontSize: 12,
              color: "text.primary",
              whiteSpace: "nowrap",
              minWidth: 0,
            }}
          >
            {callId || "-"}
          </Typography>
          <Tooltip title={copyTooltip} arrow placement="bottom">
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

        <Stack direction="row" spacing={0.5}>
          <NavButton
            icon="mdi:chevron-up"
            tooltip="Previous call (↑)"
            onClick={onPrev}
            disabled={!hasPrev}
          />
          <NavButton
            icon="mdi:chevron-down"
            tooltip="Next call (↓)"
            onClick={onNext}
            disabled={!hasNext}
          />
        </Stack>
      </Stack>

      {/* Right: actions + close */}
      <Stack direction="row" alignItems="center" spacing={1.5}>
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
          {onDownload && (
            <HeaderButton
              icon="mdi:download-outline"
              tooltip="Download raw data"
              onClick={onDownload}
            />
          )}
          {onShare && (
            <HeaderButton
              icon="basil:share-outline"
              tooltip="Share call"
              onClick={onShare}
            />
          )}
        </Stack>

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

VoiceDrawerHeader.propTypes = {
  callId: PropTypes.string,
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
  idLabel: PropTypes.string,
  copyTooltip: PropTypes.string,
  copyToastMessage: PropTypes.string,
};

export default React.memo(VoiceDrawerHeader);
