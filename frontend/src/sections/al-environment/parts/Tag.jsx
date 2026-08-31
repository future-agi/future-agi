import PropTypes from "prop-types";
import { Box } from "@mui/material";
import { ALK_MONO } from "../alkTokens";

/**
 * The harness's own chip: a small uppercase mono label on a wash of its meaning's colour.
 * Not a MUI Chip — those are pill-shaped, sans and sentence case, which reads as a filter
 * control rather than a verdict.
 */
/**
 * `accent` rather than the `.main` ramp: those are tuned for dark backgrounds and for use as
 * fills, so warning.main as text measured 1.19:1 on a light surface — the codebase already
 * says so twice, in CallLogsCellRenderer and ValidationStep. accent carries a value per mode.
 *
 * Blue for a check settled by running code, violet for one the eval harness judged. Not
 * accent.info, which in dark is #7DA9FB against violet's #C4B5FD — both pale and cool, and
 * they read as the same chip; syntax.number is the product's bluest per-mode pair (#1750EB
 * light, #6ba8e6 dark) and stays plainly blue in both.
 *
 * Green and red are left to verdicts, so "settled by code" no longer borrows "passed".
 */
const TONE = {
  pass: (p) => p.accent.pass,
  fail: (p) => p.accent.fail,
  code: (p) => p.syntax.number,
  evalHarness: (p) => p.accent.violet,
  soft: (p) => p.accent.neutral,
};

const Tag = ({ kind = "soft", children, title, dim, keepCase }) => {
  const tone = TONE[kind] || TONE.soft;
  return (
    <Box
      component="span"
      // A chip that gets ellipsized still has to be readable somewhere, and the
      // full text on hover costs nothing when it already fits.
      title={title ?? (typeof children === "string" ? children : undefined)}
      sx={{
        display: "inline-block",
        px: 0.55,
        py: "2px",
        borderRadius: "3px",
        fontFamily: ALK_MONO,
        fontSize: 10.7,
        letterSpacing: "0.08em",
        // Names carry their own case: uppercasing an identifier makes the same thing
        // look like two different things in two places.
        textTransform: keepCase ? "none" : "uppercase",
        whiteSpace: "nowrap",
        maxWidth: "100%",
        overflow: "hidden",
        textOverflow: "ellipsis",
        verticalAlign: "bottom",
        color: (theme) => tone(theme.palette),
        bgcolor: (theme) => theme.palette.action.hover,
        opacity: dim ? 0.45 : 1,
        border: "1px solid",
        borderColor: (theme) => theme.palette.divider,
      }}
    >
      {children}
    </Box>
  );
};

Tag.propTypes = {
  kind: PropTypes.oneOf(["pass", "fail", "code", "evalHarness", "soft"]),
  children: PropTypes.node,
  title: PropTypes.string,
  dim: PropTypes.bool,
  keepCase: PropTypes.bool,
};

export default Tag;
