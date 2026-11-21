import PropTypes from "prop-types";
import { useLayoutEffect, useRef, useState } from "react";
import { Box, Collapse, Stack, Typography, useMediaQuery } from "@mui/material";

const CLAMP_LINES = 4;
const LINE_PX = 18; // s1 line height

function ClampedValue({ text }) {
  const ref = useRef(null);
  const [overflows, setOverflows] = useState(false);
  const [open, setOpen] = useState(false);
  const reduceMotion = useMediaQuery("(prefers-reduced-motion: reduce)");
  const clampPx = CLAMP_LINES * LINE_PX;

  // Measured before paint, so a long value never shows unclamped first.
  useLayoutEffect(() => {
    setOverflows((ref.current?.offsetHeight || 0) > clampPx + 1);
  }, [text, clampPx]);

  const value = (
    <Typography
      ref={ref}
      component="dd"
      sx={{
        m: 0,
        typography: "s1",
        color: "text.primary",
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
      }}
    >
      {text}
    </Typography>
  );
  if (!overflows) return value;
  return (
    <>
      <Collapse
        in={open}
        collapsedSize={clampPx}
        timeout={reduceMotion ? 0 : "auto"}
      >
        {value}
      </Collapse>
      <Box
        component="button"
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((prev) => !prev)}
        sx={{
          p: 0,
          mt: 0.25,
          border: "none",
          background: "none",
          cursor: "pointer",
          typography: "s3",
          fontWeight: 500,
          color: "text.secondary",
          textDecoration: "underline",
          "&:hover": { color: "text.primary" },
        }}
      >
        {open ? "Show less" : "Show more"}
      </Box>
    </>
  );
}
ClampedValue.propTypes = { text: PropTypes.string };

export default function ConfirmChange({ change, sx }) {
  return (
    <Box sx={sx}>
      <Typography
        sx={{ typography: "s1", fontWeight: 500, color: "text.primary" }}
      >
        Confirm this change: {change.title}
      </Typography>
      <Stack component="dl" spacing={1} sx={{ m: 0, mt: 1 }}>
        {change.fields.map((field) => (
          <Box key={field.key}>
            <Typography
              component="dt"
              sx={{ typography: "s3", color: "text.secondary" }}
            >
              {field.label}
            </Typography>
            <ClampedValue text={field.value} />
          </Box>
        ))}
      </Stack>
    </Box>
  );
}
ConfirmChange.propTypes = {
  change: PropTypes.shape({
    title: PropTypes.string,
    fields: PropTypes.arrayOf(
      PropTypes.shape({
        key: PropTypes.string,
        label: PropTypes.string,
        value: PropTypes.string,
      }),
    ),
  }).isRequired,
  sx: PropTypes.object,
};
