import PropTypes from "prop-types";

import Tab from "@mui/material/Tab";
import Link from "@mui/material/Link";
import Tabs from "@mui/material/Tabs";
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
  OSS_DOC_URL,
  OSS_SETUP_TABS,
  CREATE_USER_CMD,
  RESET_SHELL_CMD,
  RESET_PYTHON_SNIPPET,
  CREATE_USER_CMD_NONINTERACTIVE,
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

export default function OssSetupModal({
  open,
  onClose,
  activeTab,
  onTabChange,
}) {
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
              Set up your account
            </Typography>
            <Typography variant="s2" color="text.secondary">
              Email and social sign-in aren&apos;t available in self-hosted
              mode. Manage accounts from the CLI on the machine running
              FutureAGI.
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

      <Tabs
        value={activeTab}
        onChange={(_e, value) => onTabChange(value)}
        sx={{ px: 3, borderBottom: 1, borderColor: "divider" }}
      >
        <Tab value={OSS_SETUP_TABS.CREATE} label="Create account" />
        <Tab value={OSS_SETUP_TABS.RESET} label="Reset password" />
      </Tabs>

      <DialogContent sx={{ pt: 3 }}>
        {activeTab === OSS_SETUP_TABS.CREATE && (
          <Stack spacing={2}>
            <StepLabel>Run this to create a user:</StepLabel>
            <CommandBlock command={CREATE_USER_CMD} />

            <Typography variant="s2" color="text.secondary">
              Or create it non-interactively:
            </Typography>
            <CommandBlock command={CREATE_USER_CMD_NONINTERACTIVE} />

            <Typography variant="s1" color="text.secondary">
              Then close this and log in with those credentials.
            </Typography>
          </Stack>
        )}

        {activeTab === OSS_SETUP_TABS.RESET && (
          <Stack spacing={2}>
            <StepLabel>1. Open a Django shell:</StepLabel>
            <CommandBlock command={RESET_SHELL_CMD} />

            <StepLabel>2. Generate a sign-in link:</StepLabel>
            <CommandBlock command={RESET_PYTHON_SNIPPET} />

            <Typography variant="s1" color="text.secondary">
              This prints a link that signs the user in. Treat it like a
              password, share it privately, and open it promptly to set a new
              password.
            </Typography>
          </Stack>
        )}
      </DialogContent>

      <Divider />

      <Stack
        direction="row"
        alignItems="center"
        justifyContent="flex-end"
        sx={{ px: 3, py: 2 }}
      >
        <Link
          href={OSS_DOC_URL[activeTab]}
          target="_blank"
          rel="noopener"
          variant="s2"
          underline="hover"
        >
          {activeTab === OSS_SETUP_TABS.RESET
            ? "Password reset guide"
            : "Account setup guide"}
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
  activeTab: PropTypes.string,
  onTabChange: PropTypes.func,
};
