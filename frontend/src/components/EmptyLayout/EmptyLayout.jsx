import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  IconButton,
  Link,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from "@mui/material";

import Iconify from "src/components/iconify";
import {
  useCopyCode,
  visuallyHidden,
} from "src/components/empty-state/useCopyCode";
import SvgColor from "../svg-color";

// The DS EmptyState has no title-element or icon-tile slots. Keep its first-run
// body at this compatibility boundary so titleComponent and hideIcon affect the
// actual DOM without changing the shared DS component's native h2 default.
// Steps and Quickstart retain the DS implementations (they are private there).
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
  const { codeRef, copy, copied, tooltip, status } = useCopyCode();
  const snippet = snippets[active];
  if (!snippet) return null;
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
        <Tooltip title={tooltip}>
          <IconButton
            size="small"
            onClick={() => copy(snippet.code)}
            aria-label="Copy code"
          >
            <Iconify
              icon={copied ? "solar:check-read-linear" : "solar:copy-linear"}
              width={16}
            />
          </IconButton>
        </Tooltip>
      </Stack>
      <Box
        component="span"
        role="status"
        aria-live="polite"
        sx={visuallyHidden}
      >
        {status}
      </Box>
      <Box
        component="pre"
        ref={codeRef}
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

const EmptyLayout = ({
  title,
  description,
  link,
  linkText,
  action,
  actionDisabledReason,
  icon,
  onLinkClick,
  sx = {},
  hideIcon = false,
  titleComponent = "div",
  quickstart,
  steps,
}) => {
  const docsHref = linkText ? link : undefined;
  return (
    <Stack
      alignItems="center"
      justifyContent="flex-start"
      spacing={3}
      sx={{
        flexGrow: 1,
        width: 1,
        px: 3,
        pt: "min(16vh, 160px)",
        pb: 8,
        textAlign: "center",
        ...sx,
      }}
    >
      <Stack alignItems="center" spacing={1.5} sx={{ maxWidth: 400 }}>
        {!hideIcon && (
          <Box
            sx={{
              width: 40,
              height: 40,
              borderRadius: 1,
              display: "grid",
              placeItems: "center",
              bgcolor: "background.muted",
              color: "text.secondary",
            }}
          >
            {icon ? (
              <SvgColor src={icon} sx={{ width: 20, height: 20 }} />
            ) : (
              <Iconify icon="solar:inbox-linear" width={20} />
            )}
          </Box>
        )}
        <Stack spacing={0.5} alignItems="center">
          <Typography
            component={titleComponent}
            sx={{
              fontSize: 13,
              lineHeight: "20px",
              fontWeight: 600,
              color: "text.primary",
            }}
          >
            {title}
          </Typography>
          {description && (
            <Typography
              sx={{ fontSize: 12, lineHeight: "18px", color: "text.tertiary" }}
            >
              {description}
            </Typography>
          )}
        </Stack>
        {(action || docsHref) && (
          <Stack
            direction="row"
            spacing={1.5}
            alignItems="center"
            sx={{ pt: 0.5 }}
          >
            {action}
            {docsHref && (
              <Link
                href={docsHref}
                target="_blank"
                rel="noopener noreferrer"
                onClick={onLinkClick}
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
                {linkText}
                <Iconify icon="solar:arrow-right-up-linear" width={14} />
              </Link>
            )}
          </Stack>
        )}
        {actionDisabledReason && (
          <Typography
            sx={{ fontSize: 12, lineHeight: "18px", color: "text.secondary" }}
          >
            {actionDisabledReason}
          </Typography>
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
};

EmptyLayout.propTypes = {
  title: PropTypes.string,
  description: PropTypes.node,
  link: PropTypes.string,
  linkText: PropTypes.string,
  icon: PropTypes.string,
  action: PropTypes.node,
  // Why the action is disabled; shown as text under it (see EmptyState).
  actionDisabledReason: PropTypes.node,
  onLinkClick: PropTypes.func,
  sx: PropTypes.object,
  hideIcon: PropTypes.bool,
  // The title's element: a div unless the page needs it as a heading (e.g.
  // "h2" beside sibling h2 sections). Typography is the same either way.
  titleComponent: PropTypes.elementType,
  quickstart: PropTypes.object,
  steps: PropTypes.array,
};

export default EmptyLayout;
