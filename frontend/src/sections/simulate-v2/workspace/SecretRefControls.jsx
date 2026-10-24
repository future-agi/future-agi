import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Stack, Typography, Button, TextField } from "@mui/material";
import Iconify from "src/components/iconify";
import { validateSecretRef, storeOfRef } from "../_mock/secretRefs";
import usePreflightPending from "./usePreflightPending";

const MONO = "ui-monospace, Menlo, monospace";

/**
 * Secrets by reference (AC-6.12) — the controls Settings, the credentials
 * manifest and the builder chat share.
 *
 * A secret is pasted as a pointer into the team's secret manager, never as
 * the value. A pasted key is refused with one plain sentence; it never
 * reaches the store.
 */

/** The reference itself, or what stands in for one. */
export function SecretRefText({ value, empty = "not set", sx }) {
  return (
    <Typography
      noWrap
      title={
        value
          ? `${storeOfRef(value)?.label || "Secret manager"} · ${value}`
          : undefined
      }
      sx={{
        typography: "s2",
        fontFamily: MONO,
        color: value ? "text.secondary" : "text.tertiary",
        minWidth: 0,
        ...sx,
      }}
    >
      {value || empty}
    </Typography>
  );
}
SecretRefText.propTypes = {
  value: PropTypes.string,
  empty: PropTypes.string,
  sx: PropTypes.object,
};

/** "Set reference" — a quiet text action, like the panel's other row actions. */
export function SetRefAction({
  onClick,
  label = "Set reference",
  disabled = false,
}) {
  return (
    <Typography
      role="button"
      tabIndex={disabled ? -1 : 0}
      onClick={disabled ? undefined : onClick}
      onKeyDown={(e) => {
        if (!disabled && e.key === "Enter") onClick();
      }}
      sx={{
        typography: "s3",
        fontWeight: 600,
        flexShrink: 0,
        whiteSpace: "nowrap",
        color: disabled ? "text.disabled" : "primary.main",
        cursor: disabled ? "default" : "pointer",
      }}
    >
      {label}
    </Typography>
  );
}
SetRefAction.propTypes = {
  onClick: PropTypes.func,
  label: PropTypes.string,
  disabled: PropTypes.bool,
};

/**
 * The inline editor. Accepts only a reference; a pasted key is refused
 * with the reason and nothing is saved.
 */
export function SecretRefEditor({
  initial = "",
  placeholder,
  onSave,
  onCancel,
  saveLabel = "Save reference",
  autoFocus = true,
}) {
  const [value, setValue] = useState(initial);
  const [error, setError] = useState(null);
  const save = () => {
    const res = validateSecretRef(value);
    if (!res.ok) {
      setError(res.error);
      return;
    }
    setError(null);
    onSave(res.ref);
  };
  return (
    <Stack spacing={0.75}>
      <Stack direction="row" alignItems="flex-start" spacing={1}>
        <TextField
          size="small"
          fullWidth
          autoFocus={autoFocus}
          value={value}
          placeholder={placeholder || "vault://acme/sim/api_key"}
          onChange={(e) => {
            setValue(e.target.value);
            if (error) setError(null);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              save();
            }
            if (e.key === "Escape") onCancel?.();
          }}
          error={!!error}
          inputProps={{
            spellCheck: false,
            autoComplete: "off",
            "aria-label": "Secret reference",
          }}
          sx={{
            flex: 1,
            minWidth: 200,
            "& .MuiInputBase-root": { typography: "s2", fontFamily: MONO },
          }}
        />
        <Button
          size="small"
          variant="outlined"
          onClick={save}
          disabled={!value.trim()}
          sx={{ flexShrink: 0, color: "text.primary", borderColor: "divider" }}
        >
          {saveLabel}
        </Button>
        {onCancel && (
          <Button
            size="small"
            onClick={onCancel}
            sx={{ flexShrink: 0, color: "text.secondary" }}
          >
            Cancel
          </Button>
        )}
      </Stack>
      {error ? (
        <Typography role="alert" sx={{ typography: "s3", color: "error.main" }}>
          {error}
        </Typography>
      ) : (
        <Typography sx={{ typography: "s3", color: "text.tertiary" }}>
          The key, or a path in your secret manager — vault://, aws-sm://,
          gcp-sm://, azure-kv://, op:// or doppler://. Stored encrypted and used
          only by Future AGI — never shown again, never sent to the improver.
        </Typography>
      )}
    </Stack>
  );
}
SecretRefEditor.propTypes = {
  initial: PropTypes.string,
  placeholder: PropTypes.string,
  onSave: PropTypes.func.isRequired,
  onCancel: PropTypes.func,
  saveLabel: PropTypes.string,
  autoFocus: PropTypes.bool,
};

/**
 * The access check's line: "Re-checking access…" while it runs, then what
 * the key may do. Rebuilds nothing and mints no version — said once, here,
 * because that is the question people have when they rotate a key.
 */
export function PreflightLine({ check, sx }) {
  const pending = usePreflightPending(check?.at);
  if (!check) return null;
  if (pending) {
    return (
      <Stack direction="row" alignItems="center" spacing={0.75} sx={sx}>
        <Box
          sx={{
            width: 6,
            height: 6,
            borderRadius: "50%",
            bgcolor: "text.tertiary",
            flexShrink: 0,
            animation: "preflightPulse 1.1s ease-in-out infinite",
            "@keyframes preflightPulse": {
              "0%,100%": { opacity: 0.3 },
              "50%": { opacity: 1 },
            },
          }}
        />
        <Typography sx={{ typography: "s3", color: "text.secondary" }}>
          Re-checking access…
        </Typography>
      </Stack>
    );
  }
  return (
    <Stack spacing={0.25} sx={sx}>
      <Stack direction="row" alignItems="center" spacing={0.75}>
        <Iconify
          icon={
            check.ok
              ? "solar:shield-check-linear"
              : "solar:danger-circle-linear"
          }
          width={14}
          sx={{
            color: check.ok ? "text.tertiary" : "error.main",
            flexShrink: 0,
          }}
        />
        <Typography
          sx={{
            typography: "s3",
            color: check.ok ? "text.secondary" : "error.main",
            minWidth: 0,
          }}
        >
          {check.ok ? (
            <>
              Access OK ·{" "}
              <Box
                component="span"
                sx={{ color: "text.primary", fontWeight: 600 }}
              >
                {check.line}
              </Box>
              <Box component="span" sx={{ color: "text.tertiary" }}>
                {" "}
                · nothing rebuilt
              </Box>
            </>
          ) : (
            check.line
          )}
        </Typography>
      </Stack>
      {check.note && (
        <Typography sx={{ typography: "s3", color: "text.tertiary", pl: 2.5 }}>
          {check.note}
        </Typography>
      )}
    </Stack>
  );
}
PreflightLine.propTypes = { check: PropTypes.object, sx: PropTypes.object };

/** Small neutral tag — "moved from a pasted value". */
export function RefTag({ children }) {
  return (
    <Typography
      component="span"
      sx={{
        typography: "s3",
        color: "text.secondary",
        flexShrink: 0,
        px: 0.75,
        py: 0.125,
        borderRadius: "4px",
        bgcolor: "action.selected",
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </Typography>
  );
}
RefTag.propTypes = { children: PropTypes.node };
