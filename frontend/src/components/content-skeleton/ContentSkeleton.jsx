import React from "react";
import PropTypes from "prop-types";
import { Box, Skeleton, Stack } from "@mui/material";
import { ROW_HEIGHT } from "src/theme/sizes";

// Design system loading state: keep the layout, show grey placeholders where
// the content will land. One shape per kind of screen:
//   page  – title, subtitle, toolbar and table rows (a whole screen)
//   rows  – table or list rows only (inside a table, panel or drawer)
//   panel – a few lines of text (a card, side panel or detail block)
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
  // An indeterminate loading indicator, like the spinner it replaces.
  return (
    <Box
      role={role}
      aria-live="polite"
      aria-busy="true"
      aria-label={label}
      sx={{ width: 1, ...sx }}
      {...other}
    >
      {variant === "page" && (
        <Stack spacing={1.5} sx={{ px: 3, pt: 2 }}>
          <Skeleton variant="rounded" width={180} height={18} />
          <Skeleton variant="rounded" width={360} height={12} />
          <Stack direction="row" spacing={1} sx={{ pt: 1 }}>
            <Skeleton variant="rounded" width={240} height={30} />
            <Skeleton variant="rounded" width={88} height={30} />
          </Stack>
          <Rows count={rows ?? 8} />
        </Stack>
      )}
      {variant === "rows" && <Rows count={rows ?? 6} />}
      {variant === "panel" && (
        <Stack spacing={1} sx={{ p: 1.5 }}>
          <Skeleton variant="rounded" width="40%" height={14} />
          {Array.from({ length: rows ?? 3 }, (_, i) => (
            <Skeleton
              // eslint-disable-next-line react/no-array-index-key
              key={i}
              variant="rounded"
              width={i % 2 ? "70%" : "90%"}
              height={12}
            />
          ))}
        </Stack>
      )}
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
