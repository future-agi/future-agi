import React from "react";
import PropTypes from "prop-types";
import {
  Dialog,
  Box,
  Stack,
  Typography,
  TextField,
  Button,
  IconButton,
  alpha,
} from "@mui/material";
import Iconify from "src/components/iconify";
import CopyLinkButton from "./CopyLinkButton";
import { formatInvitesForCopy } from "./formatInvites";

function subtitleFor(invites, withLinkCount) {
  if (!withLinkCount) {
    return invites.length === 1
      ? "This invite has no shareable link, so it has to arrive by email."
      : "These invites have no shareable links, so they have to arrive by email.";
  }

  const keep = "Invites keep their links until they are accepted.";
  if (withLinkCount < invites.length) {
    return `Send each teammate their link. Where there is no link, the invite has to arrive by email. ${keep}`;
  }
  return invites.length === 1
    ? `Copy the link and send it to the teammate it belongs to. ${keep}`
    : `Copy each link and send it to the teammate it belongs to. ${keep}`;
}

export default function InviteLinksResult({
  open,
  invites,
  onClose,
  onInviteMore,
}) {
  const withLinks = invites.filter((invite) => invite.inviteLink);
  // Redundant with the row's own button when there is only one link.
  const showCopyAll = withLinks.length > 1;
  const allInvites = showCopyAll ? formatInvitesForCopy(invites) : "";

  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth="sm"
      fullWidth
      PaperProps={{ sx: { borderRadius: 2, bgcolor: "background.paper" } }}
    >
      <Box sx={{ p: 3 }}>
        <Stack direction="row" alignItems="flex-start">
          <Box sx={{ flex: 1 }}>
            <Typography
              variant="m3"
              fontWeight="fontWeightSemiBold"
              color="text.primary"
            >
              {invites.length === 1
                ? "1 invite created"
                : `${invites.length} invites created`}
            </Typography>
            <Typography
              variant="s1"
              color="text.secondary"
              sx={{ mt: 0.5, display: "block" }}
            >
              {subtitleFor(invites, withLinks.length)}
            </Typography>
          </Box>
          <IconButton onClick={onClose}>
            <Iconify icon="mdi:close" />
          </IconButton>
        </Stack>

        {allInvites && (
          <Stack direction="row" justifyContent="flex-end" sx={{ mt: 2 }}>
            <CopyLinkButton text={allInvites} label="Copy all" />
          </Stack>
        )}

        <Stack spacing={1.25} sx={{ mt: allInvites ? 1.5 : 2.5 }}>
          {invites.map((invite, index) => (
            <Box
              key={`${invite.email}-${index}`}
              sx={{
                p: 1.5,
                borderRadius: 1,
                border: "1px solid",
                borderColor: "divider",
                bgcolor: (t) => alpha(t.palette.common.white, 0.02),
              }}
            >
              <Typography
                variant="s1"
                color="text.primary"
                fontWeight="fontWeightMedium"
                sx={{ mb: 1, display: "block" }}
              >
                {invite.email}
              </Typography>
              {invite.inviteLink ? (
                <Stack direction="row" spacing={1} alignItems="center">
                  <TextField
                    value={invite.inviteLink}
                    size="small"
                    fullWidth
                    InputProps={{
                      readOnly: true,
                      sx: { fontFamily: "monospace" },
                    }}
                  />
                  <CopyLinkButton text={invite.inviteLink} />
                </Stack>
              ) : (
                <Typography variant="s2" color="text.secondary">
                  Invited. No shareable link for this address, so the invite has
                  to arrive by email.
                </Typography>
              )}
            </Box>
          ))}
        </Stack>

        <Stack
          direction="row"
          spacing={1.5}
          justifyContent="flex-end"
          sx={{ mt: 3 }}
        >
          <Button
            size="small"
            variant="outlined"
            color="inherit"
            onClick={onInviteMore}
          >
            Invite more
          </Button>
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={onClose}
          >
            Done
          </Button>
        </Stack>
      </Box>
    </Dialog>
  );
}

InviteLinksResult.propTypes = {
  open: PropTypes.bool,
  invites: PropTypes.arrayOf(
    PropTypes.shape({
      email: PropTypes.string,
      inviteLink: PropTypes.string,
    }),
  ),
  onClose: PropTypes.func,
  onInviteMore: PropTypes.func,
};
