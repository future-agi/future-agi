/* eslint-disable react/prop-types */
import React, { useState, useMemo, useCallback } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  Dialog,
  DialogContent,
  IconButton,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "notistack";
import {
  useAddSharedLinkAccess,
  useRemoveSharedLinkAccess,
} from "src/api/shared-links";
import useDialogGeneration from "./useDialogGeneration";
import useShareLink from "./useShareLink";
import useShareAccess from "./useShareAccess";
import useCopyLink from "./useCopyLink";

/* ── AccessOption ─────────────────────────────── */

const AccessOption = ({
  icon,
  iconColor,
  label,
  description,
  selected,
  disabled,
  pending,
  unconfirmed,
  onClick,
}) => (
  <Box
    role="button"
    aria-pressed={selected ? "true" : "false"}
    aria-busy={pending ? "true" : undefined}
    aria-disabled={disabled ? "true" : undefined}
    tabIndex={disabled ? -1 : 0}
    onClick={disabled ? undefined : onClick}
    onKeyDown={(e) => {
      if (disabled || !onClick) return;
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        onClick();
      }
    }}
    sx={{
      display: "flex",
      alignItems: "center",
      gap: 1.5,
      px: 1.5,
      py: 1.25,
      borderRadius: "6px",
      border: "1.5px solid",
      borderColor: selected ? "primary.main" : "divider",
      bgcolor: selected ? "rgba(87, 63, 204, 0.04)" : "background.paper",
      cursor: disabled ? "not-allowed" : "pointer",
      opacity: disabled ? 0.6 : 1,
      transition: "all 120ms",
      "&:hover": { borderColor: selected ? "primary.main" : "text.disabled" },
    }}
  >
    <Box
      sx={{
        width: 32,
        height: 32,
        borderRadius: "8px",
        bgcolor: selected ? "rgba(87, 63, 204, 0.1)" : "action.hover",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        flexShrink: 0,
      }}
    >
      <Iconify
        icon={icon}
        width={16}
        sx={{ color: selected ? "primary.main" : iconColor }}
      />
    </Box>
    <Box sx={{ flex: 1 }}>
      <Typography sx={{ fontSize: 13, fontWeight: 500, color: "text.primary" }}>
        {label}
      </Typography>
      <Typography
        sx={{ fontSize: 11, color: "text.disabled", lineHeight: "14px" }}
      >
        {description}
      </Typography>
    </Box>
    {pending ? (
      <Typography sx={{ fontSize: 11, color: "text.disabled", flexShrink: 0 }}>
        Updating…
      </Typography>
    ) : selected && unconfirmed ? (
      <Typography sx={{ fontSize: 11, color: "text.disabled", flexShrink: 0 }}>
        Last confirmed
      </Typography>
    ) : (
      selected && (
        <Iconify
          icon="mdi:check-circle"
          width={18}
          sx={{ color: "primary.main", flexShrink: 0 }}
        />
      )
    )}
  </Box>
);

/* ── ShareDialog ──────────────────────────────── */

