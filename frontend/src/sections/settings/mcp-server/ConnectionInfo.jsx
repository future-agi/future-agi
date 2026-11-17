import { useRef } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Chip,
  IconButton,
  InputAdornment,
  TextField,
  Tooltip,
  Typography,
  useTheme,
} from "@mui/material";
import Section from "src/components/section";
import Iconify from "src/components/iconify";
import { enqueueSnackbar } from "notistack";
import { copyToClipboard } from "src/utils/utils";

ConnectionInfo.propTypes = {
  config: PropTypes.object,
};

export default function ConnectionInfo({ config }) {
  const theme = useTheme();
  const urlInputRef = useRef(null);

  const mcpUrl = config?.mcp_url || "";

  // Call straight from the click handler: the fallback copy only works while
  // the browser still treats the click as the user's action.
  const handleCopy = async (text, label) => {
    if (await copyToClipboard(text)) {
      enqueueSnackbar(`${label} copied to clipboard`, { variant: "success" });
      return;
    }
    // Select the URL so copying it by hand is one keystroke.
    urlInputRef.current?.focus();
    urlInputRef.current?.select();
    enqueueSnackbar(
      "Could not copy. Press Ctrl+C (Cmd+C on Mac) to copy the selected text.",
      { variant: "error" },
    );
  };

  return (
    <Section variant="panel" sx={{ p: theme.spacing(3), mb: theme.spacing(3) }}>
      <Typography
        sx={{
          typography: "m2",
          fontWeight: "fontWeightSemiBold",
          color: "text.primary",
          mb: theme.spacing(2),
        }}
      >
        Connection Details
      </Typography>
      <Typography
        sx={{
          typography: "s1",
          color: "text.secondary",
          mb: theme.spacing(3),
        }}
      >
        Your MCP endpoint URL. Authentication is handled via OAuth — your IDE
        will prompt you to log in on first connection.
      </Typography>

      <Box
        display="flex"
        flexDirection="column"
        gap={theme.spacing(2)}
        maxWidth={560}
      >
        <TextField
          label="MCP Endpoint URL"
          value={mcpUrl}
          fullWidth
          size="small"
          inputRef={urlInputRef}
          InputProps={{
            readOnly: true,
            endAdornment: (
              <InputAdornment position="end">
                <Tooltip title="Copy URL">
                  <IconButton
                    size="small"
                    onClick={() => handleCopy(mcpUrl, "MCP URL")}
                  >
                    <Iconify icon="solar:copy-linear" width={20} />
                  </IconButton>
                </Tooltip>
              </InputAdornment>
            ),
          }}
        />

        <Box display="flex" alignItems="center" gap={1}>
          <Iconify
            icon="solar:shield-check-linear"
            width={20}
            sx={{ color: "success.main" }}
          />
          <Typography sx={{ typography: "s2", color: "text.secondary" }}>
            Authentication: OAuth 2.0
          </Typography>
          <Chip label="Secure" size="small" color="success" variant="soft" />
        </Box>
      </Box>
    </Section>
  );
}
