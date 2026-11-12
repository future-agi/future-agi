import React from "react";
import PropTypes from "prop-types";
import { Chip } from "@mui/material";
import Iconify from "src/components/iconify";
import useCustomColumns from "./useCustomColumns";
import CustomTooltip from "src/components/tooltip";
import {
  customSortLabel,
  customSortShortLabel,
  parseCustomSortString,
} from "./resultsCustomColumns";

/**
 * HO results R1 (A-H3): while the grid is sorted by a custom column the
 * toolbar says so (`Sorted by intent_out ↑ · #2B`), and ✕ goes back to the
 * page's default order. Renders nothing for any other sort. `compact` (the
 * narrow toolbar) keeps the arrow icon and `out_len · #2B`, `tight` (the
 * compare toolbar at 1440 px and up, whose row has no room left) the arrow
 * and ✕ alone; the tooltip and the accessible name keep the whole sentence.
 * Props: datasetId, sort, runs (run chips), onClear(), compact, tight.
 */
const CustomSortChip = ({
  datasetId,
  sort,
  runs = [],
  onClear,
  compact = false,
  tight = false,
}) => {
  const parsed = parseCustomSortString(sort);
  const { columns } = useCustomColumns(datasetId, { enabled: !!parsed });
  if (!parsed) return null;
  const def = columns.find((c) => c.id === parsed.colId);
  const label = customSortLabel(parsed, def, runs);
  const chip = (
    <Chip
      size="small"
      data-testid="results-custom-sort"
      aria-label={label}
      icon={
        <Iconify
          icon={
            parsed.dir === "desc"
              ? "material-symbols:arrow-downward-rounded"
              : "material-symbols:arrow-upward-rounded"
          }
          width={14}
        />
      }
      label={compact ? customSortShortLabel(parsed, def, runs) : label}
      onDelete={onClear}
      sx={{
        height: 24,
        borderRadius: 0.5,
        typography: "s2",
        flexShrink: 0,
        ...(tight
          ? {
              "& .MuiChip-label": { display: "none" },
              "& .MuiChip-icon": { ml: 0.75, mr: 0.25 },
              "& .MuiChip-deleteIcon": { ml: 0.25, mr: 0.75 },
            }
          : {}),
      }}
    />
  );
  return compact || tight ? (
    <CustomTooltip show size="small" placement="top" arrow title={label}>
      {chip}
    </CustomTooltip>
  ) : (
    chip
  );
};

CustomSortChip.propTypes = {
  datasetId: PropTypes.string,
  sort: PropTypes.string,
  runs: PropTypes.array,
  onClear: PropTypes.func,
  compact: PropTypes.bool,
  tight: PropTypes.bool,
};

export default CustomSortChip;
