import React, { useEffect, useCallback } from "react";
import PropTypes from "prop-types";
import { IconButton, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "notistack";

const HeaderButton = ({ icon, tooltip, onClick, disabled }) => (
  <Tooltip title={tooltip} arrow placement="bottom">
    <span>
      <IconButton
        size="small"
        onClick={onClick}
        disabled={disabled}
        sx={{
          border: "1px solid",
          borderColor: "divider",
          bgcolor: "background.paper",
        }}
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

const NavButton = ({ icon, tooltip, onClick, disabled }) => (
  <Tooltip title={tooltip} arrow placement="bottom">
    <span>
      <IconButton
        size="small"
        onClick={onClick}
        disabled={disabled}
        sx={{
          border: "1px solid",
          borderColor: "divider",
          bgcolor: "background.paper",
        }}
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
            <IconButton
              size="small"
              onClick={handleCopy}
              aria-label="Copy call ID"
            >
              <Iconify
                icon="solar:copy-linear"
                width={12}
                sx={{ color: "text.secondary" }}
              />
            </IconButton>
          </Tooltip>
        </Stack>

        <Stack direction="row" spacing={0.5}>
          <NavButton
            icon="solar:alt-arrow-up-linear"
            tooltip="Previous call (↑)"
            onClick={onPrev}
            disabled={!hasPrev}
          />
          <NavButton
            icon="solar:alt-arrow-down-linear"
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
          {onDownload && (
            <HeaderButton
              icon="solar:download-minimalistic-linear"
              tooltip="Download raw data"
              onClick={onDownload}
            />
          )}
          {onShare && (
            <HeaderButton
              icon="solar:share-linear"
              tooltip="Share call"
              onClick={onShare}
            />
          )}
        </Stack>

        <Tooltip title="Close (Esc)" arrow placement="bottom">
          <IconButton size="small" onClick={onClose} aria-label="Close">
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
