import PropTypes from "prop-types";
import {
  Box,
  Button,
  Collapse,
  Typography,
  alpha,
  useTheme,
} from "@mui/material";

// "Select all matching filter" opt-in banner. Appears above the
// grid when the user ticks the header "Select all" checkbox and the grid
// enters ag-grid's inverted-selection mode. Clicking the link hides the
// banner and flips the parent into filter-mode selection, which lets bulk
// actions target the full filtered set server-side via the Phase 2
// annotation-queue `selection` payload.
export default function SelectAllBanner({
  visible,
  visibleCount,
  totalMatching,
  totalMatchingIsLowerBound = false,
  noun = "trace",
  onSelectAll,
}) {
  const theme = useTheme();
  const pluralize = (n, base) => (n === 1 ? base : `${base}s`);
  return (
    <Collapse in={visible} unmountOnExit>
      <Box
        role="status"
        sx={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexWrap: "wrap",
          gap: 1,
          py: 1,
          px: 2,
          bgcolor: alpha(theme.palette.primary.main, 0.06),
          borderBottom: `1px solid ${theme.palette.divider}`,
        }}
      >
        <Typography variant="body2">
          {visibleCount} {pluralize(visibleCount, noun)} on this page selected.
        </Typography>
        <Button
          size="small"
          variant="text"
          color="primary"
          onClick={onSelectAll}
          sx={{ textTransform: "none", fontWeight: 600 }}
        >
          {totalMatchingIsLowerBound
            ? `Select all matching ${pluralize(2, noun)} (≥${totalMatching.toLocaleString()})`
            : `Select all ${totalMatching.toLocaleString()} ${pluralize(totalMatching, noun)} matching your filter`}
        </Button>
      </Box>
    </Collapse>
  );
}

SelectAllBanner.propTypes = {
  visible: PropTypes.bool.isRequired,
  visibleCount: PropTypes.number.isRequired,
  totalMatching: PropTypes.number.isRequired,
  totalMatchingIsLowerBound: PropTypes.bool,
  noun: PropTypes.string,
  onSelectAll: PropTypes.func.isRequired,
};
