import PropTypes from "prop-types";
import { Box, Stack, Tooltip, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import { MONO, QUIET as quiet } from "./trialChanges.constants";

/* One section of the tab: a title, an optional count and an optional
   one-line caption; the body sits under it. */
export function Section({ title, count, caption, hint, children, ...rest }) {
  return (
    <Box {...rest}>
      <Stack
        direction="row"
        alignItems="baseline"
        spacing={0.75}
        sx={{ mb: 0.75 }}
      >
        <Typography sx={{ typography: "s2", fontWeight: 600 }}>
          {title}
        </Typography>
        {count != null && (
          <Typography
            sx={{
              typography: "s3",
              color: "text.secondary",
              fontVariantNumeric: "tabular-nums",
            }}
          >
            {count}
          </Typography>
        )}
        {hint && (
          <Tooltip arrow title={hint}>
            <Box
              component="span"
              aria-label={hint}
              sx={{
                display: "inline-flex",
                color: "text.disabled",
                alignSelf: "center",
              }}
            >
              <Iconify icon="solar:info-circle-linear" width={14} />
            </Box>
          </Tooltip>
        )}
        {caption && (
          <Typography sx={{ ...quiet, color: "text.disabled" }}>
            {caption}
          </Typography>
        )}
      </Stack>
      {children}
    </Box>
  );
}
Section.propTypes = {
  title: PropTypes.node.isRequired,
  count: PropTypes.node,
  caption: PropTypes.node,
  hint: PropTypes.string,
  children: PropTypes.node,
};

/* A bordered table: `cols` is the CSS grid template, `head` the header
   cells, `rows` an array of cell arrays (or { cells, sx, below }). */
export function GridTable({ cols, head, rows, testId }) {
  const cell = { px: 1.25, py: 0.75, minWidth: 0 };
  return (
    <Box
      data-testid={testId}
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        overflow: "hidden",
      }}
    >
      {head && (
        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: cols,
            bgcolor: "background.neutral",
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          {head.map((h, i) => (
            <Typography
              key={i}
              sx={{
                ...cell,
                py: 0.5,
                typography: "s3",
                fontWeight: 500,
                color: "text.secondary",
                textAlign: h?.align || "left",
              }}
            >
              {h?.label ?? h}
            </Typography>
          ))}
        </Box>
      )}
      {rows.map((row, i) => {
        const r = Array.isArray(row) ? { cells: row } : row;
        return (
          <Box
            key={r.key ?? i}
            aria-label={r.label}
            sx={[
              { borderTop: i ? "1px solid" : "none", borderColor: "divider" },
              ...(r.sx ? [r.sx] : []),
            ]}
          >
            <Box sx={{ display: "grid", gridTemplateColumns: cols }}>
              {r.cells.map((c, j) => (
                <Box
                  key={j}
                  sx={{
                    ...cell,
                    textAlign: head?.[j]?.align || "left",
                  }}
                >
                  {c}
                </Box>
              ))}
            </Box>
            {r.below && <Box sx={{ ...cell, pt: 0 }}>{r.below}</Box>}
          </Box>
        );
      })}
    </Box>
  );
}
GridTable.propTypes = {
  cols: PropTypes.string.isRequired,
  head: PropTypes.array,
  rows: PropTypes.array.isRequired,
  testId: PropTypes.string,
};

/* A tool or field name, in mono. `tone`: committed (solid), read (muted),
   warning or error. */
export function ActionChip({ name, tone = "committed" }) {
  const color = {
    committed: "text.primary",
    read: "text.secondary",
    warning: "warning.dark",
    error: "error.main",
  }[tone];
  return (
    <Box
      component="span"
      sx={(theme) => ({
        display: "inline-block",
        px: 0.75,
        py: 0.125,
        borderRadius: "4px",
        border: "1px solid",
        borderColor:
          tone === "warning"
            ? alpha(theme.palette.warning.main, 0.4)
            : tone === "error"
              ? alpha(theme.palette.error.main, 0.4)
              : "divider",
        bgcolor: tone === "read" ? "transparent" : "background.paper",
        typography: "s3",
        fontFamily: MONO,
        color,
        lineHeight: 1.6,
        wordBreak: "break-all",
      })}
    >
      {name}
    </Box>
  );
}
ActionChip.propTypes = {
  name: PropTypes.string.isRequired,
  tone: PropTypes.oneOf(["committed", "read", "warning", "error"]),
};

/* A tinted note box (no edge stripe): a title and its lines. */
export function Callout({ tone = "warning", title, lines, testId }) {
  return (
    <Box
      data-testid={testId}
      sx={(theme) => ({
        px: 1.5,
        py: 1,
        borderRadius: 1,
        border: "1px solid",
        borderColor: alpha(theme.palette[tone].main, 0.3),
        bgcolor: alpha(theme.palette[tone].main, 0.06),
      })}
    >
      <Stack direction="row" spacing={0.75} alignItems="center">
        <Iconify
          icon={
            tone === "error"
              ? "solar:danger-circle-linear"
              : "solar:info-circle-linear"
          }
          width={16}
          sx={{ color: `${tone}.dark`, flexShrink: 0 }}
        />
        <Typography
          sx={{ typography: "s2", fontWeight: 600, color: `${tone}.dark` }}
        >
          {title}
        </Typography>
      </Stack>
      {lines.length > 0 && (
        <Box
          component="ul"
          sx={{ m: 0, mt: 0.5, pl: 3.25, "& li": { mt: 0.25 } }}
        >
          {lines.map((line, i) => (
            <Typography
              component="li"
              key={i}
              sx={{ ...quiet, color: "text.primary" }}
            >
              {line}
            </Typography>
          ))}
        </Box>
      )}
    </Box>
  );
}
Callout.propTypes = {
  tone: PropTypes.oneOf(["warning", "error", "info"]),
  title: PropTypes.node.isRequired,
  lines: PropTypes.arrayOf(PropTypes.node).isRequired,
  testId: PropTypes.string,
};

/* A count in a table cell: zero reads quiet, a non-zero count in its tone. */
export function Count({ value, tone }) {
  const zero = !value;
  return (
    <Typography
      sx={{
        typography: "s2",
        fontVariantNumeric: "tabular-nums",
        fontWeight: zero ? 400 : 500,
        color: zero ? "text.disabled" : tone ? `${tone}` : "text.primary",
      }}
    >
      {value ?? 0}
    </Typography>
  );
}
Count.propTypes = {
  value: PropTypes.number,
  tone: PropTypes.string,
};
