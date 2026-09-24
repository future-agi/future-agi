import PropTypes from "prop-types";
import { useEffect, useRef, useState } from "react";
import {
  IconButton, Stack, Tooltip, Typography,
} from "@mui/material";
import Iconify from "src/components/iconify";
import { copyToClipboard } from "src/utils/utils";

const COPIED_RESET_MS = 1500;
const MONO_STACK = "ui-monospace, SFMono-Regular, Menlo, monospace";

/**
 * A mono, read-only field that shows a value in a static bordered box with a
 * copy button. The button flips to a tick for a moment after a successful copy,
 * then resets. Used for the local-scaffold CLI steps.
 *
 * The value renders in a Typography (not an input) so the mono text sits at the
 * theme's s2 size — a MUI TextField input renders larger than the shorthand asks
 * for, which reads as oversized next to the surrounding copy.
 */
export default function CopyField({ value, wrap = false }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef(null);

  useEffect(() => () => clearTimeout(timer.current), []);

  // `navigator.clipboard` is undefined on insecure origins, where
  // `navigator.clipboard?.writeText?.(value)` resolves to undefined rather than
  // throwing — which used to flip the tick on for a copy that never happened.
  // copyToClipboard reports whether the value actually landed (falling back to
  // execCommand), and only a true flips the tick. On a failure the value stays
  // on screen to be copied by hand, with no false confirmation.
  const handleCopy = async () => {
    const copiedOk = await copyToClipboard(value);
    if (!copiedOk) return;
    setCopied(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), COPIED_RESET_MS);
  };

  const label = copied ? "Copied" : "Copy to clipboard";

  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={1}
      sx={{
        px: 1.5, py: 1, borderRadius: 1,
        border: "1px solid", borderColor: "divider",
        bgcolor: "background.neutral",
      }}
    >
      <Typography
        noWrap={!wrap}
        sx={{
          flex: 1, minWidth: 0,
          typography: "s2",
          fontFamily: MONO_STACK,
          color: "text.primary",
          ...(wrap && { wordBreak: "break-all" }),
        }}
      >
        {value}
      </Typography>
      <Tooltip title={label} arrow>
        <IconButton aria-label={label} size="small" onClick={handleCopy} sx={{ p: 0.5 }}>
          <Iconify
            icon={copied ? "solar:check-read-linear" : "solar:copy-linear"}
            width={15}
            sx={{ color: copied ? "success.main" : "text.subtitle" }}
          />
        </IconButton>
      </Tooltip>
    </Stack>
  );
}
CopyField.propTypes = {
  value: PropTypes.string.isRequired,
  wrap: PropTypes.bool,
};
