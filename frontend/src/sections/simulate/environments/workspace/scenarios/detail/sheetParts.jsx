import PropTypes from "prop-types";
import { useState } from "react";
import { Box, Collapse, Stack, Typography } from "@mui/material";
import Iconify from "src/components/iconify";

// The pieces of Vel's scenario drawer sheet: four figures across the top,
// titled ruled parts, one labelled row per fact, and a quiet inline link.

const SECTION_HEAD = {
  typography: "s3",
  fontWeight: 600,
  color: "text.subtitle",
  letterSpacing: 0.5,
  mb: 1,
};

// One figure across the top: what it counts, the count, and a quiet line under it.
export function Figure({ label, value, sub, testId }) {
  const plain = typeof value === "string" || typeof value === "number";
  return (
    <Box data-testid={testId} sx={{ px: 1.75, py: 1.25, minWidth: 0 }}>
      <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
        {label}
      </Typography>
      {plain ? (
        <Typography
          noWrap
          sx={{
            typography: "m3",
            fontWeight: 600,
            fontVariantNumeric: "tabular-nums",
            lineHeight: 1.35,
            mt: 0.25,
          }}
        >
          {value}
        </Typography>
      ) : (
        <Box
          sx={{ mt: 0.5, minHeight: 24, display: "flex", alignItems: "center" }}
        >
          {value}
        </Box>
      )}
      <Box
        sx={{ mt: 0.375, minHeight: 18, display: "flex", alignItems: "center" }}
      >
        {sub && (
          <Typography noWrap sx={{ typography: "s3", color: "text.subtitle" }}>
            {sub}
          </Typography>
        )}
      </Box>
    </Box>
  );
}
Figure.propTypes = {
  label: PropTypes.node,
  value: PropTypes.node,
  sub: PropTypes.node,
  testId: PropTypes.string,
};

// A titled, ruled sheet.
export function Part({ title, children }) {
  return (
    <Box>
      <Typography sx={SECTION_HEAD}>{title}</Typography>
      <Box
        sx={{
          border: "1px solid",
          borderColor: "divider",
          borderRadius: 1,
          "& > .sr + .sr": { borderTop: "1px solid", borderColor: "divider" },
        }}
      >
        {children}
      </Box>
    </Box>
  );
}
Part.propTypes = { title: PropTypes.string, children: PropTypes.node };

// One ruled row of a sheet: the label in a fixed gutter, the value beside it.
export function Row({ label, children }) {
  return (
    <Box
      className="sr"
      sx={{
        display: "grid",
        gridTemplateColumns: { xs: "1fr", sm: "124px minmax(0, 1fr)" },
        columnGap: 2,
        rowGap: 0.25,
        px: 1.75,
        py: 1.125,
      }}
    >
      <Typography
        sx={{ typography: "s2", color: "text.subtitle", lineHeight: 1.6 }}
      >
        {label}
      </Typography>
      <Box minWidth={0}>{children}</Box>
    </Box>
  );
}
Row.propTypes = { label: PropTypes.string, children: PropTypes.node };

// A quiet inline link: "Open conversation", "Details".
export function InlineLink({ onClick, children, ...rest }) {
  return (
    <Box
      component="button"
      type="button"
      onClick={onClick}
      {...rest}
      sx={{
        p: 0,
        border: 0,
        bgcolor: "transparent",
        cursor: "pointer",
        font: "inherit",
        typography: "s2",
        fontWeight: 600,
        color: "text.primary",
        display: "inline-flex",
        alignItems: "center",
        gap: 0.25,
        flexShrink: 0,
        "&:hover": { textDecoration: "underline" },
        "&:disabled": {
          cursor: "default",
          color: "text.disabled",
          textDecoration: "none",
        },
      }}
    >
      {children}
    </Box>
  );
}
InlineLink.propTypes = { onClick: PropTypes.func, children: PropTypes.node };

// A row whose body opens and closes from a Details link; open to start with.
// `summary` sits on the link's line and stays put when the body closes.
export function CollapsibleRow({ label, summary, children }) {
  const [open, setOpen] = useState(true);
  return (
    <Row label={label}>
      <Stack
        direction="row"
        alignItems="center"
        spacing={0.75}
        flexWrap="wrap"
        rowGap={0.25}
      >
        {summary}
        <Box sx={{ flex: 1 }} />
        <InlineLink
          aria-label={`${label} details`}
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
        >
          {open ? "Hide details" : "Details"}
          <Iconify
            icon={
              open ? "solar:alt-arrow-up-linear" : "solar:alt-arrow-down-linear"
            }
            width={12}
          />
        </InlineLink>
      </Stack>
      <Collapse in={open} unmountOnExit>
        <Box sx={{ pt: 0.75 }}>{children}</Box>
      </Collapse>
    </Row>
  );
}
CollapsibleRow.propTypes = {
  label: PropTypes.string.isRequired,
  summary: PropTypes.node,
  children: PropTypes.node,
};