const ShareDialog = ({ open, onClose, resourceType, resourceId }) => {
  const [emailInput, setEmailInput] = useState("");
  const [localEmails, setLocalEmails] = useState([]); // optimistic local ACL

  const generation = useDialogGeneration(
    open,
    `${resourceType ?? ""}|${resourceId ?? ""}`,
  );
  const link = useShareLink({ open, resourceType, resourceId, generation });
  const access = useShareAccess({
    shareLink: link.shareLink,
    linksUpdatedAt: link.linksUpdatedAt,
    linkBlocked: link.blocked,
    refetch: link.refetch,
    generation,
  });
  const { shareLink, loading } = link;
  const shareLinkReady = access.ready;

  // Only a token link is shareable: a dashboard page URL needs sign-in.
  const shareUrl = link.tokenUrl || null;
  const copyReady = Boolean(shareUrl) && !link.blocked && !access.settling;
  const { copied, copy: handleCopy } = useCopyLink({
    url: shareUrl,
    ready: copyReady,
    generation,
  });

  const notice = link.notice || access.notice;
  const handleRetry = () => link.retry(access.clearUnknown);

  const addAccessMutation = useAddSharedLinkAccess();
  const removeAccessMutation = useRemoveSharedLinkAccess();

  const allEmails = useMemo(() => {
    const accessList = shareLink?.access_list || [];
    const backendEmails = accessList.map((e) => ({
      id: e.id,
      email: e.email,
      source: "server",
    }));
    const localOnly = localEmails
      .filter((e) => !backendEmails.some((b) => b.email === e))
      .map((e) => ({ id: e, email: e, source: "local" }));
    return [...backendEmails, ...localOnly];
  }, [shareLink, localEmails]);

  const handleAddEmail = useCallback(() => {
    const email = emailInput.trim().toLowerCase();
    if (!email || !email.includes("@")) {
      if (email)
        enqueueSnackbar("Enter a valid email address", { variant: "warning" });
      return;
    }
    if (allEmails.some((e) => e.email === email)) {
      enqueueSnackbar("Already shared with this email", { variant: "info" });
      setEmailInput("");
      return;
    }
    const linkId = shareLink?.id;
    if (!linkId || !shareLinkReady) {
      enqueueSnackbar(
        loading
          ? "Share link is still being generated"
          : "Share link isn't ready yet",
        { variant: "warning" },
      );
      return;
    }

    // Try backend, always add locally for instant feedback
    setLocalEmails((prev) => [...prev, email]);
    setEmailInput("");
    enqueueSnackbar(`Shared with ${email}`, {
      variant: "success",
      autoHideDuration: 2000,
    });

    addAccessMutation.mutate({ linkId, emails: [email] });
  }, [
    emailInput,
    allEmails,
    shareLink,
    shareLinkReady,
    loading,
    addAccessMutation,
  ]);

  const handleRemoveEmail = useCallback(
    (entry) => {
      if (entry.source === "local") {
        setLocalEmails((prev) => prev.filter((e) => e !== entry.email));
      } else if (shareLink?.id) {
        removeAccessMutation.mutate({
          linkId: shareLink.id,
          accessId: entry.id,
        });
      }
    },
    [shareLink, removeAccessMutation],
  );

  const handleKeyDown = (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      handleAddEmail();
    }
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="xs"
      fullWidth
      PaperProps={{
        sx: {
          borderRadius: "12px",
          boxShadow: "0 20px 60px rgba(0,0,0,0.15)",
          maxWidth: 440,
          overflow: "visible",
        },
      }}
    >
      <DialogContent sx={{ p: 0 }}>
        {/* ── Header ──────────────────────────── */}
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
          sx={{ px: 2.5, pt: 2.5, pb: 1.5 }}
        >
          <Stack direction="row" alignItems="center" spacing={1}>
            <Box
              sx={{
                width: 28,
                height: 28,
                borderRadius: "6px",
                bgcolor: "rgba(87, 63, 204, 0.1)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <Iconify
                icon="mdi:share-variant-outline"
                width={15}
                sx={{ color: "primary.main" }}
              />
            </Box>
            <Typography
              sx={{ fontSize: 15, fontWeight: 600, color: "text.primary" }}
            >
              Share
            </Typography>
          </Stack>
          <IconButton
            size="small"
            onClick={onClose}
            sx={{ color: "text.disabled" }}
          >
            <Iconify icon="mdi:close" width={18} />
          </IconButton>
        </Stack>

        <Box sx={{ px: 2.5, pb: 2.5 }}>
          {/* ── Copy Link ─────────────────────── */}
          <Box
            sx={{
              display: "flex",
              alignItems: "center",
              gap: 1,
              p: 1,
              bgcolor: "background.default",
              border: "1px solid",
              borderColor: "divider",
              borderRadius: "8px",
              mb: 2,
            }}
          >
            <Iconify
              icon="mdi:link-variant"
              width={16}
              sx={{ color: "text.disabled", flexShrink: 0 }}
            />
            {loading || !shareUrl ? (
              <Typography
                sx={{ flex: 1, fontSize: 12, color: "text.disabled" }}
              >
                {link.generating
                  ? "Generating share link..."
                  : link.verifying
                    ? "Checking share link..."
                    : "Share link not available"}
              </Typography>
            ) : (
              <Typography
                noWrap
                title={shareUrl}
                sx={{
                  flex: 1,
                  fontSize: 12,
                  fontFamily: "monospace",
                  color: "text.secondary",
                  userSelect: "all",
                }}
              >
                {shareUrl}
              </Typography>
            )}
            <Button
              size="small"
              variant={copied ? "contained" : "outlined"}
              onClick={handleCopy}
              disabled={!copyReady}
              startIcon={
                <Iconify
                  icon={copied ? "mdi:check" : "mdi:content-copy"}
                  width={14}
                />
              }
              sx={{
                textTransform: "none",
                fontSize: 12,
                height: 30,
                px: 1.5,
                flexShrink: 0,
                borderRadius: "6px",
                ...(copied
                  ? {
                      bgcolor: "primary.main",
                      "&:hover": { bgcolor: "primary.dark" },
                    }
                  : { borderColor: "divider", color: "text.secondary" }),
              }}
            >
              {copied ? "Copied" : "Copy"}
            </Button>
          </Box>

          {notice && (
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              role="status"
              aria-live="polite"
              sx={{ mt: -1, mb: 2 }}
            >
              <Typography sx={{ flex: 1, fontSize: 12, color: "warning.dark" }}>
                {notice.message}
              </Typography>
              {notice.action && (
                <Button
                  size="small"
                  variant="text"
                  onClick={handleRetry}
                  disabled={link.retryBusy || loading}
                  sx={{ textTransform: "none", fontSize: 12, flexShrink: 0 }}
                >
                  {notice.action}
                </Button>
              )}
            </Stack>
          )}

          {/* ── Access Mode ───────────────────── */}
          <Typography
            sx={{
              fontSize: 12,
              fontWeight: 600,
              color: "text.disabled",
              mb: 1,
              textTransform: "uppercase",
              letterSpacing: "0.5px",
            }}
          >
            Who can access
          </Typography>
          <Stack spacing={1} sx={{ mb: 2 }}>
            <AccessOption
              icon="mdi:earth"
              iconColor="text.disabled"
              label="Anyone with the link"
              description="No sign-in required to view"
              selected={access.confirmedMode === "public"}
              pending={access.pendingMode === "public"}
              unconfirmed={access.accessUnknown}
              disabled={!shareLinkReady}
              onClick={() => access.changeMode("public")}
            />
            <AccessOption
              icon="mdi:shield-lock-outline"
              iconColor="text.disabled"
              label="Restricted"
              description="Only people you add can view"
              selected={access.confirmedMode === "restricted"}
              pending={access.pendingMode === "restricted"}
              unconfirmed={access.accessUnknown}
              disabled={!shareLinkReady}
              onClick={() => access.changeMode("restricted")}
            />
          </Stack>

          {/* ── Invite People ─────────────────── */}
          <Typography
            sx={{
              fontSize: 12,
              fontWeight: 600,
              color: "text.disabled",
              mb: 1,
              textTransform: "uppercase",
              letterSpacing: "0.5px",
            }}
          >
            Invite people
          </Typography>
          <Stack direction="row" spacing={1} sx={{ mb: 1 }}>
            <TextField
              size="small"
              fullWidth
              placeholder="name@email.com"
              value={emailInput}
              onChange={(e) => setEmailInput(e.target.value)}
              onKeyDown={handleKeyDown}
              InputProps={{
                sx: {
                  fontSize: 13,
                  borderRadius: "8px",
                  bgcolor: "background.paper",
                },
                startAdornment: (
                  <Iconify
                    icon="mdi:email-outline"
                    width={16}
                    sx={{ color: "text.disabled", mr: 0.75 }}
                  />
                ),
              }}
            />
            <Button
              variant="contained"
              size="small"
              onClick={handleAddEmail}
              disabled={!emailInput.trim() || !shareLinkReady}
              sx={{
                textTransform: "none",
                px: 2,
                flexShrink: 0,
                borderRadius: "8px",
                bgcolor: "primary.main",
                height: 40,
                "&:hover": { bgcolor: "primary.dark" },
                "&.Mui-disabled": { bgcolor: "action.disabledBackground" },
              }}
            >
              Invite
            </Button>
          </Stack>

          {/* ── People with access ────────────── */}
          {allEmails.length > 0 && (
            <Box
              sx={{
                mt: 1.5,
                border: "1px solid",
                borderColor: "divider",
                borderRadius: "8px",
                overflow: "hidden",
              }}
            >
              <Typography
                sx={{
                  fontSize: 11,
                  fontWeight: 600,
                  color: "text.disabled",
                  textTransform: "uppercase",
                  letterSpacing: "0.5px",
                  px: 1.5,
                  py: 0.75,
                  bgcolor: "background.default",
                  borderBottom: "1px solid",
                  borderColor: "divider",
                }}
              >
                People with access ({allEmails.length})
              </Typography>
              {allEmails.map((entry) => (
                <Stack
                  key={entry.id}
                  direction="row"
                  alignItems="center"
                  justifyContent="space-between"
                  sx={{
                    px: 1.5,
                    py: 0.75,
                    borderBottom: "1px solid",
                    borderColor: "divider",
                    "&:last-child": { borderBottom: "none" },
                    "&:hover": { bgcolor: "background.default" },
                  }}
                >
                  <Stack direction="row" alignItems="center" spacing={1}>
                    <Box
                      sx={{
                        width: 26,
                        height: 26,
                        borderRadius: "50%",
                        background:
                          "linear-gradient(135deg, #573fcc 0%, #7c5ce7 100%)",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        color: "white",
                        fontSize: 11,
                        fontWeight: 600,
                      }}
                    >
                      {entry.email[0].toUpperCase()}
                    </Box>
                    <Box>
                      <Typography
                        sx={{
                          fontSize: 12,
                          color: "text.primary",
                          lineHeight: "16px",
                        }}
                      >
                        {entry.email}
                      </Typography>
                      <Typography sx={{ fontSize: 10, color: "text.disabled" }}>
                        Can view
                      </Typography>
                    </Box>
                  </Stack>
                  <Tooltip title="Remove access">
                    <IconButton
                      size="small"
                      aria-label={`Remove access for ${entry.email}`}
                      onClick={() => handleRemoveEmail(entry)}
                      sx={{ opacity: 0.4, "&:hover": { opacity: 1 } }}
                    >
                      <Iconify icon="mdi:close" width={14} />
                    </IconButton>
                  </Tooltip>
                </Stack>
              ))}
            </Box>
          )}
        </Box>
      </DialogContent>
    </Dialog>
  );
};

ShareDialog.propTypes = {
  open: PropTypes.bool.isRequired,
  onClose: PropTypes.func.isRequired,
  resourceType: PropTypes.string.isRequired,
  resourceId: PropTypes.string.isRequired,
};

export default ShareDialog;
