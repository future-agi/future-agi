import React, { useId, useRef } from "react";
import PropTypes from "prop-types";
import { Box, ButtonBase, Collapse, Link, Stack } from "@mui/material";
import Iconify from "src/components/iconify";
import CopyButton from "../CopyButton";

/**
 * InlineDetail — depth in place, never a mode (ONE-VIEW-SPEC §3.5, P6). The
 * summary is a button (Enter / Space toggles, `aria-expanded`); the detail
 * opens right under it with a 2 px left rule and an 8 px indent; its
 * top-right corner holds `Copy` (when `copyValue`) and `Open in trace ↗`
 * (when `traceHref`). Esc inside the detail collapses it and returns focus
 * to the summary.
 *
 * Props:
 * @param {boolean} props.open
 * @param {Function} props.onToggle
 * @param {React.ReactNode} props.summary  the one-line answer
 * @param {React.ReactNode} props.children  the raw detail (JSON, call…)
 * @param {string|Function} [props.copyValue]
 * @param {string} [props.traceHref]  opens in a new tab
 * @param {string} props.label  what this is (`step 3 · lookup_order`)
 * @param {boolean} [props.chevron=true]  a leading ▸ / ▾
 * @param {object} [props.sx]
 */
export default function InlineDetail({
  open,
  onToggle,
  summary,
  children,
  copyValue,
  traceHref,
  label,
  chevron = true,
  sx,
}) {
  const id = useId();
  const summaryRef = useRef(null);
  return (
    <Box sx={sx}>
      <ButtonBase
        ref={summaryRef}
        aria-expanded={open}
        aria-controls={id}
        aria-label={
          label ? `${open ? "Collapse" : "Expand"} ${label}` : undefined
        }
        onClick={(e) => {
          e.stopPropagation();
          onToggle?.(e);
        }}
        sx={{
          width: "100%",
          justifyContent: "flex-start",
          textAlign: "left",
          gap: 0.5,
          borderRadius: 0.5,
          px: 0.5,
          py: 0.25,
          typography: "s2",
          color: "text.primary",
          "&:hover": { bgcolor: "action.hover" },
          "&:focus-visible": {
            outline: "2px solid",
            outlineColor: "primary.main",
            outlineOffset: -1,
          },
        }}
      >
        {chevron && (
          <Iconify
            icon={open ? "mdi:chevron-down" : "mdi:chevron-right"}
            width={16}
            sx={{ color: "text.secondary", flexShrink: 0 }}
          />
        )}
        <Box sx={{ minWidth: 0, flex: 1 }}>{summary}</Box>
      </ButtonBase>
      <Collapse in={open} unmountOnExit timeout={120}>
        <Box
          id={id}
          role="region"
          aria-label={label}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.stopPropagation();
              e.preventDefault();
              onToggle?.(e);
              summaryRef.current?.focus();
            }
          }}
          sx={{
            position: "relative",
            ml: 1,
            mt: 0.5,
            pl: 1,
            borderLeft: "2px solid",
            borderColor: "divider",
            minWidth: 0,
          }}
        >
          {(copyValue != null || traceHref) && (
            <Stack
              direction="row"
              alignItems="center"
              gap={1}
              sx={{ position: "absolute", top: 0, right: 0, zIndex: 1 }}
            >
              {copyValue != null && (
                <CopyButton value={copyValue} tooltip="Copy" size="small" />
              )}
              {traceHref && (
                <Link
                  href={traceHref}
                  target="_blank"
                  rel="noopener noreferrer"
                  typography="s3"
                  underline="hover"
                  onClick={(e) => e.stopPropagation()}
                >
                  Open in trace ↗
                </Link>
              )}
            </Stack>
          )}
          <Box sx={{ pr: copyValue != null || traceHref ? 14 : 0 }}>
            {children}
          </Box>
        </Box>
      </Collapse>
    </Box>
  );
}

InlineDetail.propTypes = {
  open: PropTypes.bool,
  onToggle: PropTypes.func,
  summary: PropTypes.node,
  children: PropTypes.node,
  copyValue: PropTypes.oneOfType([PropTypes.string, PropTypes.func]),
  traceHref: PropTypes.string,
  label: PropTypes.string,
  chevron: PropTypes.bool,
  sx: PropTypes.object,
};

export { InlineDetail };
