import PropTypes from "prop-types";
import { enqueueSnackbar } from "notistack";
import { Box, Button, Dialog, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import UiOnlyFlag from "../../components/UiOnlyFlag";
import { SAMPLE_TOOL_FIT } from "src/api/simulate-environments/_fixtures/detail/toolFit";
import { UI_ONLY_SAVED } from "../gaps/NeedsInputButton";

const mono = { fontFamily: "ui-monospace, Menlo, monospace", fontSize: 12.5 };

// Vel's "ask before a run" dialog: the agent calls tools this environment
// can't answer. We can't tell that yet, so it shows only with
// ?uiSample=toolfit, on Vel's sample. Run anyway is a real run; Rebuild and
// run has no API.
export default function RunFitDialog({ open, onClose, onRunAnyway }) {
  const { agent, environment, nextEnvironment, missing } = SAMPLE_TOOL_FIT;
  const n = missing.length;
  return (
    <Dialog
      open={open}
      onClose={onClose}
      maxWidth={false}
      PaperProps={{ sx: { width: 520, p: 2.5 } }}
    >
      <Stack spacing={1.5}>
        <Box
          sx={{
            width: 36,
            height: 36,
            borderRadius: 1,
            display: "grid",
            placeItems: "center",
            bgcolor: "action.hover",
            color: "text.secondary",
          }}
        >
          <Iconify icon="solar:plug-circle-linear" width={20} />
        </Box>
        <Stack direction="row" alignItems="center" spacing={1}>
          <Typography sx={{ typography: "m3", fontWeight: 600 }}>
            {`Agent ${agent} calls ${n === 1 ? "a tool" : `${n} tools`} environment ${environment} can't answer`}
          </Typography>
          <UiOnlyFlag feature="toolFit" />
        </Stack>
        <Typography
          component="div"
          sx={{ typography: "s2", color: "text.secondary", lineHeight: 1.6 }}
        >
          It calls{" "}
          {missing.map((t, i) => (
            <Box component="span" key={t}>
              {i > 0 && (i === n - 1 ? " and " : ", ")}
              <Box component="code" sx={mono}>
                {t}
              </Box>
            </Box>
          ))}
          , which this world doesn&apos;t know how to answer. Scenarios where it
          calls {n === 1 ? "it" : "them"} come back <b>not measured</b>.
        </Typography>
        <Box sx={{ p: 1.5, borderRadius: 1, bgcolor: "background.neutral" }}>
          <Typography sx={{ typography: "s3", color: "text.secondary" }}>
            {`Rebuilding makes environment ${nextEnvironment}: the world learns ${n === 1 ? "the tool" : "the tools"}, and new scenarios are added to test ${n === 1 ? "it" : "them"}. Older environment versions stay as they are.`}
          </Typography>
        </Box>
        <Stack direction="row" spacing={1} alignItems="center" sx={{ pt: 0.5 }}>
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
            variant="outlined"
            onClick={onRunAnyway}
            sx={{ color: "text.primary", borderColor: "divider" }}
          >
            {`Run on environment ${environment} anyway`}
          </Button>
          <Button
            size="small"
            variant="contained"
            color="primary"
            startIcon={<Iconify icon="solar:refresh-linear" width={16} />}
            onClick={() => {
              onClose?.();
              enqueueSnackbar(UI_ONLY_SAVED, { variant: "info" });
            }}
          >
            Rebuild and run
          </Button>
        </Stack>
      </Stack>
    </Dialog>
  );
}

RunFitDialog.propTypes = {
  open: PropTypes.bool,
  onClose: PropTypes.func,
  onRunAnyway: PropTypes.func,
};
