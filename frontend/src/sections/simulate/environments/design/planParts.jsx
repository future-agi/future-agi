import PropTypes from "prop-types";
import { Children, isValidElement } from "react";
import { Box, Stack, Typography } from "@mui/material";
import ReadingLine from "./ReadingLine";

const hairline = { borderBottom: "1px solid", borderColor: "divider" };

/* One part of the design: a heading with its count, then its lines on
   hairlines (a Collapse's lines too). While the files are read it shows a
   reading line instead. */
export function PlanRow({ label, count, reading, children }) {
  const n = reading
    ? ""
    : count ??
      Children.toArray(children).filter(
        (c) => isValidElement(c) && !c.props.empty,
      ).length;
  return (
    <Box>
      <Typography sx={{ typography: "m3", fontWeight: 500, mb: 0.5 }}>
        {label}
        <Box
          component="span"
          sx={{
            color: "text.tertiary",
            fontWeight: 400,
            ml: 0.75,
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {n}
        </Box>
      </Typography>
      <Box
        sx={{
          borderTop: "1px solid",
          borderColor: "divider",
          "& > *, & > .MuiCollapse-root .MuiCollapse-wrapperInner > *":
            hairline,
          "& > .MuiCollapse-root": { borderBottom: 0 },
        }}
      >
        {reading ? <ReadingLine text={`Reading ${reading}…`} /> : children}
      </Box>
    </Box>
  );
}
PlanRow.propTypes = {
  label: PropTypes.string.isRequired,
  // A number, or words like "2 of 4".
  count: PropTypes.oneOfType([PropTypes.number, PropTypes.string]),
  // The files still being read, named; empty once read.
  reading: PropTypes.string,
  children: PropTypes.node,
};

/* One line of a part, with where it came from at the end. */
export function PlanLine({ children, src, empty = false }) {
  return (
    <Stack
      direction="row"
      alignItems="center"
      spacing={0.5}
      sx={{ px: 1.5, minHeight: 36, "&:hover": { bgcolor: "action.hover" } }}
    >
      <Typography
        component="div"
        noWrap={!empty}
        sx={{
          typography: "s1",
          color: empty ? "text.tertiary" : "text.primary",
          flex: 1,
          minWidth: 0,
        }}
      >
        {children}
      </Typography>
      {src && (
        <Typography
          noWrap
          sx={{
            typography: "s3",
            color: "text.tertiary",
            flexShrink: 0,
            pl: 1.5,
          }}
        >
          {src}
        </Typography>
      )}
    </Stack>
  );
}
PlanLine.propTypes = {
  children: PropTypes.node,
  src: PropTypes.string,
  empty: PropTypes.bool,
};

export const Quiet = ({ children }) => (
  <Box component="span" sx={{ color: "text.secondary", ml: 0.75 }}>
    {children}
  </Box>
);
Quiet.propTypes = { children: PropTypes.node };
