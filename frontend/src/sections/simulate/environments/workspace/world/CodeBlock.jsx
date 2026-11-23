import PropTypes from "prop-types";
import { Box, IconButton, Stack, Tooltip, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { CODE_BLOCK_COPY, MONO } from "./world.constants";

export default function CodeBlock({ label, file, code }) {
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      enqueueSnackbar(CODE_BLOCK_COPY.copied, { variant: "info" });
    } catch {
      enqueueSnackbar(CODE_BLOCK_COPY.copyFailed, { variant: "error" });
    }
  };
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "6px",
        bgcolor: "background.paper",
        overflow: "hidden",
        minWidth: 0,
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          pl: 1.5,
          pr: 0.5,
          py: 0.25,
          borderBottom: "1px solid",
          borderColor: "divider",
          bgcolor: "background.neutral",
        }}
      >
        <Iconify
          icon="solar:file-text-linear"
          width={14}
          sx={{ color: "text.tertiary", flexShrink: 0 }}
        />
        <Typography sx={{ typography: "s3", fontWeight: 500, flexShrink: 0 }}>
          {label}
        </Typography>
        <Typography
          noWrap
          sx={{
            typography: "s3",
            fontFamily: MONO,
            color: "text.tertiary",
            flex: 1,
            minWidth: 0,
          }}
        >
          {file}
        </Typography>
        <Tooltip arrow title={CODE_BLOCK_COPY.copy(label)}>
          <IconButton
            size="small"
            onClick={copy}
            aria-label={CODE_BLOCK_COPY.copy(label)}
          >
            <Iconify
              icon="solar:copy-linear"
              width={14}
              sx={{ color: "text.secondary" }}
            />
          </IconButton>
        </Tooltip>
      </Stack>
      <Box sx={{ px: 1.5, py: 1.25, maxHeight: 360, overflow: "auto" }}>
        <Typography
          component="pre"
          sx={{
            typography: "s3",
            fontFamily: MONO,
            color: "text.primary",
            whiteSpace: "pre",
            m: 0,
          }}
        >
          {code}
        </Typography>
      </Box>
    </Box>
  );
}

CodeBlock.propTypes = {
  label: PropTypes.string.isRequired,
  file: PropTypes.string,
  code: PropTypes.string.isRequired,
};
