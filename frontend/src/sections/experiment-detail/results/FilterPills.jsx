import React from "react";
import PropTypes from "prop-types";
import { Button, Chip, Stack, Tooltip, Typography } from "@mui/material";
import Iconify from "src/components/iconify";
import { pillKey, pillLabel } from "src/sections/common/Results/filterPills";

const PILL_ICON = {
  movement: "material-symbols:swap-vert-rounded",
  fails: "material-symbols:cancel-outline",
  score: "material-symbols:tune",
  error: "material-symbols:error-outline",
  flaky: "mdi:dice-multiple-outline",
  tool_error: "mdi:wrench-outline",
  split: "material-symbols:call-split",
  meta: "material-symbols:label-outline",
  human: "mdi:account-outline",
  pref: "mdi:scale-balance",
  pref_disagree: "mdi:scale-unbalanced",
  latency: "mdi:timer-outline",
  cost: "mdi:currency-usd",
  expression: "mdi:code-parentheses",
};

/**
 * Active filter pills (PRODUCT-SPEC §3.10.5): plain-text labels, AND
 * together, each removable; the caption reads `Filtered: 7 of 248 rows`.
 */
const FilterPills = ({
  pills,
  runs,
  metrics,
  baselineId,
  total,
  examplesTotal,
  onRemove,
  onClear,
  onEdit,
}) => {
  if (!pills?.length) return null;
  return (
    <Stack
      direction="row"
      alignItems="center"
      gap={0.75}
      flexWrap="wrap"
      sx={{ minHeight: 28 }}
    >
      {pills.map((pill) => {
        const label = pillLabel(pill, { runs, metrics, baselineId });
        // integration S8: an expression pill shows its full text on hover and
        // reopens the dialog on click.
        const isExpr = pill.type === "expression";
        const chip = (
          <Chip
            key={pillKey(pill)}
            {...(isExpr && onEdit
              ? {
                  onClick: () => onEdit(pill),
                  "data-testid": "expression-pill",
                }
              : {})}
            size="small"
            icon={
              <Iconify
                icon={PILL_ICON[pill.type] ?? "material-symbols:filter-list"}
                width={14}
              />
            }
            label={label}
            onDelete={() => onRemove(pill)}
            deleteIcon={<Iconify icon="mdi:close" width={14} />}
            sx={{
              height: 26,
              borderRadius: 0.75,
              typography: "s2",
              fontWeight: 500,
              bgcolor: (t) => `${t.palette.primary.main}14`,
              color: "primary.dark",
              border: "1px solid",
              borderColor: (t) => `${t.palette.primary.main}33`,
              "& .MuiChip-icon": { color: "primary.main", ml: 0.75 },
              "& .MuiChip-deleteIcon": {
                color: "primary.main",
                "&:hover": { color: "primary.dark" },
              },
            }}
          />
        );
        return isExpr ? (
          <Tooltip
            key={pillKey(pill)}
            title={pill.expr}
            placement="bottom-start"
          >
            {chip}
          </Tooltip>
        ) : (
          chip
        );
      })}
      <Button
        size="small"
        onClick={onClear}
        sx={{ typography: "s2", color: "text.secondary", minWidth: 0 }}
      >
        Clear
      </Button>
      {Number.isFinite(total) && Number.isFinite(examplesTotal) && (
        <Typography
          typography="s2"
          color="text.secondary"
          sx={{ ml: 0.5, fontVariantNumeric: "tabular-nums" }}
        >
          {`Filtered: ${total} of ${examplesTotal} rows`}
        </Typography>
      )}
    </Stack>
  );
};

FilterPills.propTypes = {
  pills: PropTypes.array,
  runs: PropTypes.array,
  metrics: PropTypes.array,
  baselineId: PropTypes.string,
  total: PropTypes.number,
  examplesTotal: PropTypes.number,
  onRemove: PropTypes.func.isRequired,
  onClear: PropTypes.func.isRequired,
  onEdit: PropTypes.func,
};

export default FilterPills;
