import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, IconButton } from "@mui/material";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { notify } from "./toast";

export const COPIED_MS = 1500;

/** Copy text; resolves true on success. */
export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(String(text ?? ""));
    return true;
  } catch {
    try {
      const ta = document.createElement("textarea");
      ta.value = String(text ?? "");
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      ta.remove();
      return ok;
    } catch {
      return false;
    }
  }
}

/** `[copied, flash]`: flash() shows the check for 1.5 s. */
export function useCopiedFlash() {
  const [copied, setCopied] = useState(false);
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);
  const flash = () => {
    setCopied(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), COPIED_MS);
  };
  return [copied, flash];
}

/**
 * CopyButton — copy a value with in-place feedback: `mdi:content-copy` →
 * green `mdi:check` for 1.5 s, tooltip `Copy` → `Copied`, announced politely.
 * No toast unless `toast` (then `notify(toast)`, deduped).
 *
 * Props (frozen):
 * @param {string|(() => string)} props.value  the text (or a getter)
 * @param {string} [props.tooltip="Copy"]  what is copied (`Copy output`)
 * @param {string} [props.toast]  also toast this message
 * @param {"small"|"medium"} [props.size="small"]
 * @param {number} [props.iconSize=16]
 * @param {Function} [props.onCopied]
 * @param {object} [props.sx]
 */
export default function CopyButton({
  value,
  tooltip = "Copy",
  toast,
  size = "small",
  iconSize = 16,
  onCopied,
  sx,
}) {
  const [copied, flash] = useCopiedFlash();
  const onClick = async (e) => {
    e.stopPropagation();
    const text = typeof value === "function" ? value() : value;
    const ok = await copyText(text);
    if (!ok) {
      notify("Couldn't copy. Your browser blocked the clipboard.", {
        variant: "error",
        key: "copy-failed",
      });
      return;
    }
    flash();
    if (toast) notify(toast, { variant: "success", key: `copy:${toast}` });
    onCopied?.(text);
  };
  return (
    <CustomTooltip
      show
      size="small"
      placement="top"
      title={copied ? "Copied" : tooltip}
    >
      <IconButton
        size={size}
        aria-label={tooltip}
        onClick={onClick}
        sx={{
          color: copied ? "green.600" : "text.secondary",
          "&:hover": { color: copied ? "green.600" : "text.primary" },
          ...sx,
        }}
      >
        <Iconify
          icon={copied ? "mdi:check" : "mdi:content-copy"}
          width={iconSize}
        />
        <Box
          component="span"
          aria-live="polite"
          sx={{
            position: "absolute",
            width: 1,
            height: 1,
            overflow: "hidden",
            clip: "rect(0 0 0 0)",
          }}
        >
          {copied ? "Copied" : ""}
        </Box>
      </IconButton>
    </CustomTooltip>
  );
}

CopyButton.propTypes = {
  value: PropTypes.oneOfType([PropTypes.string, PropTypes.func]),
  tooltip: PropTypes.string,
  toast: PropTypes.string,
  size: PropTypes.oneOf(["small", "medium"]),
  iconSize: PropTypes.number,
  onCopied: PropTypes.func,
  sx: PropTypes.object,
};
