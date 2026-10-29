import PropTypes from "prop-types";
import { useState } from "react";
import { IconButton, Tooltip } from "@mui/material";
import Iconify from "src/components/iconify";

/**
 * Copies an ID the CLI and SDK take (a run, an experiment) — the same ID on
 * every surface. The tooltip says what it is and where it's used.
 */
export default function CopyIdButton({ id, what, usedBy }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard?.writeText(id).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <Tooltip
      arrow
      title={
        copied
          ? "Copied"
          : `Copy ${what} · ${id}${usedBy ? ` — ${usedBy}` : ""}`
      }
    >
      <IconButton
        size="small"
        aria-label={`Copy ${what}`}
        onClick={copy}
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          flexShrink: 0,
        }}
      >
        <Iconify
          icon={copied ? "solar:check-circle-linear" : "solar:copy-linear"}
          width={16}
        />
      </IconButton>
    </Tooltip>
  );
}

CopyIdButton.propTypes = {
  id: PropTypes.string.isRequired,
  what: PropTypes.string.isRequired,
  usedBy: PropTypes.string,
};
