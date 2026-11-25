import PropTypes from "prop-types";
import { Box, IconButton, Stack, Tooltip, Typography } from "@mui/material";
import { enqueueSnackbar } from "notistack";
import Iconify from "src/components/iconify";
import { CODE_BLOCK_COPY, MONO } from "./world.constants";

export default function CodeBlock({ file, code }) {
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
        borderTop: "1px solid",
        borderColor: "divider",
        bgcolor: "background.neutral",
        minWidth: 0,
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 2.5,
          py: 0.75,
          bgcolor: "action.hover",
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Iconify
          icon="solar:file-text-linear"
          width={12}
          sx={{ color: "text.tertiary", flexShrink: 0 }}
        />
        <Typography
          noWrap
          sx={{
            typography: "s3",
            fontFamily: MONO,
            color: "text.secondary",
            flex: 1,
            minWidth: 0,
          }}
        >
          {file}
        </Typography>
        <Tooltip arrow title={CODE_BLOCK_COPY.copyCode}>
          <IconButton
            size="small"
            onClick={copy}
            aria-label={CODE_BLOCK_COPY.copyCode}
          >
            <Iconify
              icon="solar:copy-linear"
              width={12}
              sx={{ color: "text.tertiary" }}
            />
          </IconButton>
        </Tooltip>
      </Stack>
      <Box sx={{ px: 2.5, py: 1.5, maxHeight: 360, overflow: "auto" }}>
        <Typography
          component="pre"
          sx={{
            typography: "s3",
            fontFamily: MONO,
            lineHeight: 1.6,
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
  file: PropTypes.string,
  code: PropTypes.string.isRequired,
};
