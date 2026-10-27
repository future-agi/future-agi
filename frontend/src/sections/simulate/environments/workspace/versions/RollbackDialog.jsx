import PropTypes from "prop-types";
import { Box, Button, Dialog, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import {
  SAMPLE_NEXT_ENV_VERSION,
  SAMPLE_ROLLBACK_LINES,
} from "src/api/simulate-environments/_fixtures/detail/versions";
import UiOnlyFlag from "../../components/UiOnlyFlag";

// Vel's "Roll back to environment vN?": rolling back makes the next version as
// the older one was, and says what that does. Sample only; confirming saves
// nothing.
export default function RollbackDialog({ target, onClose, onConfirm }) {
  const lines = SAMPLE_ROLLBACK_LINES[target] || [];
  return (
    <Dialog
      open={!!target}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { width: 480, p: 2.5 } }}
    >
      <Stack spacing={1.25}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <Typography
            sx={{ typography: "m3", fontWeight: 600 }}
          >{`Roll back to environment ${target}?`}</Typography>
          <UiOnlyFlag feature="environmentVersions" />
        </Stack>
        <Typography sx={{ typography: "s2", color: "text.secondary" }}>
          {`This makes environment ${SAMPLE_NEXT_ENV_VERSION} as it was in ${target}. Nothing is deleted.`}
        </Typography>
        <Stack
          spacing={0.5}
          sx={{ p: 1.5, borderRadius: 1, bgcolor: "background.neutral" }}
        >
          {lines.map((line) => (
            <Stack
              key={line}
              direction="row"
              spacing={1}
              alignItems="flex-start"
            >
              <Box
                sx={{
                  width: 4,
                  height: 4,
                  borderRadius: "50%",
                  bgcolor: "text.subtitle",
                  mt: "8px",
                  flexShrink: 0,
                }}
              />
              <Typography sx={{ typography: "s3", color: "text.secondary" }}>
                {line}
              </Typography>
            </Stack>
          ))}
        </Stack>
        <Stack direction="row" alignItems="center" spacing={1} sx={{ pt: 0.5 }}>
          <Button
            size="small"
            onClick={onClose}
            sx={{ color: "text.secondary" }}
          >
            Cancel
          </Button>
          <Box flex={1} />
          <Button
            size="small"
            variant="contained"
            color="primary"
            onClick={onConfirm}
            startIcon={<Iconify icon="solar:history-linear" width={14} />}
          >
            {`Roll back to ${target}`}
          </Button>
        </Stack>
      </Stack>
    </Dialog>
  );
}

RollbackDialog.propTypes = {
  target: PropTypes.string,
  onClose: PropTypes.func,
  onConfirm: PropTypes.func,
};
