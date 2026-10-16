import React, { useEffect, useRef, useState } from "react";
import PropTypes from "prop-types";
import { Box, Stack, Typography } from "@mui/material";
import { alpha, darken, useTheme } from "@mui/material/styles";
import { NO_COLOR, answerColor } from "../../results/answerColors";

// Pieces the three charts share: the card they sit in, its header, and the
// colour of an answer.

/**
 * Width of an element, kept current as it resizes (640 where unmeasured).
 * The element may mount after the first render (a chart that waits for its
 * question), so every render checks whether it is the one being watched.
 */
export function useWidth(ref) {
  const [width, setWidth] = useState(0);
  const watched = useRef(null);
  const observer = useRef(null);
  useEffect(() => {
    const element = ref.current;
    if (element === watched.current) return;
    observer.current?.disconnect();
    observer.current = null;
    watched.current = element;
    if (!element) return;
    const measure = () =>
      setWidth(element.getBoundingClientRect().width || 640);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    observer.current = new ResizeObserver(measure);
    observer.current.observe(element);
  });
  useEffect(() => () => observer.current?.disconnect(), []);
  return width;
}

export function VizCard({ title, caption, action, children, label }) {
  return (
    <Box
      component="section"
      aria-label={label || (typeof title === "string" ? title : undefined)}
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        px: 2,
        pt: 1.75,
        pb: 1.5,
        bgcolor: "background.paper",
      }}
    >
      <Stack
        direction="row"
        alignItems="flex-start"
        justifyContent="space-between"
        spacing={2}
        sx={{ mb: 1.5 }}
      >
        <Box sx={{ minWidth: 0 }}>
          <Typography variant="subtitle2" component="h2" sx={{ fontSize: 15 }}>
            {title}
          </Typography>
          {caption && (
            <Typography
              variant="caption"
              color="text.secondary"
              component="p"
              sx={{ m: 0 }}
            >
              {caption}
            </Typography>
          )}
        </Box>
        {action && (
          <Stack
            direction="row"
            spacing={1}
            alignItems="center"
            sx={{ flexShrink: 0 }}
          >
            {action}
          </Stack>
        )}
      </Stack>
      {children}
    </Box>
  );
}

VizCard.propTypes = {
  title: PropTypes.node.isRequired,
  caption: PropTypes.node,
  action: PropTypes.node,
  children: PropTypes.node,
  label: PropTypes.string,
};

/** The colour of `answer` to `question`, from the results' palette. */
export function useAnswerColor() {
  const theme = useTheme();
  return (question, answer) =>
    answerColor(
      {
        answerType: question?.answerType,
        labels: question?.labels || ["Yes", "No"],
      },
      answer,
      theme,
    );
}

/** The deep end of an answer's hue: burnt orange for No, a darker shade else. */
export const deepColor = (hue) =>
  hue === NO_COLOR ? "#C2410C" : darken(hue, 0.3);

/**
 * An answer's hue in five steps for a share from 0% to 100%: three tints,
 * the hue itself, then its deep end.
 */
export const answerRamp = (hue) => [
  alpha(hue, 0.12),
  alpha(hue, 0.35),
  alpha(hue, 0.65),
  hue,
  deepColor(hue),
];

/** A quoted answer: “No”. */
export const quoted = (answer) => `“${answer}”`;

export function Swatch({ color, hollow, size = 10, sx }) {
  return (
    <Box
      aria-hidden
      sx={{
        width: size,
        height: size,
        borderRadius: "2px",
        flexShrink: 0,
        bgcolor: hollow ? "transparent" : color,
        border: hollow ? "1.5px solid" : "none",
        borderColor: color,
        ...sx,
      }}
    />
  );
}

Swatch.propTypes = {
  color: PropTypes.string,
  hollow: PropTypes.bool,
  size: PropTypes.number,
  sx: PropTypes.object,
};
