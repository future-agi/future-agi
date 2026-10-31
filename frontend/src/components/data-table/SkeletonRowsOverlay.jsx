import PropTypes from "prop-types";
import { Box, Skeleton, Stack } from "@mui/material";

const SKELETON_ROWS = 8;

// DataTable's opt-in loading state: placeholder rows shaped like the table's
// own columns, drawn over whatever rows are on screen so a reload never shows
// the previous results as if they were current.
export default function SkeletonRowsOverlay({ columns = [], rowHeight = 44 }) {
  return (
    <Box
      data-testid="data-table-skeleton"
      sx={{
        // Fills DataGrid's inner overlay box; the outer wrapper is 0px wide.
        width: "100%",
        height: "100%",
        bgcolor: "background.paper",
        overflow: "hidden",
      }}
    >
      {Array.from({ length: SKELETON_ROWS }, (_, row) => (
        <Stack
          key={row}
          direction="row"
          alignItems="center"
          sx={{
            height: rowHeight,
            borderBottom: "1px solid",
            borderColor: "divider",
          }}
        >
          {columns.map((col) => (
            <Box
              key={col.field}
              sx={{
                flex: col.flex
                  ? `${col.flex} 1 0`
                  : `0 0 ${col.width ?? 100}px`,
                minWidth: col.minWidth ?? 0,
                px: 1.25,
              }}
            >
              <Skeleton
                animation="wave"
                variant="text"
                width={col.flex ? "60%" : "70%"}
              />
            </Box>
          ))}
        </Stack>
      ))}
    </Box>
  );
}

SkeletonRowsOverlay.propTypes = {
  columns: PropTypes.array,
  rowHeight: PropTypes.number,
};
