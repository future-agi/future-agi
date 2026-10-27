import PropTypes from "prop-types";
import { enqueueSnackbar } from "notistack";
import { Box, IconButton, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";

const MONO = "ui-monospace, Menlo, monospace";

// Vel's read-only code viewer: the file name, a copy button, the source.
export default function CodeBlock({ file, code }) {
  const copy = () => {
    navigator.clipboard?.writeText(code);
    enqueueSnackbar("Copied to clipboard", { variant: "info" });
  };
  return (
    <Box sx={{ borderTop: "1px solid", borderColor: "divider", bgcolor: "background.neutral" }}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ px: 2.5, py: 0.75, borderBottom: "1px solid", borderColor: "divider", bgcolor: "action.hover" }}
      >
        <Iconify icon="solar:file-text-linear" width={12} sx={{ color: "text.subtitle" }} />
        <Typography sx={{ typography: "s3", fontFamily: MONO, color: "text.secondary", flex: 1 }}>{file}</Typography>
        <CustomTooltip show arrow size="small" title="Copy code">
          <IconButton size="small" onClick={copy} aria-label="Copy code">
            <Iconify icon="solar:copy-linear" width={12} sx={{ color: "text.subtitle" }} />
          </IconButton>
        </CustomTooltip>
      </Stack>
      <Box sx={{ px: 2.5, py: 1.5, maxHeight: 360, overflow: "auto" }}>
        <Typography
          component="pre"
          sx={{ typography: "s3", fontFamily: MONO, color: "text.primary", whiteSpace: "pre", m: 0, lineHeight: 1.6 }}
        >
          {code}
        </Typography>
      </Box>
    </Box>
  );
}
CodeBlock.propTypes = { file: PropTypes.string, code: PropTypes.string };
