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

// Shown after a successful invite when email can't reach the invitee. The
// invite itself is created either way — this is purely about handing the admin
// something they can pass on.
export default function InviteLinksResult({
  open,
  invites,
  onClose,
  onInviteMore,
}) {
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
              Invites created
            </Typography>
            <Typography
              variant="s1"
              color="text.secondary"
              sx={{ mt: 0.5, display: "block" }}
            >
              Copy each link and send it to the teammate it belongs to.
            </Typography>
          </Box>
          <IconButton onClick={onClose}>
            <Iconify icon="mdi:close" />
          </IconButton>
        </Stack>

        <Stack spacing={1.25} sx={{ mt: 2.5 }}>
          {invites.map((invite) => (
            <Box
              key={invite.email}
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
                  <CopyLinkButton link={invite.inviteLink} />
                </Stack>
              ) : (
                <Typography variant="s2" color="text.secondary">
                  Invited. This server didn&apos;t return a shareable link, so
                  the invite has to arrive by email.
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
