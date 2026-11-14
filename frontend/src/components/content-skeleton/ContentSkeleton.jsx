import React from "react";
import PropTypes from "prop-types";
import { Box, Skeleton, Stack } from "@mui/material";
import { ROW_HEIGHT } from "src/theme/sizes";
import Loader from "../loading-screen/loader";

// Design system loading state. A skeleton only where it copies the content:
//   rows  – table or list rows inside a table whose header is already drawn
//   page, panel – a generic page or text block can't copy the real layout,
//           so these render the simple loader instead of grey bars.
function Rows({ count }) {
  return (
    <Stack>
      {Array.from({ length: count }, (_, i) => (
        <Stack
          // eslint-disable-next-line react/no-array-index-key
          key={i}
          direction="row"
          alignItems="center"
          spacing={2}
          sx={{
            height: ROW_HEIGHT.default,
            borderBottom: 1,
            borderColor: "divider",
          }}
        >
          <Skeleton variant="rounded" width="28%" height={12} />
          <Skeleton variant="rounded" width="18%" height={12} />
          <Skeleton variant="rounded" width="14%" height={12} />
          <Skeleton variant="rounded" width="10%" height={12} />
        </Stack>
      ))}
    </Stack>
  );
}
Rows.propTypes = { count: PropTypes.number };

export default function ContentSkeleton({
  variant = "rows",
  rows,
  label = "Loading",
  role = "progressbar",
  sx,
  ...other
}) {
  if (variant !== "rows") {
    return (
      <Loader
        label={label}
        role={role}
        minHeight={variant === "page" ? "60vh" : 96}
        sx={sx}
        {...other}
      />
    );
  }

  // Table rows: grey bars where the rows will land, under the real header.
  return (
    <Box
      role={role}
      aria-live="polite"
      aria-busy="true"
      aria-label={label}
      sx={{ width: 1, ...sx }}
      {...other}
    >
      <Rows count={rows ?? 6} />
    </Box>
  );
}

ContentSkeleton.propTypes = {
  variant: PropTypes.oneOf(["page", "rows", "panel"]),
  rows: PropTypes.number,
  label: PropTypes.string,
  role: PropTypes.string,
  sx: PropTypes.object,
};
