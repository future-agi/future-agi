/* eslint-disable react/prop-types */
import React, { useState, useMemo, useEffect, useRef } from "react";
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
  useGetSharedLinkDetail,
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
      bgcolor: selected ? "action.hover" : "background.paper",
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
        bgcolor: selected ? "status.discovery.bg" : "action.hover",
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
        sx={{ fontSize: 12, color: "text.disabled", lineHeight: "14px" }}
      >
        {description}
      </Typography>
    </Box>
    {pending ? (
      <Typography sx={{ fontSize: 12, color: "text.disabled", flexShrink: 0 }}>
        Updating…
      </Typography>
    ) : selected && unconfirmed ? (
      <Typography sx={{ fontSize: 12, color: "text.disabled", flexShrink: 0 }}>
        Last confirmed
      </Typography>
    ) : (
      selected && (
        <Iconify
          icon="solar:check-circle-bold"
          width={20}
          sx={{ color: "primary.main", flexShrink: 0 }}
        />
      )
    )}
  </Box>
);

const isGrant = (entry) =>
  Boolean(entry?.id) &&
  typeof entry.email === "string" &&
  Boolean(entry.email.trim());
const normalizedEmail = (email) => email.trim().toLowerCase();

/* ── ShareDialog ──────────────────────────────── */

