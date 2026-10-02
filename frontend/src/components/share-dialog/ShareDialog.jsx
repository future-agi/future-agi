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

const COPIED_RESET_MS = 2000;

// Map a transport error to safe, fixed copy. Server-supplied text is never
// rendered: it can carry HTML, stack traces, tokens or URLs.
function describeLinkError(error, action) {
  const status = error?.response?.status ?? error?.status;
  if (status === 401) {
    return "Your session has expired. Sign in again to share this item.";
  }
  if (status === 403 || status === 404) {
    return "This item can't be shared from here.";
  }
  return action === "create"
    ? "Couldn't create a share link. Check your connection and retry."
    : "Couldn't load the share link. Check your connection and retry.";
}

function readToken(link) {
  const token = link?.token;
  return typeof token === "string" ? token.trim() : "";
}

function readAccessMode(link) {
  return link?.access_type ?? link?.accessType ?? null;
}

function unwrapResult(response) {
  return (
    response?.data?.result ?? response?.result ?? response?.data ?? response
  );
}

/* ── AccessOption ─────────────────────────────── */

const AccessOption = ({
  icon,
  iconColor,
  label,
  description,
  selected,
  disabled,
  pending,
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
  // Access mode requested but not yet acknowledged by the server.
  const [pendingMode, setPendingMode] = useState(null);
  // Access mode acknowledged by a PATCH response, before the list refetch lands.
  const [ackMode, setAckMode] = useState(null); // { linkId, mode, at }
  // A failed update left the server state uncertain until a successful reread.
  const [accessUnknown, setAccessUnknown] = useState(false);
  const [accessNotice, setAccessNotice] = useState(null);
  // Reopen/resource switch rereads the server before enabling actions.
  const [verifying, setVerifying] = useState(false);
  const [retryBusy, setRetryBusy] = useState(false);
  const [retryNonce, setRetryNonce] = useState(0);

  // Fetch existing shared links for this resource
  const {
    data: links,
    isLoading: linksLoading,
    isError: linksError,
    error: linksErrorDetail,
    dataUpdatedAt: linksUpdatedAt,
    refetch: refetchLinks,
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
  // Bumped on close and on resource change so late async completions
  // (clipboard, PATCH, reread) cannot act on the next dialog.
  const generation = useRef(0);
  const copyInFlight = useRef(false);
  const copiedTimer = useRef(null);
  const createdLink =
    createMutation.data?.data?.result || createMutation.data?.result || null;
  const shareLink = activeLink || createdLink;
  const createError = Boolean(createMutation.isError);

  const refetch = useCallback(async () => {
    if (typeof refetchLinks !== "function") return null;
    try {
      const result = await refetchLinks();
      if (!result || result.isError || result.data === undefined) return null;
      return result;
    } catch {
      return null;
    }
  }, [refetchLinks]);

  const clearCopiedTimer = () => {
    if (copiedTimer.current) {
      clearTimeout(copiedTimer.current);
      copiedTimer.current = null;
    }
  };

  // Reset local state when the dialog closes or the resource changes.
  const contextKey = `${resourceType ?? ""}|${resourceId ?? ""}`;
  const prevContext = useRef(contextKey);
  const prevOpen = useRef(open);
  useEffect(() => {
    const contextChanged = prevContext.current !== contextKey;
    const closed = prevOpen.current && !open;
    prevContext.current = contextKey;
    prevOpen.current = open;
    if (!contextChanged && !closed) return;
    generation.current += 1;
    copyInFlight.current = false;
    autoCreated.current = false;
    clearCopiedTimer();
    setCopied(false);
    setPendingMode(null);
    setAckMode(null);
    setAccessUnknown(false);
    setAccessNotice(null);
    setVerifying(false);
    setRetryBusy(false);
    createMutation.reset?.();
    updateMutation.reset?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contextKey, open]);

  useEffect(() => clearCopiedTimer, []);

  // Reopen reads the server before trusting a cached link (R7).
  useEffect(() => {
    if (!open || !resourceType || !resourceId) return;
    if (linksLoading || links === undefined) return;
    if (typeof refetchLinks !== "function") return;
    const gen = generation.current;
    setVerifying(true);
    refetch().finally(() => {
      if (gen === generation.current) setVerifying(false);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, contextKey]);

  // Auto-create a restricted shared link when dialog opens and none exists.
  // Only after a successful empty discovery; never from an error state.
  useEffect(() => {
    if (!open || !resourceType || !resourceId) return;
    if (
      !linksLoading &&
      !linksError &&
      links &&
      links.length === 0 &&
      !createMutation.isPending &&
      !createMutation.isError &&
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
    retryNonce,
  ]);

  // Confirmed access mode: a matching PATCH acknowledgement wins until a
  // newer server read lands; otherwise the server read is authoritative.
  const serverMode = readAccessMode(shareLink);
  const confirmedMode = useMemo(() => {
    if (ackMode && shareLink?.id && ackMode.linkId === shareLink.id) {
      const serverIsNewer =
        typeof linksUpdatedAt === "number" && linksUpdatedAt > ackMode.at;
      if (!serverIsNewer) return ackMode.mode;
    }
    return serverMode || "restricted";
  }, [ackMode, shareLink, linksUpdatedAt, serverMode]);

  // Share URL: token-based when ready, caller-supplied fallback otherwise.
  // There is no implicit current-page fallback: an authenticated page URL is
  // not a share link.
  const token = readToken(shareLink);
  const tokenUrl = token ? `${window.location.origin}/shared/${token}` : null;
  const shareUrl = tokenUrl || fallbackShareUrl || null;

  const loading = linksLoading || createMutation.isPending || verifying;
  const linkBlocked =
    loading ||
    linksError ||
    createError ||
    accessUnknown ||
    Boolean(pendingMode) ||
    retryBusy;
  const copyReady = tokenUrl
    ? !linkBlocked
    : Boolean(fallbackShareUrl) && !loading && !accessUnknown && !pendingMode;
  const shareLinkReady = Boolean(shareLink?.id) && !linkBlocked;
  const linkUnavailable =
    !loading &&
    !linksError &&
    !createError &&
    !shareLink &&
    Array.isArray(links) &&
    links.length > 0;

  let linkNotice = null;
  let linkNoticeAction = null;
  if (linksError) {
    linkNotice = describeLinkError(linksErrorDetail, "load");
    linkNoticeAction = "Retry";
  } else if (createError) {
    linkNotice = describeLinkError(createMutation.error, "create");
    linkNoticeAction = "Retry";
  } else if (linkUnavailable) {
    linkNotice =
      "This share link is no longer active. Recheck to see the current state.";
    linkNoticeAction = "Recheck";
  } else if (accessUnknown && !pendingMode) {
    linkNotice = accessNotice;
    linkNoticeAction = "Recheck";
  }

  const handleRetry = useCallback(async () => {
    if (retryBusy) return;
    const gen = generation.current;
    setRetryBusy(true);
    try {
      if (createMutation.isError) createMutation.reset?.();
      const result = await refetch();
      if (gen !== generation.current) return;
      if (!result) return;
      setAccessUnknown(false);
      setAccessNotice(null);
      if (Array.isArray(result.data) && result.data.length === 0) {
        autoCreated.current = false;
        setRetryNonce((n) => n + 1);
      }
    } finally {
      if (gen === generation.current) setRetryBusy(false);
    }
  }, [retryBusy, createMutation, refetch]);

  // Persist access mode changes to server; display only confirmed state.
  const handleAccessModeChange = useCallback(
    (mode) => {
      if (!shareLinkReady || mode === confirmedMode) return;
      const linkId = shareLink.id;
      const gen = generation.current;
      setPendingMode(mode);
      setAccessNotice(null);
      updateMutation.mutate(
        { id: linkId, access_type: mode },
        {
          onSuccess: (response) => {
            if (gen !== generation.current) return;
            const confirmed = readAccessMode(unwrapResult(response)) || mode;
            setAckMode({ linkId, mode: confirmed, at: Date.now() });
            setPendingMode(null);
            setAccessUnknown(false);
          },
          onError: async () => {
            if (gen !== generation.current) return;
            // The request may have committed before the response was lost:
            // never assert a rollback, reread instead.
            setPendingMode(null);
            setAccessUnknown(true);
            setAccessNotice(
              "Could not confirm the access change. Rechecking the current setting…",
            );
            const result = await refetch();
            if (gen !== generation.current) return;
            if (result) {
              setAckMode(null);
              setAccessUnknown(false);
              setAccessNotice(null);
              enqueueSnackbar(
                "Couldn't confirm the access change. Showing the current setting.",
                { variant: "warning" },
              );
            } else {
              setAccessNotice(
                "The access setting couldn't be confirmed. Recheck to continue.",
              );
            }
          },
        },
      );
    },
    [shareLinkReady, confirmedMode, shareLink, updateMutation, refetch],
  );

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

  // Report success only after the clipboard write resolves for this dialog.
  const handleCopy = useCallback(async () => {
    if (!copyReady || copyInFlight.current) return;
    const gen = generation.current;
    const url = shareUrl;
    if (typeof navigator.clipboard?.writeText !== "function") {
      enqueueSnackbar(
        "Clipboard isn't available. Select the link and copy it manually.",
        { variant: "warning" },
      );
      return;
    }
    copyInFlight.current = true;
    try {
      await navigator.clipboard.writeText(url);
      if (gen !== generation.current) return;
      setCopied(true);
      enqueueSnackbar("Link copied!", {
        variant: "success",
        autoHideDuration: 1500,
      });
      clearCopiedTimer();
      copiedTimer.current = setTimeout(() => {
        copiedTimer.current = null;
        setCopied(false);
      }, COPIED_RESET_MS);
    } catch {
      if (gen !== generation.current) return;
      enqueueSnackbar(
        "Couldn't copy the link. Select it and copy it manually.",
        { variant: "warning" },
      );
    } finally {
      copyInFlight.current = false;
    }
  }, [copyReady, shareUrl]);

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
                {linksLoading || createMutation.isPending
                  ? "Generating share link..."
                  : verifying
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

          {/* ── Link status / recovery ────────── */}
          {linkNotice && (
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              role="status"
              aria-live="polite"
              sx={{ mt: -1, mb: 2 }}
            >
              <Typography sx={{ flex: 1, fontSize: 12, color: "warning.dark" }}>
                {linkNotice}
              </Typography>
              {linkNoticeAction && (
                <Button
                  size="small"
                  variant="text"
                  onClick={handleRetry}
                  disabled={retryBusy || loading}
                  sx={{ textTransform: "none", fontSize: 12, flexShrink: 0 }}
                >
                  {linkNoticeAction}
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
              selected={confirmedMode === "public"}
              pending={pendingMode === "public"}
              disabled={!shareLinkReady}
              onClick={() => handleAccessModeChange("public")}
            />
            <AccessOption
              icon="mdi:shield-lock-outline"
              iconColor="text.disabled"
              label="Restricted"
              description="Only people you add can view"
              selected={confirmedMode === "restricted"}
              pending={pendingMode === "restricted"}
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
