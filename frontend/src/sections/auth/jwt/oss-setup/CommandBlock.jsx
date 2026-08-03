import PropTypes from "prop-types";

import Box from "@mui/material/Box";
import Tooltip from "@mui/material/Tooltip";
import IconButton from "@mui/material/IconButton";
import Typography from "@mui/material/Typography";

import Iconify from "src/components/iconify";
import { useSnackbar } from "src/components/snackbar";
import { copyToClipboard } from "src/utils/utils";

const TERMINAL_BG = "#0D1117";

export default function CommandBlock({ command }) {
  const { enqueueSnackbar } = useSnackbar();

  const handleCopy = async () => {
    const ok = await copyToClipboard(command);
    enqueueSnackbar(ok ? "Copied to clipboard" : "Copy failed", {
      variant: ok ? "success" : "error",
    });
  };

  return (
    <Box
      sx={{
        position: "relative",
        bgcolor: TERMINAL_BG,
        borderRadius: 1,
        py: 1.5,
        pl: 2,
        pr: 5,
        overflowX: "auto",
      }}
    >
      <Typography
        component="pre"
        variant="s2"
        sx={{
          m: 0,
          fontFamily: "monospace",
          whiteSpace: "pre",
          color: "grey.300",
        }}
      >
        {command}
      </Typography>

      <Tooltip title="Copy">
        <IconButton
          size="small"
          onClick={handleCopy}
          sx={{
            position: "absolute",
            top: (theme) => theme.spacing(0.5),
            right: (theme) => theme.spacing(0.5),
            color: "grey.500",
            "&:hover": { color: "grey.100" },
          }}
        >
          <Iconify icon="ph:copy" width={16} />
        </IconButton>
      </Tooltip>
    </Box>
  );
}

CommandBlock.propTypes = {
  command: PropTypes.string.isRequired,
};
