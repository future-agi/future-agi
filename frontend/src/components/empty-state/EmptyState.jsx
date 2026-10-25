import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  Button,
  IconButton,
  Link,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";

import Iconify from "src/components/iconify";

// ----------------------------------------------------------------------
// Design System empty state. One component for every "nothing here" screen.
//
//   variant="first-run"  – nothing exists yet. Rendered in the content area
//     under the page header (the header, tabs and top-right action stay).
//     Icon tile, "No ___ yet", one sentence, one primary action and an
//     optional docs link; optional numbered steps and a code quickstart.
//   variant="no-results" – a search or filter matched nothing. Sits inside
//     the table frame; offers Clear filters.
//
// Copy: sentence case, buttons are verb + noun ("Create alert"), never
// "Get started" / "Start creating".

function Steps({ steps }) {
  return (
    <Stack
      component="ol"
      spacing={1.25}
      sx={{ m: 0, p: 0, listStyle: "none", width: 1, textAlign: "left" }}
    >
      {steps.map((step, i) => (
        <Stack key={i} component="li" direction="row" spacing={1.25}>
          <Box
            sx={{
              width: 20,
              height: 20,
              flexShrink: 0,
              borderRadius: "50%",
              display: "grid",
              placeItems: "center",
              bgcolor: "background.muted",
              color: "text.secondary",
              fontSize: 12,
              lineHeight: "16px",
              fontWeight: 500,
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {i + 1}
          </Box>
          <Box sx={{ minWidth: 0 }}>
            <Typography
              sx={{ fontSize: 13, lineHeight: "20px", fontWeight: 500 }}
            >
              {step.title}
            </Typography>
            {step.description && (
              <Typography
                sx={{
                  fontSize: 13,
                  lineHeight: "20px",
                  color: "text.tertiary",
                }}
              >
                {step.description}
              </Typography>
            )}
          </Box>
        </Stack>
      ))}
    </Stack>
  );
}

Steps.propTypes = { steps: PropTypes.array.isRequired };

function Quickstart({ quickstart }) {
  const { title = "Or send from code", snippets = [], waiting } = quickstart;
  const [active, setActive] = useState(0);
  const [copied, setCopied] = useState(false);
  const snippet = snippets[active];
  if (!snippet) return null;
  const copy = () => {
    navigator.clipboard?.writeText(snippet.code);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <Box
      sx={{
        width: 1,
        maxWidth: 640,
        border: 1,
        borderColor: "divider",
        borderRadius: 1,
        overflow: "hidden",
        textAlign: "left",
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{ px: 1.5, py: 1, borderBottom: 1, borderColor: "divider" }}
      >
        <Typography
          sx={{ fontSize: 13, lineHeight: "20px", fontWeight: 500, mr: "auto" }}
        >
          {title}
        </Typography>
        {snippets.length > 1 && (
          <ToggleButtonGroup
            exclusive
            size="small"
            value={active}
            onChange={(_, v) => v !== null && setActive(v)}
          >
            {snippets.map((s, i) => (
              <ToggleButton key={s.label} value={i}>
                {s.label}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
        )}
        <Tooltip title={copied ? "Copied" : "Copy"}>
          <IconButton size="small" onClick={copy} aria-label="Copy code">
            <Iconify
              icon={copied ? "solar:check-read-linear" : "solar:copy-linear"}
              width={16}
            />
          </IconButton>
        </Tooltip>
      </Stack>
      <Box
        component="pre"
        sx={{
          m: 0,
          px: 1.75,
          py: 1.5,
          bgcolor: "background.muted",
          fontFamily: "monospace",
          fontSize: 12,
          lineHeight: "18px",
          color: "text.primary",
          overflowX: "auto",
          whiteSpace: "pre",
        }}
      >
        {snippet.code}
      </Box>
      {waiting && (
        <Stack
          direction="row"
          alignItems="center"
          spacing={1}
          sx={{ px: 1.5, py: 1, borderTop: 1, borderColor: "divider" }}
        >
          <Box
            sx={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              bgcolor: "status.info.solid",
              animation: "dsPulse 1.6s ease-in-out infinite",
              "@keyframes dsPulse": {
                "0%, 100%": { opacity: 1 },
                "50%": { opacity: 0.3 },
              },
              "@media (prefers-reduced-motion: reduce)": { animation: "none" },
            }}
          />
          <Typography
            sx={{ fontSize: 12, lineHeight: "16px", color: "text.secondary" }}
          >
            {waiting}
          </Typography>
        </Stack>
      )}
    </Box>
  );
}

Quickstart.propTypes = { quickstart: PropTypes.object.isRequired };

export default function EmptyState({
  variant = "first-run",
  icon = "solar:inbox-linear",
  title,
  description,
  action,
  secondaryAction,
  docsHref,
  docsLabel = "Read the docs",
  onDocsClick,
  steps,
  quickstart,
  onClearFilters,
  sx,
}) {
  const compact = variant === "no-results";
  return (
    <Stack
      alignItems="center"
      justifyContent={compact ? "center" : "flex-start"}
      spacing={3}
      sx={{
        flexGrow: 1,
        width: 1,
        px: 3,
        pt: compact ? 6 : "min(16vh, 160px)",
        pb: compact ? 6 : 8,
        textAlign: "center",
        ...sx,
      }}
    >
      <Stack alignItems="center" spacing={1.5} sx={{ maxWidth: 400 }}>
        <Box
          sx={{
            width: compact ? 32 : 40,
            height: compact ? 32 : 40,
            borderRadius: 1,
            display: "grid",
            placeItems: "center",
            bgcolor: "background.muted",
            color: "text.secondary",
          }}
        >
          {typeof icon === "string" ? (
            <Iconify icon={icon} width={compact ? 16 : 20} />
          ) : (
            icon
          )}
        </Box>
        <Stack spacing={0.5} alignItems="center">
          <Typography
            component="h2"
            sx={{
              fontSize: compact ? 13 : 14,
              lineHeight: "20px",
              fontWeight: 600,
              color: "text.primary",
            }}
          >
            {title}
          </Typography>
          {description && (
            <Typography
              sx={{ fontSize: 13, lineHeight: "20px", color: "text.tertiary" }}
            >
              {description}
            </Typography>
          )}
        </Stack>
        {(action || secondaryAction || docsHref || onClearFilters) && (
          <Stack
            direction="row"
            spacing={1.5}
            alignItems="center"
            sx={{ pt: 0.5 }}
          >
            {onClearFilters && (
              <Button size="small" variant="outlined" onClick={onClearFilters}>
                Clear filters
              </Button>
            )}
            {action}
            {secondaryAction}
            {docsHref && (
              <Link
                href={docsHref}
                target="_blank"
                rel="noopener noreferrer"
                onClick={onDocsClick}
                underline="hover"
                sx={{
                  fontSize: 13,
                  lineHeight: "20px",
                  color: "text.secondary",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 0.5,
                }}
              >
                {docsLabel}
                <Iconify icon="solar:arrow-right-up-linear" width={14} />
              </Link>
            )}
          </Stack>
        )}
      </Stack>
      {steps?.length > 0 && (
        <Box sx={{ width: 1, maxWidth: 400 }}>
          <Steps steps={steps} />
        </Box>
      )}
      {quickstart && <Quickstart quickstart={quickstart} />}
    </Stack>
  );
}

EmptyState.propTypes = {
  variant: PropTypes.oneOf(["first-run", "no-results"]),
  /** Iconify name or a 20px node. */
  icon: PropTypes.oneOfType([PropTypes.string, PropTypes.node]),
  title: PropTypes.node.isRequired,
  description: PropTypes.node,
  /** The primary <Button> (verb + noun). */
  action: PropTypes.node,
  secondaryAction: PropTypes.node,
  docsHref: PropTypes.string,
  docsLabel: PropTypes.string,
  onDocsClick: PropTypes.func,
  /** [{ title, description }] — at most 3. */
  steps: PropTypes.array,
  /** { title?, snippets: [{ label, code }], waiting? } */
  quickstart: PropTypes.object,
  onClearFilters: PropTypes.func,
  sx: PropTypes.object,
};
