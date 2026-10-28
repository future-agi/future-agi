import PropTypes from "prop-types";
import { Box, Skeleton, Stack, Typography } from "@mui/material";

// Small pieces shared by the agent detail tabs, from the designer prototype.

export function Pill({ children }) {
  return (
    <Box
      sx={{
        px: 0.75,
        height: 20,
        display: "inline-flex",
        alignItems: "center",
        borderRadius: 0.75,
        bgcolor: "action.selected",
        flexShrink: 0,
      }}
    >
      <Typography
        sx={{ typography: "s2", fontWeight: 500, color: "text.secondary" }}
      >
        {children}
      </Typography>
    </Box>
  );
}
Pill.propTypes = { children: PropTypes.node };

export function Card({ title, action, children, sx }) {
  return (
    <Box
      sx={{
        border: "1px solid",
        borderColor: "divider",
        borderRadius: 1,
        bgcolor: "background.paper",
        overflow: "hidden",
        minWidth: 0,
        display: "flex",
        flexDirection: "column",
        ...sx,
      }}
    >
      <Stack
        direction="row"
        alignItems="center"
        spacing={1}
        sx={{
          px: 2,
          py: 1.5,
          borderBottom: "1px solid",
          borderColor: "divider",
        }}
      >
        <Typography
          component="div"
          sx={{ typography: "s2_1", fontWeight: 500, flex: 1 }}
        >
          {title}
        </Typography>
        {action}
      </Stack>
      {/* Its own box, so rows can drop the first top border with :first-of-type. */}
      <Box
        sx={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}
      >
        {children}
      </Box>
    </Box>
  );
}
Card.propTypes = {
  title: PropTypes.node,
  action: PropTypes.node,
  children: PropTypes.node,
  sx: PropTypes.object,
};

export function Row({ cells, header = false, onClick, template }) {
  const clickable = onClick
    ? {
        role: "button",
        tabIndex: 0,
        onClick,
        onKeyDown: (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onClick();
          }
        },
      }
    : {};
  return (
    <Box
      {...clickable}
      sx={{
        display: "grid",
        gridTemplateColumns:
          template ?? `repeat(${cells.length}, minmax(0, 1fr))`,
        gap: 1.5,
        px: 2,
        py: header ? 0.75 : 1,
        alignItems: "center",
        // A header row sits right under its card's title, which already
        // draws that line.
        borderTop: header ? "none" : "1px solid",
        borderColor: "divider",
        cursor: onClick ? "pointer" : "default",
        "&:hover": onClick ? { bgcolor: "action.hover" } : {},
        "&:focus-visible": {
          outline: "2px solid",
          outlineColor: "primary.main",
        },
      }}
    >
      {cells.map((c, i) => (
        <Typography
          // eslint-disable-next-line react/no-array-index-key
          key={i}
          component="div"
          noWrap
          sx={{
            typography: "s2_1",
            fontWeight: header ? 500 : 400,
            color: header ? "text.secondary" : "text.primary",
          }}
        >
          {c}
        </Typography>
      ))}
    </Box>
  );
}
Row.propTypes = {
  cells: PropTypes.array.isRequired,
  header: PropTypes.bool,
  onClick: PropTypes.func,
  template: PropTypes.string,
};

export function QuietTag({ children }) {
  return (
    <Box
      component="span"
      sx={{
        display: "inline-flex",
        alignItems: "center",
        px: 0.75,
        height: 20,
        borderRadius: 0.75,
        flexShrink: 0,
        bgcolor: "action.selected",
        typography: "s2",
        fontWeight: 500,
        lineHeight: 1,
        whiteSpace: "nowrap",
        color: "text.secondary",
      }}
    >
      {children}
    </Box>
  );
}
QuietTag.propTypes = { children: PropTypes.node };

// A text skeleton as tall as the line of text it stands in for. MUI's text
// Skeleton is 1.2em, which is shorter than our line heights, so a loading
// row would be shorter than the row that replaces it.
export function LineSkeleton({ sx, ...rest }) {
  return <Skeleton sx={{ height: "1lh", ...sx }} {...rest} />;
}
LineSkeleton.propTypes = { sx: PropTypes.object };
