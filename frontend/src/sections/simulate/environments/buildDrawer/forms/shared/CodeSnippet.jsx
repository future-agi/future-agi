import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import { Box, IconButton } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { copyToClipboard } from "src/utils/utils";
import { T12 } from "../../drawerStyles";
import { MONO } from "../formBits";

const COPIED_RESET_MS = 1600;

/** A few lines of code to copy: the command or the wrapper the person pastes. */
export default function CodeSnippet({ code }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);

  const copy = async () => {
    if (!(await copyToClipboard(code))) return;
    setCopied(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), COPIED_RESET_MS);
  };
  const label = copied ? "Copied" : "Copy";

  return (
    <Box
      sx={{
        position: "relative",
        border: "1px solid",
        borderColor: "divider",
        borderRadius: "6px",
        bgcolor: "background.neutral",
      }}
    >
      <Box
        component="pre"
        sx={{
          m: 0,
          px: 1.5,
          py: 1.125,
          pr: 5,
          overflowX: "auto",
          ...T12,
          fontFamily: MONO,
          color: "text.primary",
          whiteSpace: "pre",
        }}
      >
        {code}
      </Box>
      <CustomTooltip show arrow size="small" title={label}>
        <IconButton
          size="small"
          aria-label={label}
          onClick={copy}
          sx={{ position: "absolute", top: 6, right: 6, p: 0.5 }}
        >
          <Iconify
            icon={copied ? "solar:check-read-linear" : "solar:copy-linear"}
            width={16}
            sx={{ color: copied ? "text.primary" : "text.secondary" }}
          />
        </IconButton>
      </CustomTooltip>
    </Box>
  );
}
CodeSnippet.propTypes = { code: PropTypes.string.isRequired };