const ShareDialog = ({ open, onClose, resourceType, resourceId }) => {
  const [emailInput, setEmailInput] = useState("");
  const [pendingInvite, setPendingInvite] = useState(null);
  const [removingId, setRemovingId] = useState(null);
  const [aclChanges, setAclChanges] = useState([]);

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
  const linkId = shareLink?.id;
  const detail = useGetSharedLinkDetail(open ? linkId || null : null);

  // Each open/resource/link has its own ACL work. A stale callback must not
  // populate, notify, or unlock a later session (including after unmount).
  const aclScope = useMemo(
    () => ({ open, resourceType, resourceId, linkId }),
    [open, resourceType, resourceId, linkId],
  );
  const currentAclScope = useRef(aclScope);
  const aclOperation = useRef(false);
  currentAclScope.current = aclScope;
  useEffect(() => {
    currentAclScope.current = aclScope;
    aclOperation.current = false;
    setEmailInput("");
    setPendingInvite(null);
    setRemovingId(null);
    setAclChanges([]);
    return () => {
      if (currentAclScope.current === aclScope) currentAclScope.current = null;
    };
  }, [aclScope]);

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
    const accessList =
      linkId && detail.data?.id === linkId ? detail.data.access_list : [];
    const grants = new Map(
      (Array.isArray(accessList) ? accessList : [])
        .filter(isGrant)
        .map((entry) => [entry.id, entry]),
    );
    // Mutation acknowledgements carry real IDs. Keep them until a newer
    // successful detail read supersedes them, not just until any rerender.
    aclChanges.forEach((change) => {
      if (change.scope !== aclScope) return;
      if (
        !detail.isError &&
        detail.data?.id === linkId &&
        detail.dataUpdatedAt > change.at
      )
        return;
      if (change.removed) grants.delete(change.grant.id);
      else grants.set(change.grant.id, change.grant);
    });
    return [...grants.values()];
  }, [
    detail.data,
    detail.dataUpdatedAt,
    detail.isError,
    linkId,
    aclChanges,
    aclScope,
  ]);

  const rememberGrant = (grant, removed = false) => {
    setAclChanges((previous) => [
      ...previous.filter(
        (change) => change.scope === aclScope && change.grant.id !== grant.id,
      ),
      { scope: aclScope, grant, removed, at: Date.now() },
    ]);
  };
  const aclBusy = Boolean(pendingInvite || removingId);
  const isCurrentAcl = () => currentAclScope.current === aclScope;

  const confirmInvitation = (grant) => {
    rememberGrant(grant);
    setPendingInvite(null);
    aclOperation.current = false;
    enqueueSnackbar(`Shared with ${grant.email}`, {
      variant: "success",
      autoHideDuration: 2000,
    });
  };

  // A duplicate POST returns []; an error may also have committed. Neither
  // outcome supplies a removable grant until a successful detail read does.
  const recheckInvitation = async (email) => {
    if (!isCurrentAcl()) return;
    setPendingInvite({ email, unknown: false });
    try {
      const result = await detail.refetch();
      if (!isCurrentAcl()) return;
      if (
        result?.isError ||
        result?.data?.id !== linkId ||
        !Array.isArray(result?.data?.access_list)
      ) {
        throw new Error("Unconfirmed invitation");
      }
      const grant = result.data.access_list.find(
        (entry) => isGrant(entry) && normalizedEmail(entry.email) === email,
      );
      if (grant) confirmInvitation(grant);
      else {
        setPendingInvite(null);
        aclOperation.current = false;
        enqueueSnackbar("Invitation was not confirmed. Please try again.", {
          variant: "warning",
        });
      }
    } catch {
      if (!isCurrentAcl()) return;
      setPendingInvite({ email, unknown: true });
      enqueueSnackbar("Couldn't confirm the invitation. Recheck access.", {
        variant: "warning",
      });
    }
  };

  const handleAddEmail = () => {
    if (!open || aclOperation.current) return;
    const email = normalizedEmail(emailInput);
    if (!email || !email.includes("@")) {
      if (email)
        enqueueSnackbar("Enter a valid email address", { variant: "warning" });
      return;
    }
    if (allEmails.some((e) => normalizedEmail(e.email) === email)) {
      enqueueSnackbar("Already shared with this email", { variant: "info" });
      setEmailInput("");
      return;
    }
    if (!linkId || !shareLinkReady) {
      enqueueSnackbar(
        loading
          ? "Share link is still being generated"
          : "Share link isn't ready yet",
        { variant: "warning" },
      );
      return;
    }

    // Serialize ACL writes: per-call mutation callbacks otherwise only run
    // for the last consecutive mutation, stranding earlier pending invites.
    aclOperation.current = true;
    setPendingInvite({ email, unknown: false });
    setEmailInput("");
    addAccessMutation.mutate(
      { linkId, emails: [email] },
      {
        onSuccess: (response) => {
          if (!isCurrentAcl()) return;
          const created = response?.data?.result;
          const grant =
            Array.isArray(created) &&
            created.find(
              (entry) =>
                isGrant(entry) && normalizedEmail(entry.email) === email,
            );
          if (grant) confirmInvitation(grant);
          else return recheckInvitation(email);
        },
        onError: () => {
          if (isCurrentAcl()) return recheckInvitation(email);
        },
      },
    );
  };

  const handleRemoveEmail = (entry) => {
    if (
      !open ||
      !linkId ||
      !shareLinkReady ||
      aclOperation.current ||
      !isGrant(entry)
    )
      return;
    aclOperation.current = true;
    setRemovingId(entry.id);
    removeAccessMutation.mutate(
      { linkId, accessId: entry.id },
      {
        onSuccess: () => {
          if (!isCurrentAcl()) return;
          rememberGrant(entry, true);
          setRemovingId(null);
          aclOperation.current = false;
        },
        onError: () => {
          if (!isCurrentAcl()) return;
          setRemovingId(null);
          aclOperation.current = false;
          enqueueSnackbar(
            "Couldn't confirm removal. Recheck access or try again.",
            { variant: "warning" },
          );
          // A lost DELETE response is not evidence of rollback. Refresh the
          // authoritative list, but never hide the row merely on failure.
          detail.refetch?.().catch(() => {});
        },
      },
    );
  };

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
      maxWidth={false}
      fullWidth
      PaperProps={{
        sx: {
          borderRadius: "12px",
          maxWidth: 560,
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
                bgcolor: "status.discovery.bg",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
              }}
            >
              <Iconify
                icon="solar:share-linear"
                width={16}
                sx={{ color: "primary.main" }}
              />
            </Box>
            <Typography
              sx={{ fontSize: 13, fontWeight: 600, color: "text.primary" }}
            >
              Share
            </Typography>
          </Stack>
          <IconButton
            size="small"
            onClick={onClose}
            sx={{ color: "text.disabled" }}
          >
            <Iconify icon="solar:close-linear" width={20} />
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
              icon="solar:link-linear"
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
                  icon={copied ? "solar:check-linear" : "solar:copy-linear"}
                  width={14}
                />
              }
              sx={{
                flexShrink: 0,
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
              textTransform: "none",
              letterSpacing: "0.5px",
            }}
          >
            Who can access
          </Typography>
          <Stack spacing={1} sx={{ mb: 2 }}>
            <AccessOption
              icon="solar:global-linear"
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
              icon="solar:shield-keyhole-linear"
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
              textTransform: "none",
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
                    icon="solar:letter-linear"
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
              disabled={!emailInput.trim() || !shareLinkReady || aclBusy}
              sx={{
                flexShrink: 0,
                bgcolor: "primary.main",
                "&:hover": { bgcolor: "primary.dark" },
                "&.Mui-disabled": { bgcolor: "action.disabledBackground" },
              }}
            >
              Invite
            </Button>
          </Stack>

          {pendingInvite && (
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              role="status"
              sx={{ my: 1 }}
            >
              <Box sx={{ flex: 1 }}>
                <Typography sx={{ fontSize: 12 }}>
                  {pendingInvite.email}
                </Typography>
                <Typography sx={{ fontSize: 12, color: "text.disabled" }}>
                  {pendingInvite.unknown
                    ? "Invitation not confirmed"
                    : "Pending invitation"}
                </Typography>
              </Box>
              {pendingInvite.unknown && (
                <Button
                  size="small"
                  onClick={() => recheckInvitation(pendingInvite.email)}
                >
                  Recheck invitation
                </Button>
              )}
            </Stack>
          )}
          {detail.isError && (
            <Stack
              direction="row"
              alignItems="center"
              spacing={1}
              role="status"
            >
              <Typography sx={{ fontSize: 12, color: "warning.dark" }}>
                {"Couldn't load people with access."}
              </Typography>
              <Button
                size="small"
                disabled={aclBusy}
                onClick={() => detail.refetch().catch(() => {})}
              >
                Recheck access
              </Button>
            </Stack>
          )}

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
                  fontSize: 12,
                  fontWeight: 600,
                  color: "text.disabled",
                  textTransform: "none",
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
                        fontSize: 12,
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
                      <Typography sx={{ fontSize: 12, color: "text.disabled" }}>
                        {removingId === entry.id
                          ? "Removing access…"
                          : "Can view"}
                      </Typography>
                    </Box>
                  </Stack>
                  <Tooltip title="Remove access">
                    <span>
                      <IconButton
                        size="small"
                        aria-label={`Remove access for ${entry.email}`}
                        onClick={() => handleRemoveEmail(entry)}
                        disabled={!shareLinkReady || aclBusy}
                        sx={{ opacity: 0.4, "&:hover": { opacity: 1 } }}
                      >
                        <Iconify icon="solar:close-linear" width={14} />
                      </IconButton>
                    </span>
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
