import React, { useRef } from "react";
import PropTypes from "prop-types";
import Box from "@mui/material/Box";
import ButtonBase from "@mui/material/ButtonBase";
import Typography from "@mui/material/Typography";
import { alpha } from "@mui/material/styles";

import { templateReason } from "../lib/offerCopy";

const STEP = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };

/**
 * Curated templates as a radio group of cards (name and one line), and under
 * them what the selected one contains: EE's own list, its suggested size, the
 * pinned image and the result of the image's vulnerability scan. A template
 * this workspace cannot use stays visible with the reason.
 *
 * Keyboard: one tab stop (the chosen template, else the first offered one);
 * the arrow keys choose the next or previous offered template and move focus
 * with the choice, as a native radio group does.
 */
export default function TemplateGallery({ templates, value, sizes, onChange }) {
  const cards = useRef({});
  const selected = templates.find((template) => template.id === value) || null;
  const offered = templates.filter((template) => template.available);
  const tabStop = offered.some((template) => template.id === value)
    ? value
    : offered[0]?.id;
  const move = (event, id) => {
    if (!Object.hasOwn(STEP, event.key)) return;
    event.preventDefault();
    const at = offered.findIndex((template) => template.id === id);
    const next =
      offered[(at + STEP[event.key] + offered.length) % offered.length];
    if (!next) return;
    onChange(next.id);
    cards.current[next.id]?.focus();
  };
  return (
    <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
      <Box
        role="radiogroup"
        aria-label="Template"
        data-testid="create-templates"
        sx={{
          display: "grid",
          gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
          gap: 1,
        }}
      >
        {templates.map((template) => {
          const active = template.id === value;
          return (
            <ButtonBase
              key={template.id}
              ref={(element) => {
                cards.current[template.id] = element;
              }}
              role="radio"
              aria-checked={active}
              aria-disabled={!template.available}
              disabled={!template.available}
              tabIndex={template.id === tabStop ? 0 : -1}
              onClick={() => onChange(template.id)}
              onKeyDown={(event) => move(event, template.id)}
              data-testid={`create-template-${template.id}`}
              sx={(theme) => ({
                display: "flex",
                flexDirection: "column",
                alignItems: "flex-start",
                textAlign: "left",
                gap: 0.25,
                p: 1.25,
                borderRadius: 1,
                border: "1px solid",
                borderColor: active ? "primary.main" : "divider",
                bgcolor: active
                  ? alpha(theme.palette.primary.main, 0.06)
                  : "background.paper",
                boxShadow: active
                  ? `inset 0 0 0 1px ${theme.palette.primary.main}`
                  : "none",
                opacity: template.available ? 1 : 0.55,
                "&:hover": template.available
                  ? { borderColor: active ? "primary.main" : "text.disabled" }
                  : undefined,
                "&.Mui-focusVisible": {
                  outline: `2px solid ${theme.palette.primary.main}`,
                  outlineOffset: 2,
                },
              })}
            >
              <Typography
                typography="s1"
                fontWeight="fontWeightMedium"
                color="text.primary"
              >
                {template.name}
              </Typography>
              <Typography
                typography="s3"
                color="text.secondary"
                sx={{ lineHeight: 1.4 }}
              >
                {template.available
                  ? template.description
                  : templateReason(template)}
              </Typography>
            </ButtonBase>
          );
        })}
      </Box>
      {selected && (
        <Box
          data-testid="create-template-contents"
          sx={{
            borderRadius: 1,
            bgcolor: "background.neutral",
            px: 1.5,
            py: 1.25,
            display: "flex",
            flexDirection: "column",
            gap: 0.75,
          }}
        >
          <Typography typography="s2" color="text.secondary">
            What&apos;s installed
          </Typography>
          <Box
            component="ul"
            sx={{
              m: 0,
              pl: 2.25,
              display: "flex",
              flexDirection: "column",
              gap: 0.25,
            }}
          >
            {selected.contents.map((line) => (
              <Typography
                key={line}
                component="li"
                typography="s2"
                color="text.primary"
              >
                {line}
              </Typography>
            ))}
          </Box>
          <Typography
            typography="s3"
            color="text.secondary"
            data-testid="create-template-size-hint"
          >
            {sizeHintLine(selected, sizes)}
            {" · "}
            <Box component="span" sx={{ fontFamily: "monospace" }}>
              {selected.image}
            </Box>
            {selected.scan ? ` · ${scanLine(selected.scan)}` : ""}
          </Typography>
        </Box>
      )}
    </Box>
  );
}

/**
 * "Suggested size: Medium (at least Small)". A suggestion this workspace cannot
 * pick (the host, the deployment's limit or the workspace refuses it) says so,
 * instead of pointing at a card that is disabled a few lines below.
 */
function sizeHintLine(template, sizes) {
  const find = (id) => sizes.find((size) => size.id === id);
  const label = (id) => find(id)?.label || id;
  const hint = find(template.sizeHint);
  const notOffered = hint !== undefined && hint.available === false;
  const minimum =
    template.minSize && template.minSize !== "small"
      ? `at least ${label(template.minSize)}`
      : "";
  if (notOffered) {
    return `Suggested size: ${label(template.sizeHint)}, not offered here${
      minimum ? ` (runs on ${label(template.minSize)} or larger)` : ""
    }`;
  }
  return `Suggested size: ${label(template.sizeHint)}${
    minimum ? ` (${minimum})` : ""
  }`;
}

/**
 * One plain sentence about the image's scan, with every count EE reports: the
 * critical and high findings, and how many of them have a fix (an outside
 * reader must not be able to call "no fixable issues" spin on 74 high ones).
 */
export function scanLine(scan) {
  const fixable = scan.fixableHighOrCritical;
  return `scan: ${scan.critical} critical and ${scan.high} high findings, ${
    fixable === 0 ? "none" : fixable
  } with a fix available`;
}

TemplateGallery.propTypes = {
  templates: PropTypes.arrayOf(
    PropTypes.shape({
      id: PropTypes.string.isRequired,
      name: PropTypes.string.isRequired,
      description: PropTypes.string,
      contents: PropTypes.arrayOf(PropTypes.string),
      sizeHint: PropTypes.string,
      minSize: PropTypes.string,
      image: PropTypes.string,
      available: PropTypes.bool.isRequired,
      reason: PropTypes.string,
      scan: PropTypes.object,
    }),
  ).isRequired,
  value: PropTypes.string,
  sizes: PropTypes.array.isRequired,
  onChange: PropTypes.func.isRequired,
};
