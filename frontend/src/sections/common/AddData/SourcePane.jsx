import React, { useEffect, useRef } from "react";
import PropTypes from "prop-types";
import {
  Box,
  ButtonBase,
  CircularProgress,
  Stack,
  Typography,
} from "@mui/material";
import { alpha, useTheme } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { SOURCES } from "./shapes";

/** One line, ellipsis, full text in a tooltip; a small spinner while it re-counts. */
export const SummaryLine = ({ text, loading, action }) => (
  <Stack
    direction="row"
    spacing={0.75}
    alignItems="center"
    sx={{ minWidth: 0 }}
  >
    <CustomTooltip show={Boolean(text)} title={text} size="small" arrow>
      <Typography
        typography="s3"
        color="text.secondary"
        noWrap
        sx={{ minWidth: 0 }}
        data-testid="add-data-source-summary"
      >
        {text}
      </Typography>
    </CustomTooltip>
    {loading && (
      <CircularProgress size={10} thickness={6} sx={{ flexShrink: 0 }} />
    )}
    {action}
  </Stack>
);
SummaryLine.propTypes = {
  text: PropTypes.string,
  loading: PropTypes.bool,
  action: PropTypes.node,
};

/**
 * Where the data comes from (R2-SPEC §7.3.1, left pane, 260 px): one
 * radio-like row per source with its live count; the selected source shows
 * its one-line filter summary and its controls (`children`).
 */
const SourcePane = ({
  value,
  onChange,
  counts = {},
  summary,
  summaryAction,
  loading,
  children,
}) => {
  const theme = useTheme();
  const selectedRef = useRef(null);
  useEffect(() => {
    // Focus starts on the Source pane.
    const t = setTimeout(() => selectedRef.current?.focus(), 120);
    return () => clearTimeout(t);
  }, []);
  return (
    <Stack spacing={0.5} role="radiogroup" aria-label="Source">
      {SOURCES.map((s) => {
        const selected = s.id === value;
        const count = counts[s.id];
        return (
          <Box key={s.id}>
            <ButtonBase
              ref={selected ? selectedRef : undefined}
              role="radio"
              aria-checked={selected}
              onClick={() => onChange(s.id)}
              sx={{
                width: "100%",
                justifyContent: "flex-start",
                gap: 1,
                px: 1,
                py: 0.75,
                borderRadius: 1,
                textAlign: "left",
                border: "1px solid",
                borderColor: selected ? "primary.main" : "transparent",
                bgcolor: selected
                  ? alpha(theme.palette.primary.main, 0.08)
                  : "transparent",
                "&:hover": {
                  bgcolor: selected
                    ? alpha(theme.palette.primary.main, 0.12)
                    : "action.hover",
                },
                "&.Mui-focusVisible": {
                  outline: `2px solid ${theme.palette.primary.main}`,
                  outlineOffset: 1,
                },
              }}
            >
              <Iconify
                icon={s.icon}
                width={16}
                sx={{
                  color: selected ? "primary.main" : "text.secondary",
                  flexShrink: 0,
                }}
              />
              <Typography
                typography="s2"
                fontWeight={selected ? 600 : 400}
                color="text.primary"
                sx={{ flex: 1 }}
              >
                {s.label}
              </Typography>
              {count != null && (
                <Typography typography="s3" color="text.secondary">
                  {Number(count).toLocaleString("en-US")}
                </Typography>
              )}
            </ButtonBase>
            {selected && (summary || children) && (
              <Stack spacing={0.75} sx={{ pl: 3.5, pr: 0.5, pt: 0.5, pb: 1 }}>
                {summary ? (
                  <SummaryLine
                    text={summary}
                    loading={loading}
                    action={summaryAction}
                  />
                ) : null}
                {children}
              </Stack>
            )}
          </Box>
        );
      })}
    </Stack>
  );
};

SourcePane.propTypes = {
  value: PropTypes.string,
  onChange: PropTypes.func,
  counts: PropTypes.object,
  summary: PropTypes.string,
  summaryAction: PropTypes.node,
  loading: PropTypes.bool,
  children: PropTypes.node,
};

export default SourcePane;
