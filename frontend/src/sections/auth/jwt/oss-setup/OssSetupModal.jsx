import PropTypes from "prop-types";

import Link from "@mui/material/Link";
import Stack from "@mui/material/Stack";
import Dialog from "@mui/material/Dialog";
import Divider from "@mui/material/Divider";
import IconButton from "@mui/material/IconButton";
import Typography from "@mui/material/Typography";
import DialogTitle from "@mui/material/DialogTitle";
import DialogContent from "@mui/material/DialogContent";

import Iconify from "src/components/iconify";

import CommandBlock from "./CommandBlock";
import {
  OSS_RESET_DOC_URL,
  RESET_SHELL_CMD,
  RESET_PYTHON_SNIPPET,
} from "./constants";

function StepLabel({ children }) {
  return (
    <Typography
      variant="s2"
      fontWeight="fontWeightSemiBold"
      color="text.primary"
    >
      {children}
    </Typography>
  );
}

StepLabel.propTypes = { children: PropTypes.node };

// Password-reset fallback for self-hosted installs that cannot send email.
// Account creation is NOT here: signup works in the browser on OSS, so there is
// nothing to do from a shell.
export default function OssSetupModal({ open, onClose }) {
  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="sm">
      <DialogTitle sx={{ pb: 1 }}>
        <Stack
          direction="row"
          alignItems="flex-start"
          justifyContent="space-between"
          spacing={2}
        >
          <Stack spacing={0.5}>
            <Typography variant="m3" fontWeight="fontWeightSemiBold">
              Reset your password
            </Typography>
            <Typography variant="s2" color="text.secondary">
              This installation can&apos;t send email, so a reset link
              can&apos;t be delivered. Generate a sign-in link from the CLI on
              the machine running FutureAGI.
            </Typography>
          </Stack>
          <IconButton
            size="small"
            onClick={onClose}
            sx={{ mt: -0.5, mr: -0.5 }}
          >
            <Iconify icon="mdi:close" width={20} />
          </IconButton>
        </Stack>
      </DialogTitle>

      <DialogContent sx={{ pt: 3 }}>
        <Stack spacing={2}>
          <StepLabel>1. Open a Django shell:</StepLabel>
          <CommandBlock command={RESET_SHELL_CMD} />

          <StepLabel>2. Generate a sign-in link:</StepLabel>
          <CommandBlock command={RESET_PYTHON_SNIPPET} />

          <Typography variant="s1" color="text.secondary">
            This prints a link that signs the user in. Treat it like a password,
            share it privately, and open it promptly to set a new password.
          </Typography>

          <Typography variant="s2" color="text.secondary">
            No shell access? Ask whoever administers this installation to run
            these and send you the link.
          </Typography>
        </Stack>
      </DialogContent>

      <Divider />

      <Stack
        direction="row"
        alignItems="center"
        justifyContent="flex-end"
        sx={{ px: 3, py: 2 }}
      >
        <Link
          href={OSS_RESET_DOC_URL}
          target="_blank"
          rel="noopener"
          variant="s2"
          underline="hover"
        >
          Password reset guide
          <Iconify
            icon="mdi:open-in-new"
            width={14}
            sx={{ ml: 0.5, verticalAlign: "middle" }}
          />
        </Link>
      </Stack>
    </Dialog>
  );
}

OssSetupModal.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
};
