import PropTypes from "prop-types";
import { enqueueSnackbar } from "notistack";
import { Box, Button, Dialog, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import { UI_ONLY_SAVED } from "../gaps/NeedsInputButton";

/**
 * Vel's Sync agent dialog: re-read the agent's source and, if anything
 * changed, save it as the agent's next version. The environment and its
 * scenarios never change because the agent did. Re-reading a source has no
 * API yet, so the dialog shows the steps a sync takes and Sync now is UI only.
 * Vel's results (what changed, the nudges raised) would describe this real
 * agent, so they aren't shown.
 */
export default function AgentSyncDialog({
  open,
  onClose,
  name,
  sourceLabel,
  version,
}) {
  const steps = [
    sourceLabel ? `Connecting to ${sourceLabel}` : "Connecting to the source",
    "Reading the agent's config",
    `Comparing with ${version || "the current version"}`,
  ];
  const sync = () => {
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
      <Box sx={{ p: 3 }}>
        <Stack
          direction="row"
          alignItems="flex-start"
          spacing={1.25}
          sx={{ mb: 2 }}
        >
          <Box
            sx={{
              width: 30,
              height: 30,
              borderRadius: 1,
              flexShrink: 0,
              display: "grid",
              placeItems: "center",
              bgcolor: "action.selected",
              color: "text.secondary",
            }}
          >
            <Iconify icon="solar:cloud-linear" width={16} />
          </Box>
          <Box flex={1} minWidth={0}>
            <Stack direction="row" alignItems="center" spacing={1}>
              <Typography sx={{ typography: "m3", fontWeight: 600 }}>
                {`Sync ${name || "the agent"}`}
              </Typography>
              <UiOnlyFlag feature="syncAgent" />
            </Stack>
            <Typography
              sx={{ typography: "s2", color: "text.subtitle", mt: 0.25 }}
            >
              {`Checks ${sourceLabel || "the source"} for anything that changed since ${version || "the current version"}. A change is saved as the agent's next version only. The environment and its scenarios stay as they are.`}
            </Typography>
          </Box>
        </Stack>

        <Stack
          spacing={1}
          sx={{
            p: 2,
            borderRadius: "8px",
            border: "1px solid",
            borderColor: "divider",
            bgcolor: "background.neutral",
          }}
        >
          {steps.map((s) => (
            <Stack key={s} direction="row" alignItems="center" spacing={1}>
              <Box
                sx={{
                  width: 14,
                  height: 14,
                  display: "grid",
                  placeItems: "center",
                  flexShrink: 0,
                }}
              >
                <Box
                  sx={{
                    width: 5,
                    height: 5,
                    borderRadius: "50%",
                    bgcolor: "text.disabled",
                  }}
                />
              </Box>
              <Typography sx={{ typography: "s2", color: "text.secondary" }}>
                {s}
              </Typography>
            </Stack>
          ))}
        </Stack>

        <Stack
          direction="row"
          justifyContent="flex-end"
          spacing={1}
          sx={{ mt: 2.5 }}
        >
          <Button
            size="small"
            onClick={onClose}
            sx={{ color: "text.secondary" }}
          >
            Cancel
          </Button>
          <Button
            variant="contained"
            color="primary"
            size="small"
            onClick={sync}
          >
            Sync now
          </Button>
        </Stack>
      </Box>
    </Dialog>
  );
}

AgentSyncDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  name: PropTypes.string,
  // Where the agent is read from: "Retell".
  sourceLabel: PropTypes.string,
  // The version a sync compares with.
  version: PropTypes.string,
};
