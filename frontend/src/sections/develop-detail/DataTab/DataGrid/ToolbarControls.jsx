import React, { useState } from "react";
import PropTypes from "prop-types";
import {
  Box,
  ButtonBase,
  Checkbox,
  Divider,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Typography,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import Iconify from "src/components/iconify";
import CustomTooltip from "src/components/tooltip";
import { fmtCount } from "src/sections/common/format/fmt";
import { useDataGridView } from "./useDataGridView";

/** The chip's tooltip and menu header (DG#12, RV#41). */
export function failedOnlyCopy({ evals = [], version, on, count }) {
  const n = evals.length;
  if (!n && !on)
    return {
      tooltip: "No pass/fail evaluations are available",
      disabled: true,
    };
  const scope = `${n === 1 ? "the eval" : `at least one of ${n} evals`}${version ? ` in v${version}` : ""}`;
  return {
    // IT-R2 G5 P4: say what "failed" means (their last run), on and off.
    tooltip: on
      ? `Showing rows with a stored failed result (${scope}). Click to show every row.`
      : `Rows with a stored failed result (${scope}). Click to show only them.`,
    header: `Rows failing ${scope}`,
    disabled: false,
  };
}

/**
 * `Failed only · 71 ▾` (G6, DG#12, J2): the chip toggles `system:row_failed`
 * (URL `failed=1`); the caret lists failures per eval (multi-select, URL
 * `fevals=`). One chip, fixed width digits, no vertical shift.
 */
export const FailedOnlyChip = ({
  datasetId,
  count,
  evals,
  version,
  disabled,
}) => {
  const {
    failedOnly: on,
    failedEvalIds,
    setFailedOnly,
    setFailedEvals,
  } = useDataGridView(datasetId);
  const [anchor, setAnchor] = useState(null);
  const list = evals ?? [];
  const copy = failedOnlyCopy({ evals: list, version, on, count });
  const off = !on && (disabled || copy.disabled || !count);
  const narrowed = on && failedEvalIds?.length > 0;
  // A row may fail several selected evaluations; their counts cannot be summed.
  const shown = narrowed ? null : count;
  // IT-R5 G5 P8: off is an outlined neutral toggle; on uses a soft accent
  // fill with a check (never a second contained purple next to Add rows,
  // and the red count stays readable). Red is reserved for the count only.
  const border = on ? "primary.main" : "divider";
  const seg = {
    height: 30,
    typography: "s2",
    color: on ? "primary.dark" : "text.primary",
    bgcolor: (theme) =>
      on ? alpha(theme.palette.primary.main, 0.12) : "transparent",
    "&:hover": {
      bgcolor: (theme) =>
        on
          ? alpha(theme.palette.primary.main, 0.2)
          : theme.palette.action.hover,
    },
    "&.Mui-disabled": { color: "text.disabled" },
  };
  const toggleEval = (id) => {
    const cur = failedEvalIds ?? [];
    setFailedEvals(
      cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id],
    );
  };
  return (
    <Box
      sx={{
        display: "inline-flex",
        border: "1px solid",
        borderColor: border,
        borderRadius: 1,
        overflow: "hidden",
        flexShrink: 0,
      }}
    >
      <CustomTooltip
        show
        title={disabled ? "Add rows first" : copy.tooltip}
        placement="bottom"
        size="small"
      >
        <span>
          <ButtonBase
            disabled={off}
            onClick={() => setFailedOnly(!on)}
            aria-pressed={on}
            sx={{
              ...seg,
              px: 1.25,
              gap: 0.75,
              fontWeight: on ? 600 : 400,
              whiteSpace: "nowrap",
            }}
          >
            {on ? null : (
              <Iconify
                icon="mdi:close-circle-outline"
                width={16}
                sx={{ color: off ? "text.disabled" : "text.secondary" }}
              />
            )}
            Failed only
            {/* No "· 0" while the count loads. */}
            {shown != null && (
              <Box
                component="span"
                sx={{
                  color: "error.main",
                  fontVariantNumeric: "tabular-nums",
                  minWidth: "3ch",
                  textAlign: "left",
                }}
              >
                · {shown}
              </Box>
            )}
            {on ? (
              <Iconify
                icon="mdi:check"
                width={14}
                aria-hidden
                sx={{ ml: -0.25 }}
              />
            ) : null}
          </ButtonBase>
        </span>
      </CustomTooltip>
      {/* The per-eval caret only when there is something to pick: with no
          failures (or nothing scored) the chip is one quiet segment. */}
      {!off && list.length > 0 && (
        <CustomTooltip show size="small" title="Failures by eval">
          <span>
            <ButtonBase
              aria-label="Failures by eval"
              aria-haspopup="menu"
              onClick={(e) => setAnchor(e.currentTarget)}
              sx={{
                ...seg,
                px: 0.5,
                borderLeft: "1px solid",
                borderColor: border,
              }}
            >
              <Iconify icon="mdi:chevron-down" width={16} />
            </ButtonBase>
          </span>
        </CustomTooltip>
      )}
      <Menu
        anchorEl={anchor}
        open={Boolean(anchor)}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        PaperProps={{ sx: { width: 300 } }}
      >
        <Typography
          typography="s3"
          color="text.secondary"
          sx={{ px: 1.5, pt: 0.5, pb: 0.75, display: "block" }}
        >
          {copy.header}
        </Typography>
        <MenuItem
          selected={on && !narrowed}
          onClick={() => {
            setFailedEvals([]);
            setAnchor(null);
          }}
        >
          <ListItemIcon sx={{ minWidth: 28 }}>
            {on && !narrowed && <Iconify icon="mdi:check" width={16} />}
          </ListItemIcon>
          <ListItemText
            primary="Any eval"
            primaryTypographyProps={{ typography: "s2" }}
          />
          <Typography
            typography="s2"
            sx={{ fontVariantNumeric: "tabular-nums" }}
          >
            {count ?? 0}
          </Typography>
        </MenuItem>
        <Divider sx={{ my: 0.5 }} />
        {list.map((e) => (
          <MenuItem
            key={e.column_id}
            onClick={() => toggleEval(e.column_id)}
            disabled={!e.count}
          >
            <ListItemIcon sx={{ minWidth: 28 }}>
              <Checkbox
                size="small"
                checked={Boolean(failedEvalIds?.includes(e.column_id))}
                sx={{ p: 0 }}
                inputProps={{ "aria-label": `Only rows failing ${e.name}` }}
              />
            </ListItemIcon>
            <ListItemText
              primary={e.name}
              secondary={
                e.errors
                  ? `${fmtCount(e.errors, "error")} counted as fails`
                  : null
              }
              primaryTypographyProps={{ typography: "s2", noWrap: true }}
              secondaryTypographyProps={{ typography: "s3" }}
            />
            <Typography
              typography="s2"
              color="error.main"
              sx={{ fontVariantNumeric: "tabular-nums", ml: 1 }}
            >
              {e.count}
            </Typography>
          </MenuItem>
        ))}
      </Menu>
    </Box>
  );
};

FailedOnlyChip.propTypes = {
  datasetId: PropTypes.string.isRequired,
  count: PropTypes.number,
  evals: PropTypes.array,
  version: PropTypes.number,
  disabled: PropTypes.bool,
};
