import PropTypes from "prop-types";
import { enqueueSnackbar } from "notistack";
import { Box, Button, Dialog, Stack, Typography } from "@mui/material";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import { UI_ONLY_SAVED } from "../gaps/NeedsInputButton";

/**
 * Vel's dialog for an environment's default agent version: production (it
 * moves with each new production version) or one version held. Nothing runs;
 * the next run uses it and earlier runs keep their version. A default version
 * per environment has no API yet, so confirming is UI only and changes
 * nothing.
 */
export default function SwitchVersionDialog({
  open,
  onClose,
  envName,
  label,
  follows = false,
}) {
  if (!label) return null;
  const where = envName || "this environment";
  const confirm = () => {
    onClose?.();
    enqueueSnackbar(UI_ONLY_SAVED, { variant: "info" });
  };
  return (
    <Dialog
      open={!!open}
      onClose={onClose}
      PaperProps={{
        sx: {
          borderRadius: 2,
          maxWidth: 560,
          width: "100%",
          bgcolor: "background.paper",
          backgroundImage: "none",
          border: "1px solid",
          borderColor: "divider",
        },
      }}
    >
      <Box sx={{ px: 3, pt: 2.5, pb: 2 }}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <Typography sx={{ typography: "m3", fontWeight: 600 }}>
            {follows
              ? `Make production the default in ${where}?`
              : `Make ${label} the default in ${where}?`}
          </Typography>
          <UiOnlyFlag feature="agentVersionDefault" />
        </Stack>
        <Typography
          sx={{ typography: "s2", color: "text.secondary", mt: 0.75 }}
        >
          {follows
            ? `It tests ${label}, the production version, and follows each new one from now on. Nothing runs now; earlier runs keep their version.`
            : `It stays on ${label}. New production versions won't move it. Nothing runs now; earlier runs keep their version.`}
        </Typography>
      </Box>
      <Stack
        direction="row"
        justifyContent="flex-end"
        spacing={1}
        sx={{ px: 3, py: 1.75, borderTop: "1px solid", borderColor: "divider" }}
      >
        <Button size="small" onClick={onClose} sx={{ color: "text.secondary" }}>
          Cancel
        </Button>
        <Button
          size="small"
          variant="contained"
          color="primary"
          onClick={confirm}
        >
          {follows ? "Default to production" : `Make ${label} the default`}
        </Button>
      </Stack>
    </Dialog>
  );
}

SwitchVersionDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  envName: PropTypes.string,
  // The version it would test: the production one when `follows`.
  label: PropTypes.string,
  // Default to production (moves with it) rather than hold `label`.
  follows: PropTypes.bool,
};
