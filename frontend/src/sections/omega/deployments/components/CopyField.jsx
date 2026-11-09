import React, { useEffect, useState } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import IconButton from "@mui/material/IconButton";
import Tooltip from "@mui/material/Tooltip";
import Typography from "@mui/material/Typography";

import DeployIcon from "./DeployIcon";

const visuallyHidden = {
  border: 0,
  clip: "rect(0 0 0 0)",
  height: "1px",
  margin: "-1px",
  overflow: "hidden",
  padding: 0,
  position: "absolute",
  whiteSpace: "nowrap",
  width: "1px",
};

/**
 * A monospace value with a copy button: a URL, a digest or a command. With
 * `secret`, the value is masked until revealed and never logged.
 * C-FA09.failure opts only the access-token instance into recovery via
 * `failureMessage`; URL, digest and command failures stay silent.
 */
export default function CopyField({
  value,
  label,
  secret = false,
  testId,
  wrap = false,
  failureMessage = null,
}) {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);
  const [shown, setShown] = useState(!secret);
  useEffect(() => {
    if (!copied) return undefined;
    const timer = setTimeout(() => setCopied(false), 1600);
    return () => clearTimeout(timer);
  }, [copied]);

  const copy = async () => {
    try {
      if (
        window.isSecureContext === false ||
        typeof navigator.clipboard?.writeText !== "function"
      ) {
        throw new Error();
      }
      await navigator.clipboard.writeText(value);
      setFailed(false);
      setCopied(true);
    } catch {
      setCopied(false);
      setFailed(Boolean(failureMessage));
      if (failureMessage) setShown(true);
    }
  };

  return (
    <Box
      data-testid={testId}
      sx={{
        display: "flex",
        ...(failureMessage && { flexWrap: "wrap" }),
        alignItems: "center",
        gap: 0.5,
        minWidth: 0,
        pl: 1.25,
        pr: 0.5,
        minHeight: 36,
        borderRadius: 0.75,
        border: "1px solid",
        borderColor: "divider",
        bgcolor: "background.neutral",
      }}
    >
      <Box
        component="code"
        aria-label={label}
        sx={{
          flex: 1,
          minWidth: 0,
          py: 0.75,
          fontFamily: "monospace",
          fontSize: 13,
          color: "text.primary",
          ...(failed && { userSelect: "all" }),
          ...(wrap || failed
            ? { whiteSpace: "pre-wrap", overflowWrap: "anywhere" }
            : {
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }),
        }}
      >
        {shown ? value : "•".repeat(Math.min(32, value.length))}
      </Box>
      {secret && (
        <Button2
          label={shown ? "Hide" : "Show"}
          onClick={() => setShown((v) => !v)}
        />
      )}
      <Tooltip title={copied ? "Copied" : `Copy ${label || ""}`.trim()}>
        <IconButton
          size="small"
          onClick={copy}
          aria-label={`Copy ${label || "value"}`}
        >
          <DeployIcon name={copied ? "check" : "copy"} size={16} />
        </IconButton>
      </Tooltip>
      <Typography
        component="span"
        typography="body_sm"
        color="status.danger.fg"
        aria-live="polite"
        sx={failed ? { flexBasis: "100%", pb: 0.75 } : visuallyHidden}
      >
        {failed ? failureMessage : copied ? "Copied" : ""}
      </Typography>
    </Box>
  );
}

function Button2({ label, onClick }) {
  return (
    <Box
      component="button"
      type="button"
      onClick={onClick}
      sx={{
        border: 0,
        bgcolor: "transparent",
        color: "text.secondary",
        typography: "s2",
        fontWeight: 500,
        cursor: "pointer",
        px: 0.75,
        "&:hover": { color: "text.primary" },
      }}
    >
      {label}
    </Box>
  );
}

Button2.propTypes = { label: PropTypes.string, onClick: PropTypes.func };

CopyField.propTypes = {
  value: PropTypes.string.isRequired,
  label: PropTypes.string,
  secret: PropTypes.bool,
  testId: PropTypes.string,
  wrap: PropTypes.bool,
  failureMessage: PropTypes.string,
};
