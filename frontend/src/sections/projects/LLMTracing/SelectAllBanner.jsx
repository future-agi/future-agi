import PropTypes from "prop-types";
import { Alert, Button, Collapse } from "@mui/material";

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
  const pluralize = (n, base) => (n === 1 ? base : `${base}s`);
  return (
    <Collapse in={visible} unmountOnExit>
      <Alert
        severity="info"
        role="status"
        icon={false}
        action={
          <Button
            size="small"
            variant="text"
            color="primary"
            onClick={onSelectAll}
          >
            {totalMatchingIsLowerBound
              ? `Select all matching ${pluralize(2, noun)} (≥${totalMatching.toLocaleString()})`
              : `Select all ${totalMatching.toLocaleString()} ${pluralize(totalMatching, noun)} matching your filter`}
          </Button>
        }
      >
        {visibleCount} {pluralize(visibleCount, noun)} on this page selected.
      </Alert>
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
