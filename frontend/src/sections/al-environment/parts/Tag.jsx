import PropTypes from "prop-types";
import { Box } from "@mui/material";
import { ALK_MONO } from "../alkTokens";

/**
 * The harness's own chip: a small uppercase mono label on a wash of its meaning's colour.
 * Not a MUI Chip — those are pill-shaped, sans and sentence case, which reads as a filter
 * control rather than a verdict.
 */
const WASH = {
  pass: { color: "success.main", bg: "success.main" },
  fail: { color: "error.main", bg: "error.main" },
  code: { color: "success.main", bg: "success.main" },
  judge: { color: "warning.main", bg: "warning.main" },
  soft: { color: "text.secondary", bg: "text.secondary" },
};

const Tag = ({ kind = "soft", children, title, dim, keepCase }) => {
  const tone = WASH[kind] || WASH.soft;
  return (
    <Box
      component="span"
      title={title}
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
        color: tone.color,
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
  kind: PropTypes.oneOf(["pass", "fail", "code", "judge", "soft"]),
  children: PropTypes.node,
  title: PropTypes.string,
  dim: PropTypes.bool,
  keepCase: PropTypes.bool,
};

export default Tag;
