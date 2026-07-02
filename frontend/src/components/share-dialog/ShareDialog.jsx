/* eslint-disable react/prop-types */
import React, {
  useState,
  useMemo,
  useCallback,
  useEffect,
  useRef,
} from "react";
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
  useGetSharedLinks,
  useCreateSharedLink,
  useUpdateSharedLink,
  useAddSharedLinkAccess,
  useRemoveSharedLinkAccess,
} from "src/api/shared-links";

/* ── helpers ──────────────────────────────────── */

function buildFallbackUrl() {
  // Use current page URL — it already has the drawer/trace state in query params
  return window.location.href;
}

function copyToClipboard(text) {
  navigator.clipboard.writeText(text);
  enqueueSnackbar("Link copied!", {
    variant: "success",
    autoHideDuration: 1500,
  });
}

/* ── AccessOption ─────────────────────────────── */

const AccessOption = ({
  icon,
  iconColor,
  label,
  description,
  selected,
  disabled,
  onClick,
}) => (
  <Box
    role="button"
    aria-disabled={disabled ? "true" : undefined}
    tabIndex={disabled ? -1 : 0}
    onClick={disabled ? undefined : onClick}
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
    {selected && (
      <Iconify
        icon="mdi:check-circle"
        width={18}
        sx={{ color: "primary.main", flexShrink: 0 }}
      />
    )}
  </Box>
);

/* ── ShareDialog ──────────────────────────────── */

const ShareDialog = ({
  open,
  onClose,
  resourceType,
  resourceId,
  fallbackShareUrl,
}) => {
  const [emailInput, setEmailInput] = useState("");
  const [copied, setCopied] = useState(false);
  const [localEmails, setLocalEmails] = useState([]); // optimistic local ACL
  const [accessMode, setAccessMode] = useState("restricted"); // "restricted" | "public"

  // Fetch existing shared links for this resource
  const {
    data: links,
    isLoading: linksLoading,
    isError: linksError,
  } = useGetSharedLinks(open ? resourceType : null, open ? resourceId : null);
  // Handle both camelCase (isActive) and snake_case (is_active) from DRF
  const activeLink = useMemo(() => {
    if (!links || !Array.isArray(links)) return null;
    return links.find((l) => (l.is_active ?? l.isActive) !== false) || null;
  }, [links]);

  const createMutation = useCreateSharedLink();
  const updateMutation = useUpdateSharedLink();
  const addAccessMutation = useAddSharedLinkAccess();
  const removeAccessMutation = useRemoveSharedLinkAccess();
  const autoCreated = useRef(false);
  const createdLink =
    createMutation.data?.data?.result || createMutation.data?.result || null;
  const shareLink = activeLink || createdLink;
  const shareLinkReady = Boolean(shareLink?.id);

  // Auto-create a restricted shared link when dialog opens and none exists
  useEffect(() => {
    if (!open || !resourceType || !resourceId) return;
    // Only attempt create when links loaded successfully, is empty, and we haven't tried yet
    if (
      !linksLoading &&
      !linksError &&
      links &&
      links.length === 0 &&
      !createMutation.isPending &&
      !autoCreated.current
    ) {
      autoCreated.current = true;
      createMutation.mutate({
        resource_type: resourceType,
        resource_id: resourceId,
        access_type: "restricted",
      });
    }
  }, [
    open,
    links,
    linksLoading,
    linksError,
    resourceType,
    resourceId,
    createMutation,
  ]);

  // Reset auto-create flag when dialog closes
  useEffect(() => {
    if (!open) autoCreated.current = false;
  }, [open]);

  // Sync access mode from server link
  useEffect(() => {
    if (shareLink) {
      const serverMode = shareLink.accessType || shareLink.access_type;
      if (serverMode) setAccessMode(serverMode);
    }
  }, [shareLink]);

  // Persist access mode changes to server
  const handleAccessModeChange = useCallback(
    (mode) => {
      setAccessMode(mode);
      if (shareLink?.id) {
        updateMutation.mutate({ id: shareLink.id, access_type: mode });
      }
    },
    [shareLink, updateMutation],
  );

  // Share URL: token-based when link exists, direct fallback otherwise
  // Also use the just-created link data from createMutation
  const shareUrl = useMemo(() => {
    // First check the active link from the query
    const token = shareLink?.token;
    if (token) {
      return `${window.location.origin}/shared/${token}`;
    }
    // Caller-supplied direct fallback (e.g. a voice-call full-page URL)
    if (fallbackShareUrl) return fallbackShareUrl;
    // Last-resort fallback — current page URL.
    return buildFallbackUrl();
  }, [shareLink, fallbackShareUrl]);

  const allEmails = useMemo(() => {
    const accessList = shareLink?.accessList || shareLink?.access_list || [];
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

  const handleCopy = useCallback(() => {
    copyToClipboard(shareUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }, [shareUrl]);

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
    if (!linkId) {
      enqueueSnackbar("Share link is still being generated", {
        variant: "warning",
      });
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
  }, [emailInput, allEmails, shareLink, addAccessMutation]);

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
            {linksLoading || createMutation.isPending ? (
              <Typography
                sx={{ flex: 1, fontSize: 12, color: "text.disabled" }}
              >
                Generating share link...
              </Typography>
            ) : (
              <Typography
                noWrap
                sx={{
                  flex: 1,
                  fontSize: 12,
                  fontFamily: "monospace",
                  color: "text.secondary",
                }}
              >
                {shareUrl}
              </Typography>
            )}
            <Button
              size="small"
              variant={copied ? "contained" : "outlined"}
              onClick={handleCopy}
              disabled={linksLoading || createMutation.isPending}
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
              selected={accessMode === "public"}
              disabled={!shareLinkReady}
              onClick={() => handleAccessModeChange("public")}
            />
            <AccessOption
              icon="mdi:shield-lock-outline"
              iconColor="text.disabled"
              label="Restricted"
              description="Only people you add can view"
              selected={accessMode === "restricted"}
              disabled={!shareLinkReady}
              onClick={() => handleAccessModeChange("restricted")}
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
  fallbackShareUrl: PropTypes.string,
  resourceId: PropTypes.string.isRequired,
};

export default ShareDialog;
