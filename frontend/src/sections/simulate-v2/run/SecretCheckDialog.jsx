import PropTypes from "prop-types";
import { Box, Button, Dialog, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

const MONO = "ui-monospace, Menlo, monospace";

/**
 * Asked before a run when a secret it resolves failed its access check.
 *
 * It warns rather than blocks: the calls that need that key fail, those
 * conversations come back as errors — never as the agent's failures — and
 * running the rest may be exactly what someone wants. Fix it in Settings.
 */
export default function SecretCheckDialog({
  open,
  failed = [],
  onClose,
  onRun,
  onFix,
}) {
  if (!open || !failed.length) return null;
  const n = failed.length;
  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{
        sx: {
          width: 560,
          maxWidth: "calc(100% - 32px)",
          p: 3,
          borderRadius: 2,
          backgroundImage: "none",
        },
      }}
    >
      <Box
        sx={{
          width: 36,
          height: 36,
          borderRadius: 1,
          display: "grid",
          placeItems: "center",
          mb: 1.75,
          bgcolor: "action.hover",
          color: "text.primary",
        }}
      >
        <Iconify icon="solar:lock-keyhole-minimalistic-linear" width={20} />
      </Box>
      <Typography sx={{ typography: "m3", fontWeight: 600 }}>
        {n === 1
          ? "A secret failed its access check"
          : `${n} secrets failed their access check`}
      </Typography>
      <Typography sx={{ typography: "s2", color: "text.secondary", mt: 1 }}>
        Calls that need {n === 1 ? "it" : "them"} will fail, so those
        conversations come back as errors — they count neither for nor against
        the agent. The rest of the run still tests it.
      </Typography>
      <Stack
        spacing={0.5}
        sx={{ mt: 2, p: 1.5, borderRadius: 1, bgcolor: "action.hover" }}
      >
        {failed.map((f) => (
          <Stack
            key={f.id}
            direction="row"
            spacing={1.5}
            alignItems="baseline"
            sx={{ minWidth: 0 }}
          >
            <Typography
              noWrap
              sx={{
                typography: "s3",
                fontFamily: MONO,
                fontWeight: 600,
                flexShrink: 0,
              }}
            >
              {f.id}
            </Typography>
            <Typography
              noWrap
              sx={{ typography: "s3", color: "text.tertiary", minWidth: 0 }}
            >
              {f.line || "Access check failed"}
            </Typography>
          </Stack>
        ))}
      </Stack>
      <Stack direction="row" alignItems="center" spacing={1} sx={{ mt: 2.5 }}>
        <Button size="small" onClick={onClose} sx={{ color: "text.secondary" }}>
          Cancel
        </Button>
        <Box flex={1} />
        <Button
          size="small"
          variant="outlined"
          onClick={onRun}
          sx={{
            color: "text.primary",
            borderColor: "divider",
            "&:hover": {
              borderColor: "text.disabled",
              bgcolor: "action.hover",
            },
          }}
        >
          Run anyway
        </Button>
        {onFix && (
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={onFix}
          >
            Fix in Settings
          </Button>
        )}
      </Stack>
    </Dialog>
  );
}

SecretCheckDialog.propTypes = {
  open: PropTypes.bool,
  failed: PropTypes.array,
  onClose: PropTypes.func.isRequired,
  onRun: PropTypes.func.isRequired,
  onFix: PropTypes.func,
};
