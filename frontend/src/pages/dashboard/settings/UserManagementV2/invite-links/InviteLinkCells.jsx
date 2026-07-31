import React, { useState } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography, IconButton, Tooltip } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { useMutation } from "@tanstack/react-query";
import axios, { endpoints } from "src/utils/axios";

// "Invite link" grid cell — only pending invites carry a link. Active members
// render a dash.
export function InviteLinkCell({ data }) {
  const [copied, setCopied] = useState(false);
  const link = data?.invite_link;

  if (!link) {
    return (
      <Typography
        variant="s2"
        color="text.disabled"
        sx={{ lineHeight: "40px" }}
      >
        —
      </Typography>
    );
  }

  const copy = async (e) => {
    e?.stopPropagation();
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      enqueueSnackbar("Could not copy", { variant: "error" });
    }
  };

  return (
    <Stack
      direction="row"
      spacing={0.5}
      alignItems="center"
      sx={{ height: "100%", minWidth: 0 }}
    >
      <Tooltip title={copied ? "Copied" : "Copy invite link"}>
        <IconButton
          size="small"
          onClick={copy}
          sx={{ color: copied ? "success.main" : "primary.main" }}
        >
          <Iconify
            icon={copied ? "solar:check-read-linear" : "solar:copy-linear"}
            width={16}
          />
        </IconButton>
      </Tooltip>
      <Typography
        variant="s2"
        onClick={copy}
        sx={{
          fontFamily: "monospace",
          color: "text.secondary",
          cursor: "pointer",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          minWidth: 0,
        }}
      >
        {link}
      </Typography>
    </Stack>
  );
}

InviteLinkCell.propTypes = {
  data: PropTypes.object,
};

// Cancel a pending invite. Active members show nothing here.
export function InviteActionCell({ data, onRefresh }) {
  const { mutate: cancelInvite, isPending } = useMutation({
    mutationFn: (inviteId) =>
      axios.delete(endpoints.rbac.inviteCancel, {
        data: { invite_id: inviteId },
      }),
    // The page-level handler already surfaces API errors; opting out of the
    // global one avoids a duplicate toast.
    meta: { errorHandled: true },
    onSuccess: () => {
      enqueueSnackbar("Invite cancelled", { variant: "success" });
      onRefresh?.();
    },
    onError: (error) => {
      enqueueSnackbar(
        error?.result || error?.error || "Could not cancel the invite",
        { variant: "error" },
      );
    },
  });

  if (!data?.invite_link) return null;

  return (
    <Box sx={{ height: "100%", display: "flex", alignItems: "center" }}>
      <Tooltip title="Cancel invite">
        <IconButton
          size="small"
          disabled={isPending}
          onClick={(e) => {
            e.stopPropagation();
            cancelInvite(data.id);
          }}
          sx={{ color: "text.disabled", "&:hover": { color: "error.main" } }}
        >
          <Iconify icon="solar:trash-bin-minimalistic-linear" width={16} />
        </IconButton>
      </Tooltip>
    </Box>
  );
}

InviteActionCell.propTypes = {
  data: PropTypes.object,
  onRefresh: PropTypes.func,
};
